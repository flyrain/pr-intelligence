from __future__ import annotations

import hashlib
import hmac
import json
import os
import queue as queue_module
import threading
from datetime import datetime, timezone
from typing import TYPE_CHECKING
from uuid import uuid4

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import HTMLResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles

from polaris_pr_intel.agents.derived_analysis import DerivedAnalysisAgent
from polaris_pr_intel.agents.issue_insight import IssueInsightAgent
from polaris_pr_intel.agents.review_need import ReviewNeedAgent
from polaris_pr_intel.api.ui import (
    build_resume_command,
    render_dashboard_page,
    render_deep_review_entry,
    render_deep_review_finding,
    render_folded_issue_html,
    render_folded_new_updated_html,
    render_folded_review_html,
    render_issue_row,
    render_latest_pr_review_page,
    render_new_updated_row,
    render_review_job_row,
    render_review_row,
)
from polaris_pr_intel.config import Settings
from polaris_pr_intel.graphs.daily_report_graph import DailyReportGraph
from polaris_pr_intel.graphs.event_graph import EventGraph
from polaris_pr_intel.graphs.pr_review_graph import PRReviewGraph
from polaris_pr_intel.ingest import SnapshotIngestor
from polaris_pr_intel.models import AnalysisItem, AnalysisRun, GitHubEvent, QueueItem
from polaris_pr_intel.refresh import run_full_refresh
from polaris_pr_intel.scheduler.periodic import next_periodic_refresh_at
from polaris_pr_intel.store.base import Repository
from polaris_pr_intel.time_utils import activity_timezone_label, format_activity_time, is_same_activity_day

if TYPE_CHECKING:
    from polaris_pr_intel.scheduler.periodic import PeriodicRefreshScheduler


def create_app(
    repo: Repository,
    event_graph: EventGraph,
    daily_graph: DailyReportGraph,
    pr_review_graph: PRReviewGraph,
    snapshot_ingestor: SnapshotIngestor,
    settings: Settings | None = None,
    webhook_secret: str = "",
    scheduler: "PeriodicRefreshScheduler | None" = None,
    llm_provider: str = "",
    llm_model: str = "",
) -> FastAPI:
    app = FastAPI(title="Polaris PR Intelligence")

    project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
    docs_path = os.path.join(project_root, "docs")
    if os.path.exists(docs_path):
        app.mount("/docs-static", StaticFiles(directory=docs_path), name="docs-static")

    review_jobs: dict[str, dict] = {}
    review_jobs_lock = threading.Lock()
    review_job_queue: queue_module.Queue[str] = queue_module.Queue()
    review_job_workers = max(1, int(os.getenv("REVIEW_JOB_WORKERS", "1")))
    review_job_timeout_sec = int(os.getenv("REVIEW_JOB_TIMEOUT_SEC", "1200"))
    app_settings = settings or Settings(github_token="")
    if llm_provider:
        configured_llm_display = f"{llm_provider} / {llm_model}" if llm_model else llm_provider
    else:
        configured_llm_display = (
            f"{app_settings.llm_provider} / {app_settings.llm_model}"
            if app_settings.llm_model
            else app_settings.llm_provider
        )
    review_target_login = app_settings.review_target_login.strip().lower()
    resume_cwd = app_settings.git_repo_path.strip()
    review_need_agent = getattr(event_graph, "review_need", ReviewNeedAgent(app_settings))
    issue_insight_agent = getattr(event_graph, "issue_insight", IssueInsightAgent(app_settings))

    def _expire_stuck_jobs() -> None:
        now = datetime.now(timezone.utc)
        with review_jobs_lock:
            for job in review_jobs.values():
                if job.get("status") != "running":
                    continue
                started_at = str(job.get("started_at") or "").strip()
                if not started_at:
                    continue
                try:
                    started = datetime.fromisoformat(started_at)
                except ValueError:
                    continue
                elapsed = (now - started).total_seconds()
                if elapsed > review_job_timeout_sec:
                    job["status"] = "failed"
                    job["finished_at"] = now.isoformat()
                    job["result"] = {
                        "ok": False,
                        "errors": [f"job-timeout:{review_job_timeout_sec}s"],
                        "report": None,
                    }

    def _latest_report_markdown() -> str | None:
        run = repo.latest_analysis_run()
        if run is None:
            return None
        return DerivedAnalysisAgent.render_markdown(run)

    def _report_payload(run: AnalysisRun | None) -> dict | None:
        if run is None:
            return None
        return {
            "date": run.created_at.strftime("%Y-%m-%d"),
            "markdown": DerivedAnalysisAgent.render_markdown(run),
        }

    def _review_queue_items() -> list[QueueItem]:
        analysis_run = repo.latest_analysis_run()
        if analysis_run is not None:
            if analysis_run.attention_decisions:
                items = []
                prs = repo.prs
                for decision in analysis_run.attention_decisions:
                    if not decision.needs_review:
                        continue
                    pr = prs.get(decision.pr_number)
                    if not pr or pr.state != "open":
                        continue
                    reasons = [decision.priority_reason, *decision.tags]
                    items.append(
                        QueueItem(
                            number=pr.number,
                            title=pr.title,
                            score=decision.priority_score,
                            reasons=[reason for reason in reasons if reason],
                            url=pr.html_url,
                        )
                    )
                if items:
                    return items
        return []

    def _execute_review(pr_number: int) -> dict:
        if pr_number not in repo.prs:
            fetched = snapshot_ingestor.sync_pr(pr_number)
            if not fetched:
                return {"ok": False, "errors": [f"pr-not-found:{pr_number}"], "report": None}
        try:
            out = pr_review_graph.invoke(pr_number)
        except Exception as exc:
            return {"ok": False, "notifications": [], "errors": [str(exc)], "report": None}
        report = repo.latest_pr_review_report(pr_number)
        return {
            "ok": True,
            "notifications": out.get("notifications", []),
            "errors": out.get("errors", []),
            "report": report.model_dump() if report else None,
        }

    def _review_worker_loop() -> None:
        while True:
            job_id = review_job_queue.get()
            with review_jobs_lock:
                job = review_jobs.get(job_id)
                if not job:
                    review_job_queue.task_done()
                    continue
                job["status"] = "running"
                job["started_at"] = datetime.now(timezone.utc).isoformat()
                pr_number = int(job["pr_number"])
            try:
                result = _execute_review(pr_number)
                status = "completed" if result.get("ok") else "failed"
            except Exception as exc:  # defensive path to avoid untracked crashes
                result = {"ok": False, "errors": [str(exc)], "report": None}
                status = "failed"

            with review_jobs_lock:
                job = review_jobs.get(job_id)
                if job:
                    job["status"] = status
                    job["finished_at"] = datetime.now(timezone.utc).isoformat()
                    job["result"] = result
            review_job_queue.task_done()

    for _ in range(review_job_workers):
        threading.Thread(target=_review_worker_loop, daemon=True).start()

    def _stats() -> dict:
        needs_review_count = len(_review_queue_items())
        interesting_issue_count = sum(1 for s in repo.issue_signals.values() if s.interesting)
        latest_run = repo.latest_analysis_run()
        return {
            "prs_tracked": len(repo.prs),
            "issues_tracked": len(repo.issues),
            "review_signals": len(repo.review_signals),
            "issue_signals": len(repo.issue_signals),
            "deep_pr_reviews": len(repo.pr_review_reports),
            "analysis_runs": len(repo.analysis_runs),
            "needs_review_queue": needs_review_count,
            "interesting_issues_queue": interesting_issue_count,
            "latest_report_date": latest_run.created_at.strftime("%Y-%m-%d") if latest_run else None,
            "last_sync_at": repo.last_sync_at.isoformat() if repo.last_sync_at else None,
        }

    def _refresh_status(now: datetime | None = None) -> dict:
        now_utc = now or datetime.now(timezone.utc)
        next_refresh_at: datetime | None = None
        periodic_refresh_enabled = bool(app_settings.enable_periodic_refresh)
        if scheduler is not None:
            refresh_jobs = [
                job for job in scheduler.scheduler.get_jobs() if job.id.startswith("periodic-refresh-")
            ]
            run_times = [job.next_run_time for job in refresh_jobs if job is not None and job.next_run_time is not None]
            if run_times:
                next_refresh_at = min(run_times)
            if periodic_refresh_enabled and (next_refresh_at is None or next_refresh_at <= now_utc):
                next_refresh_at = next_periodic_refresh_at(
                    now_utc,
                    app_settings.refresh_timezone,
                    app_settings.refresh_interval_minutes,
                    app_settings.refresh_start_hour_local,
                    app_settings.refresh_end_hour_local,
                )
        seconds_until_next_refresh: int | None = None
        if next_refresh_at is not None:
            seconds_until_next_refresh = max(0, int((next_refresh_at - now_utc).total_seconds()))
        return {
            "enabled": periodic_refresh_enabled,
            "last_sync_at": repo.last_sync_at.isoformat() if repo.last_sync_at else None,
            "scheduled_refresh_attempted_at": (
                repo.scheduled_refresh_attempted_at.isoformat() if repo.scheduled_refresh_attempted_at else None
            ),
            "scheduled_refresh_succeeded_at": (
                repo.scheduled_refresh_succeeded_at.isoformat() if repo.scheduled_refresh_succeeded_at else None
            ),
            "scheduled_refresh_failed_at": (
                repo.scheduled_refresh_failed_at.isoformat() if repo.scheduled_refresh_failed_at else None
            ),
            "scheduled_refresh_last_error": repo.scheduled_refresh_last_error,
            "next_refresh_at": next_refresh_at.isoformat() if next_refresh_at else None,
            "seconds_until_next_refresh": seconds_until_next_refresh,
            "refresh_interval_minutes": app_settings.refresh_interval_minutes,
            "refresh_start_hour_local": app_settings.refresh_start_hour_local,
            "refresh_end_hour_local": app_settings.refresh_end_hour_local,
        }

    @app.get("/")
    def index() -> dict:
        return {
            "service": "Polaris PR Intelligence",
            "status": "ok",
            "stats": _stats(),
            "next_steps": [
                "POST /refresh to sync, score, analyze, and generate reports",
                "POST /reviews/pr/{number}/run to run subagent deep review",
                "GET /queues/needs-review to see prioritized PRs",
                "GET /queues/interesting-issues to see prioritized issues",
                "GET /reports/daily/latest.md to view the latest markdown report",
            ],
            "links": {
                "docs": "/docs",
                "health": "/healthz",
                "stats": "/stats",
                "latest_report": "/reports/daily/latest",
                "pr_review_top": "/reviews/pr/top",
            },
        }

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/ui", response_class=HTMLResponse)
    def dashboard() -> str:
        stats = _stats()
        refresh_status = _refresh_status()
        activity_now = datetime.now(timezone.utc)
        local_tz = activity_now.astimezone().tzinfo
        activity_tz_label = activity_timezone_label(app_settings)
        new_updated_rows = []

        def _fmt_minute_ts(updated_at: datetime) -> str:
            return format_activity_time(updated_at, settings=app_settings)

        def _fmt_local_timestamp(value: str | None) -> str:
            if not value:
                return "N/A"
            dt = datetime.fromisoformat(value)
            local_dt = dt.astimezone(local_tz) if local_tz else dt
            return local_dt.strftime("%Y-%m-%d %H:%M")

        def _fmt_duration(seconds: int | None) -> str:
            if seconds is None:
                return "N/A"
            total_minutes = max(0, int(seconds)) // 60
            hours, minutes = divmod(total_minutes, 60)
            parts: list[str] = []
            if hours:
                parts.append(f"{hours}h")
            parts.append(f"{minutes}m")
            return " ".join(parts)

        new_updated_prs = sorted(
            [
                pr
                for pr in repo.prs.values()
                if pr.state == "open" and is_same_activity_day(pr.updated_at, now=activity_now, settings=app_settings)
            ],
            key=lambda p: p.updated_at,
            reverse=True,
        )
        for pr in new_updated_prs:
            new_updated_rows.append(
                render_new_updated_row(
                    pr_number=pr.number,
                    html_url=pr.html_url,
                    title=pr.title,
                    updated_at_label=_fmt_minute_ts(pr.updated_at),
                )
            )
        visible_new_updated_rows = new_updated_rows[:10]
        folded_new_updated_rows = new_updated_rows[10:]
        folded_new_updated_html = render_folded_new_updated_html(
            folded_new_updated_rows,
            activity_tz_label=activity_tz_label,
        )

        review_rows = []
        for item in _review_queue_items():
            review_rows.append(
                render_review_row(
                    number=item.number,
                    url=item.url,
                    title=item.title,
                    score=item.score,
                    reasons=item.reasons,
                )
            )
        visible_review_rows = review_rows[:10]
        folded_review_rows = review_rows[10:]
        folded_review_html = render_folded_review_html(folded_review_rows)
        issue_rows = []
        issues_snapshot = repo.issues
        for signal in sorted(repo.issue_signals.values(), key=lambda s: s.score, reverse=True):
            issue = issues_snapshot.get(signal.issue_number)
            if not issue or not signal.interesting:
                continue
            issue_rows.append(
                render_issue_row(
                    number=issue.number,
                    html_url=issue.html_url,
                    title=issue.title,
                    score=signal.score,
                    reasons=signal.reasons,
                )
            )
        visible_issue_rows = issue_rows[:10]
        folded_issue_rows = issue_rows[10:]
        folded_issue_html = render_folded_issue_html(folded_issue_rows)
        deep_review_entries = []
        deep_reports = sorted(
            repo.pr_review_reports.values(),
            key=lambda r: r.generated_at if r.generated_at.tzinfo else r.generated_at.replace(tzinfo=timezone.utc),
            reverse=True,
        )
        prs_snapshot = repo.prs
        for report in deep_reports[:20]:
            pr = prs_snapshot.get(report.pr_number)
            if not pr:
                continue
            findings_html = []
            for finding in report.findings:
                findings_html.append(
                    render_deep_review_finding(
                        agent_name=finding.agent_name,
                        focus_area=finding.focus_area,
                        verdict=finding.verdict,
                        score=finding.score,
                        confidence=finding.confidence,
                        summary=finding.summary,
                        recommendations=finding.recommendations,
                    )
                )
            deep_review_entries.append(
                render_deep_review_entry(
                    pr_number=pr.number,
                    html_url=pr.html_url,
                    title=pr.title,
                    overall_priority=report.overall_priority,
                    provider=report.provider,
                    model=report.model,
                    recommendation=report.overall_recommendation,
                    session_ids=report.session_ids,
                    resume_cwd=report.resume_cwd or resume_cwd,
                    resume_branch=report.resume_branch,
                    findings_html="".join(findings_html),
                )
            )
        _expire_stuck_jobs()
        with review_jobs_lock:
            jobs_snapshot = list(review_jobs.values())
        jobs_snapshot.sort(key=lambda j: j.get("created_at") or "", reverse=True)
        job_rows = []
        for job in jobs_snapshot[:20]:
            job_rows.append(
                render_review_job_row(
                    job_id=str(job.get("job_id", "")),
                    pr_number=job.get("pr_number", ""),
                    status=str(job.get("status") or "unknown"),
                    created_at=str(job.get("created_at", "")),
                    finished_at=str(job.get("finished_at")) if job.get("finished_at") else None,
                )
            )
        return render_dashboard_page(
            configured_llm_display=configured_llm_display,
            last_sync_at_label=_fmt_local_timestamp(refresh_status["last_sync_at"]),
            seconds_until_next_refresh=refresh_status["seconds_until_next_refresh"],
            next_refresh_label=_fmt_duration(refresh_status["seconds_until_next_refresh"]),
            stats=stats,
            activity_tz_label=activity_tz_label,
            review_rows_html="".join(visible_review_rows),
            folded_review_html=folded_review_html,
            new_updated_count=len(new_updated_rows),
            new_updated_rows_html="".join(visible_new_updated_rows),
            folded_new_updated_html=folded_new_updated_html,
            issue_rows_html="".join(visible_issue_rows),
            folded_issue_html=folded_issue_html,
            deep_review_count=len(deep_review_entries),
            deep_review_entries_html="".join(deep_review_entries),
            job_rows_html="".join(job_rows),
        )

    @app.get("/stats")
    def stats() -> dict:
        return {"ok": True, "stats": _stats()}

    @app.post("/webhooks/github")
    async def github_webhook(
        request: Request,
        x_github_event: str = Header(default=""),
        x_github_delivery: str = Header(default=""),
        x_hub_signature_256: str = Header(default=""),
    ) -> dict:
        if x_github_delivery and repo.has_processed_event(x_github_delivery):
            return {"ok": True, "duplicate": True, "notifications": ["ignored-duplicate"]}

        body = await request.body()
        if webhook_secret:
            digest = hmac.new(webhook_secret.encode(), body, hashlib.sha256).hexdigest()
            expected = f"sha256={digest}"
            if not hmac.compare_digest(expected, x_hub_signature_256):
                raise HTTPException(status_code=401, detail="invalid signature")

        payload = json.loads(body.decode("utf-8"))
        event = GitHubEvent(event_type=x_github_event, action=payload.get("action"), payload=payload)
        out = event_graph.invoke(event)
        if x_github_delivery:
            repo.mark_processed_event(x_github_delivery)
        return {"ok": True, "notifications": out.get("notifications", [])}

    @app.post("/refresh")
    def refresh_all(
        per_page: int = 100,
        max_pages: int = 20,
        prune_missing_open_prs: bool = True,
    ) -> dict:
        """
        Complete refresh: sync GitHub data → recompute scores → run analysis → generate report.

        This is the recommended endpoint for updating all intelligence data.
        """
        result = run_full_refresh(
            snapshot_ingestor=snapshot_ingestor,
            repo=repo,
            review_need_agent=review_need_agent,
            issue_insight_agent=issue_insight_agent,
            daily_graph=daily_graph,
            per_page=per_page,
            max_pages=max_pages,
            prune_missing_open_prs=prune_missing_open_prs,
        )
        analysis_run = result["analysis_run"]

        return {
            "ok": result["ok"],
            "synced": result["synced"],
            "scored": result["scored"],
            "analysis_run": analysis_run.model_dump() if analysis_run else None,
            "report": _report_payload(analysis_run),
            "notifications": result["notifications"],
        }

    @app.post("/reviews/pr/{pr_number}/run")
    def run_pr_review(pr_number: int, wait: bool = False) -> dict:
        if wait:
            result = _execute_review(pr_number)
            result["mode"] = "sync"
            return result

        with review_jobs_lock:
            existing = [
                j
                for j in review_jobs.values()
                if int(j.get("pr_number", -1)) == pr_number and j.get("status") in {"queued", "running"}
            ]
            if existing:
                latest_existing = sorted(existing, key=lambda j: j.get("created_at") or "", reverse=True)[0]
                existing_job_id = str(latest_existing["job_id"])
                return {
                    "ok": True,
                    "accepted": True,
                    "deduplicated": True,
                    "mode": "async",
                    "job_id": existing_job_id,
                    "status": str(latest_existing.get("status") or "queued"),
                    "status_url": f"/reviews/jobs/{existing_job_id}",
                }

            job_id = str(uuid4())
            now = datetime.now(timezone.utc).isoformat()
            review_jobs[job_id] = {
                "job_id": job_id,
                "pr_number": pr_number,
                "status": "queued",
                "created_at": now,
                "started_at": None,
                "finished_at": None,
                "result": None,
            }

        review_job_queue.put(job_id)
        return {
            "ok": True,
            "accepted": True,
            "deduplicated": False,
            "mode": "async",
            "job_id": job_id,
            "status": "queued",
            "status_url": f"/reviews/jobs/{job_id}",
        }

    @app.get("/reviews/jobs/{job_id}")
    def pr_review_job_status(job_id: str) -> dict:
        _expire_stuck_jobs()
        with review_jobs_lock:
            job = review_jobs.get(job_id)
        if not job:
            return {"ok": False, "error": "job-not-found"}
        return {"ok": True, "job": job}

    @app.get("/reviews/pr/{pr_number}/job")
    def pr_review_latest_job_status(pr_number: int) -> dict:
        _expire_stuck_jobs()
        with review_jobs_lock:
            jobs = [j for j in review_jobs.values() if j.get("pr_number") == pr_number]
        if not jobs:
            return {"ok": False, "error": "job-not-found", "pr_number": pr_number}
        latest = sorted(jobs, key=lambda j: j.get("created_at") or "", reverse=True)[0]
        return {"ok": True, "job": latest}

    @app.post("/reviews/pr/{pr_number}/run-sync")
    def run_pr_review_sync(pr_number: int) -> dict:
        # Alias for clients that prefer explicit synchronous semantics.
        out = _execute_review(pr_number)
        out["mode"] = "sync"
        return out

    @app.get("/reviews/pr/{pr_number}/latest")
    def latest_pr_review(pr_number: int) -> dict:
        report = repo.latest_pr_review_report(pr_number)
        return {"ok": True, "report": report.model_dump() if report else None}

    @app.get("/reviews/pr/{pr_number}/latest.md", response_class=PlainTextResponse)
    def latest_pr_review_markdown(pr_number: int) -> str:
        report = repo.latest_pr_review_report(pr_number)
        if not report:
            return f"# PR #{pr_number} Review\n\nNo review has been generated yet.\n"

        pr = repo.prs.get(pr_number)
        if not pr:
            return f"# PR #{pr_number} Review\n\nPR not found in database.\n"

        # Build header with PR metadata
        lines = [
            f"# PR #{pr.number}: {pr.title}",
            "",
            f"**Author:** @{pr.author} | **State:** {pr.state} | **Draft:** {'Yes' if pr.draft else 'No'}",
            f"**Labels:** {', '.join(pr.labels) if pr.labels else 'None'} | **Reviewers:** {', '.join(pr.requested_reviewers) if pr.requested_reviewers else 'None'}",
            f"**Updated:** {pr.updated_at.strftime('%Y-%m-%d %H:%M:%S UTC')} | **Stats:** {pr.changed_files} files, +{pr.additions}/-{pr.deletions} lines",
            f"**GitHub:** {pr.html_url}",
            "",
            "---",
            "",
            "## Review Analysis",
            "",
            f"**Overall Priority:** {report.overall_priority:.2f}",
            f"**Recommendation:** {report.overall_recommendation}",
            f"**Provider:** {report.provider} ({report.model})",
            f"**Generated:** {report.generated_at.strftime('%Y-%m-%d %H:%M:%S UTC')}",
            "",
            "---",
            "",
        ]

        if report.session_ids:
            session_id = report.session_ids[-1]
            resume_cmd = build_resume_command(
                session_id=session_id,
                provider=report.provider,
                cwd=report.resume_cwd or resume_cwd,
                pr_number=report.pr_number,
                branch=report.resume_branch,
            )
            lines.extend([
                "## Resume Session",
                "",
                f"- `{session_id}`: `{resume_cmd}`",
                "",
                "---",
                "",
            ])

        if report.blocked_reason:
            lines.extend([
                "## Blocked",
                "",
                report.blocked_reason,
                "",
            ])

        if report.findings:
            lines.extend([
                "## Findings",
                "",
            ])
        elif not report.blocked_reason:
            lines.extend([
                "## Findings",
                "",
            ])

        if not report.findings and report.blocked_reason:
            return "\n".join(lines)

        if not report.findings:
            lines.append("No findings.")
        else:
            for i, finding in enumerate(report.findings, 1):
                lines.extend([
                    f"### {i}. {finding.agent_name}: {finding.focus_area}",
                    "",
                    f"**Verdict:** {finding.verdict.upper()} | **Score:** {finding.score:.2f} | **Confidence:** {finding.confidence:.2f}",
                    "",
                    f"#### Summary",
                    finding.summary,
                    "",
                ])

                if finding.recommendations:
                    lines.append("#### Recommendations")
                    for rec in finding.recommendations:
                        lines.append(f"- {rec}")
                    lines.append("")

                if finding.tags:
                    lines.append(f"**Tags:** {', '.join(finding.tags)}")
                    lines.append("")

                lines.append("---")
                lines.append("")

        return "\n".join(lines)

    @app.get("/reviews/pr/{pr_number}/latest.html", response_class=HTMLResponse)
    def latest_pr_review_html(pr_number: int) -> str:
        markdown_content = latest_pr_review_markdown(pr_number)
        return render_latest_pr_review_page(pr_number=pr_number, markdown_content=markdown_content)

    @app.get("/reviews/pr/top")
    def top_pr_reviews(limit: int = 20) -> dict:
        reports = [r.model_dump() for r in repo.top_pr_review_reports(limit=limit)]
        return {"ok": True, "reports": reports}

    @app.get("/reports/daily/latest.md", response_class=PlainTextResponse)
    def latest_report_markdown() -> str:
        markdown = _latest_report_markdown()
        if markdown is None:
            return "# Polaris PR Intelligence Report\n\nNo report has been generated yet.\n"
        return markdown

    @app.get("/queues/needs-review", response_model=list[QueueItem])
    def needs_review() -> list[QueueItem]:
        return _review_queue_items()

    @app.get("/queues/interesting-issues", response_model=list[QueueItem])
    def interesting_issues() -> list[QueueItem]:
        items: list[QueueItem] = []
        for signal in sorted(repo.issue_signals.values(), key=lambda s: s.score, reverse=True):
            if not signal.interesting:
                continue
            issue = repo.issues.get(signal.issue_number)
            if not issue:
                continue
            items.append(
                QueueItem(number=issue.number, title=issue.title, score=signal.score, reasons=signal.reasons, url=issue.html_url)
            )
        return items

    return app
