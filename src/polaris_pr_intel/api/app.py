from __future__ import annotations

import hashlib
import hmac
import json
import os
import queue as queue_module
import threading
from datetime import datetime, timedelta, timezone
from html import escape
from typing import TYPE_CHECKING
from uuid import uuid4

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import HTMLResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles

from polaris_pr_intel.agents.issue_insight import IssueInsightAgent
from polaris_pr_intel.agents.review_need import ReviewNeedAgent
from polaris_pr_intel.config import Settings
from polaris_pr_intel.graphs.daily_report_graph import DailyReportGraph
from polaris_pr_intel.graphs.event_graph import EventGraph
from polaris_pr_intel.graphs.pr_review_graph import PRReviewGraph
from polaris_pr_intel.ingest import SnapshotIngestor
from polaris_pr_intel.models import AnalysisItem, GitHubEvent, QueueItem
from polaris_pr_intel.store.base import Repository

if TYPE_CHECKING:
    from polaris_pr_intel.scheduler.daily import DailyScheduler


def create_app(
    repo: Repository,
    event_graph: EventGraph,
    daily_graph: DailyReportGraph,
    pr_review_graph: PRReviewGraph,
    snapshot_ingestor: SnapshotIngestor,
    settings: Settings | None = None,
    webhook_secret: str = "",
    scheduler: "DailyScheduler | None" = None,
) -> FastAPI:
    app = FastAPI(title="Polaris PR Intelligence")

    # Mount static files for serving images and assets
    import os
    # Go up from api/app.py -> api -> polaris_pr_intel -> src -> project_root
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
    configured_llm_model = (app_settings.llm_model or "").strip()
    configured_llm_display = (
        f"{app_settings.llm_provider} / {configured_llm_model}"
        if configured_llm_model
        else app_settings.llm_provider
    )
    review_target_login = app_settings.review_target_login.strip().lower()
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

    def _report_markdown_to_html(markdown: str) -> str:
        parts: list[str] = []
        in_list = False

        def close_list() -> None:
            nonlocal in_list
            if in_list:
                parts.append("</ul>")
                in_list = False

        for raw_line in markdown.splitlines():
            line = raw_line.strip()
            if not line:
                close_list()
                continue
            if line.startswith("# "):
                close_list()
                parts.append(f"<h1>{escape(line[2:])}</h1>")
                continue
            if line.startswith("## "):
                close_list()
                parts.append(f"<h2>{escape(line[3:])}</h2>")
                continue
            if line.startswith("- "):
                if not in_list:
                    parts.append("<ul>")
                    in_list = True
                item = line[2:]
                if item.startswith("[#") and "](" in item and ")" in item:
                    text_part, rest = item.split("](", 1)
                    number = text_part[1:]
                    url, suffix = rest.split(")", 1)
                    parts.append(
                        f"<li><a href=\"{escape(url)}\" target=\"_blank\" rel=\"noopener noreferrer\">{escape(number)}</a>{escape(suffix)}</li>"
                    )
                else:
                    parts.append(f"<li>{escape(item)}</li>")
                continue
            close_list()
            parts.append(f"<p>{escape(line)}</p>")

        close_list()
        return "\n".join(parts)

    def _is_target_review_pr(pr, signal) -> bool:
        if not review_target_login:
            return True
        if "requested-you" in signal.reasons:
            return True
        return any((r or "").strip().lower() == review_target_login for r in pr.requested_reviewers)

    def _execute_review(pr_number: int) -> dict:
        if pr_number not in repo.prs:
            fetched = snapshot_ingestor.sync_pr(pr_number)
            if not fetched:
                return {"ok": False, "errors": [f"pr-not-found:{pr_number}"], "report": None}
        out = pr_review_graph.invoke(pr_number)
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
        needs_review_count = 0
        for signal in repo.review_signals.values():
            if not signal.needs_review:
                continue
            pr = repo.prs.get(signal.pr_number)
            if not pr:
                continue
            if pr.state != "open":
                continue
            if _is_target_review_pr(pr, signal):
                needs_review_count += 1
        interesting_issue_count = sum(1 for s in repo.issue_signals.values() if s.interesting)
        latest_report = repo.latest_daily_report()
        return {
            "prs_tracked": len(repo.prs),
            "issues_tracked": len(repo.issues),
            "review_signals": len(repo.review_signals),
            "issue_signals": len(repo.issue_signals),
            "deep_pr_reviews": len(repo.pr_review_reports),
            "analysis_runs": len(repo.analysis_runs),
            "needs_review_queue": needs_review_count,
            "interesting_issues_queue": interesting_issue_count,
            "daily_reports": len(repo.daily_reports),
            "latest_report_date": latest_report.date if latest_report else None,
            "last_sync_at": repo.last_sync_at.isoformat() if repo.last_sync_at else None,
        }

    def _refresh_status(now: datetime | None = None) -> dict:
        now_utc = now or datetime.now(timezone.utc)
        next_refresh_at: datetime | None = None
        periodic_refresh_enabled = bool(app_settings.enable_periodic_refresh)
        if scheduler is not None:
            refresh_job = scheduler.scheduler.get_job("periodic-refresh")
            if refresh_job is not None:
                next_refresh_at = refresh_job.next_run_time
        if next_refresh_at is None and periodic_refresh_enabled and repo.last_sync_at:
            next_refresh_at = repo.last_sync_at + timedelta(hours=app_settings.refresh_interval_hours)
        seconds_until_next_refresh: int | None = None
        if next_refresh_at is not None:
            seconds_until_next_refresh = max(0, int((next_refresh_at - now_utc).total_seconds()))
        return {
            "enabled": periodic_refresh_enabled,
            "last_sync_at": repo.last_sync_at.isoformat() if repo.last_sync_at else None,
            "next_refresh_at": next_refresh_at.isoformat() if next_refresh_at else None,
            "seconds_until_next_refresh": seconds_until_next_refresh,
            "refresh_interval_hours": app_settings.refresh_interval_hours,
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
        local_now = datetime.now().astimezone()
        local_today = local_now.date()
        local_tz = local_now.tzinfo
        latest = repo.latest_daily_report()
        latest_report_html = (
            _report_markdown_to_html(latest.markdown)
            if latest
            else "<h2>No Report Yet</h2><p>Run <code>POST /refresh</code> to generate one.</p>"
        )
        new_updated_rows = []
        def _is_updated_today_local(updated_at: datetime) -> bool:
            dt = updated_at if updated_at.tzinfo else updated_at.replace(tzinfo=timezone.utc)
            if local_tz is None:
                return dt.date() == local_today
            return dt.astimezone(local_tz).date() == local_today

        def _fmt_minute_ts(updated_at: datetime) -> str:
            dt = updated_at if updated_at.tzinfo else updated_at.replace(tzinfo=timezone.utc)
            local_dt = dt.astimezone(local_tz) if local_tz else dt
            return local_dt.strftime("%H:%M")

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
            [pr for pr in repo.prs.values() if pr.state == "open" and _is_updated_today_local(pr.updated_at)],
            key=lambda p: p.updated_at,
            reverse=True,
        )
        for pr in new_updated_prs:
            new_updated_rows.append(
                "<tr>"
                f"<td><a href=\"{escape(pr.html_url)}\" target=\"_blank\" rel=\"noopener noreferrer\">#{pr.number}</a></td>"
                f"<td>{escape(pr.title)}</td>"
                f"<td>{escape(_fmt_minute_ts(pr.updated_at))}</td>"
                f"<td><button class=\"action-btn\" onclick=\"runPrReview({pr.number}, this)\">Review</button></td>"
                "</tr>"
            )
        visible_new_updated_rows = new_updated_rows[:10]
        folded_new_updated_rows = new_updated_rows[10:]
        folded_new_updated_html = (
            "<details class=\"folded-section\">"
            f"<summary>Show {len(folded_new_updated_rows)} more PRs</summary>"
            "<table>"
            "<thead><tr><th>PR</th><th>Title</th><th>Updated</th><th>Action</th></tr></thead>"
            f"<tbody>{''.join(folded_new_updated_rows)}</tbody>"
            "</table>"
            "</details>"
            if folded_new_updated_rows
            else ""
        )

        review_rows = []
        for signal in sorted(repo.review_signals.values(), key=lambda s: s.score, reverse=True):
            pr = repo.prs.get(signal.pr_number)
            if not pr or not signal.needs_review:
                continue
            if pr.state != "open":
                continue
            if not _is_target_review_pr(pr, signal):
                continue
            review_rows.append(
                f"<tr><td><a href=\"{escape(pr.html_url)}\" target=\"_blank\" rel=\"noopener noreferrer\">#{pr.number}</a></td>"
                f"<td>{escape(pr.title)}</td><td>{signal.score:.1f}</td><td>{escape(', '.join(signal.reasons))}</td></tr>"
            )
        visible_review_rows = review_rows[:10]
        folded_review_rows = review_rows[10:]
        folded_review_html = (
            "<details class=\"folded-section\">"
            f"<summary>Show {len(folded_review_rows)} more PRs</summary>"
            "<table>"
            "<thead><tr><th>PR</th><th>Title</th><th>Score</th><th>Reasons</th></tr></thead>"
            f"<tbody>{''.join(folded_review_rows)}</tbody>"
            "</table>"
            "</details>"
            if folded_review_rows
            else ""
        )
        issue_rows = []
        for signal in sorted(repo.issue_signals.values(), key=lambda s: s.score, reverse=True):
            issue = repo.issues.get(signal.issue_number)
            if not issue or not signal.interesting:
                continue
            issue_rows.append(
                f"<tr><td><a href=\"{escape(issue.html_url)}\" target=\"_blank\" rel=\"noopener noreferrer\">#{issue.number}</a></td>"
                f"<td>{escape(issue.title)}</td><td>{signal.score:.1f}</td><td>{escape(', '.join(signal.reasons))}</td></tr>"
            )
        visible_issue_rows = issue_rows[:10]
        folded_issue_rows = issue_rows[10:]
        folded_issue_html = (
            "<details class=\"folded-section\">"
            f"<summary>Show {len(folded_issue_rows)} more issues</summary>"
            "<table>"
            "<thead><tr><th>Issue</th><th>Title</th><th>Score</th><th>Reasons</th></tr></thead>"
            f"<tbody>{''.join(folded_issue_rows)}</tbody>"
            "</table>"
            "</details>"
            if folded_issue_rows
            else ""
        )
        deep_review_entries = []
        deep_reports = sorted(
            repo.pr_review_reports.values(),
            key=lambda r: r.generated_at if r.generated_at.tzinfo else r.generated_at.replace(tzinfo=timezone.utc),
            reverse=True,
        )
        for report in deep_reports[:20]:
            pr = repo.prs.get(report.pr_number)
            if not pr:
                continue
            findings_html = []
            for finding in report.findings:
                recs = "".join(f"<li>{escape(rec)}</li>" for rec in finding.recommendations)
                findings_html.append(
                    "<article class=\"finding\">"
                    f"<div><strong>{escape(finding.agent_name)}</strong> · {escape(finding.focus_area)}</div>"
                    f"<div class=\"finding-meta\"><span class=\"verdict {escape(finding.verdict)}\">{escape(finding.verdict.upper())}</span> score={finding.score:.2f} confidence={finding.confidence:.2f}</div>"
                    f"<p>{escape(finding.summary)}</p>"
                    f"<ul>{recs}</ul>"
                    "</article>"
                )
            deep_review_entries.append(
                "<details class=\"review-detail\">"
                "<summary>"
                f"<span><a href=\"{escape(pr.html_url)}\" target=\"_blank\" rel=\"noopener noreferrer\">#{pr.number}</a> · {escape(pr.title)}</span>"
                f"<span>priority={report.overall_priority:.2f} · <a href=\"/reviews/pr/{pr.number}/latest.html\" target=\"_blank\" rel=\"noopener noreferrer\" class=\"view-report-link\">View Report</a></span>"
                "</summary>"
                f"<p class=\"muted\">Provider: {escape(report.provider)} | Model: {escape(report.model)} | Recommendation: {escape(report.overall_recommendation)}</p>"
                f"{''.join(findings_html) if findings_html else '<p>No findings.</p>'}"
                "</details>"
            )
        _expire_stuck_jobs()
        with review_jobs_lock:
            jobs_snapshot = list(review_jobs.values())
        jobs_snapshot.sort(key=lambda j: j.get("created_at") or "", reverse=True)
        job_rows = []
        for job in jobs_snapshot[:20]:
            status = str(job.get("status") or "unknown")
            status_cls = "job-status-" + ("queued" if status == "queued" else "running" if status == "running" else "done")
            job_rows.append(
                "<tr>"
                f"<td><code>{escape(str(job.get('job_id', '')))}</code></td>"
                f"<td>#{escape(str(job.get('pr_number', '')))}</td>"
                f"<td><span class=\"job-status {status_cls}\">{escape(status)}</span></td>"
                f"<td>{escape(str(job.get('created_at', '')))}</td>"
                f"<td>{escape(str(job.get('finished_at', '')) if job.get('finished_at') else '-')}</td>"
                f"<td><a href=\"/reviews/jobs/{escape(str(job.get('job_id', '')))}\" target=\"_blank\" rel=\"noopener noreferrer\">JSON</a></td>"
                "</tr>"
            )

        return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Polaris PR Intelligence</title>
  <style>
    :root {{
      --bg: #0b1220;
      --card: #101a2c;
      --ink: #e6edf7;
      --muted: #93a4bb;
      --line: #22324b;
      --accent: #2dd4bf;
      --accent2: #34d399;
      --good: #86efac;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      color: var(--ink);
      font-family: "IBM Plex Sans", "Segoe UI", sans-serif;
      background:
        radial-gradient(circle at 0% 0%, rgba(45, 212, 191, 0.12) 0%, transparent 35%),
        radial-gradient(circle at 100% 20%, rgba(96, 165, 250, 0.14) 0%, transparent 35%),
        var(--bg);
    }}
    .wrap {{ max-width: 1200px; margin: 0 auto; padding: 24px; }}
    .hero {{
      border: 1px solid var(--line);
      background: linear-gradient(120deg, #0f1b2f 0%, #112138 55%, #0f2438 100%);
      border-radius: 18px;
      padding: 12px 16px;
      box-shadow: 0 14px 34px rgba(0,0,0,0.35);
    }}
    .hero-top {{
      display: grid;
      grid-template-columns: minmax(320px, 360px) minmax(40px, 1fr) fit-content(320px) fit-content(240px) 140px;
      gap: 14px;
      align-items: center;
    }}
    .hero-heading {{
      min-width: 0;
      display: flex;
      align-items: flex-start;
      gap: 0;
    }}
    .brand-copy {{
      min-width: 0;
    }}
    h1,h2,h3 {{ font-family: inherit; font-weight: 700; margin: 0 0 12px 0; }}
    h1 {{ font-size: 22px; margin-bottom: 1px; line-height: 1.02; }}
    h2 {{ font-size: 24px; margin-top: 18px; }}
    .muted {{ color: var(--muted); margin: 0; }}
    .hero-side {{
      min-width: 0;
      display: contents;
    }}
    .btn {{
      border: 1px solid rgba(45, 212, 191, 0.22);
      background: linear-gradient(135deg, #1f948f 0%, #177c80 100%);
      color: #f8fffe;
      padding: 8px 12px;
      border-radius: 12px;
      text-decoration: none;
      font-weight: 700;
      cursor: pointer;
      text-align: center;
      min-height: 40px;
      display: flex;
      align-items: center;
      justify-content: center;
      box-shadow: 0 6px 16px rgba(8, 54, 61, 0.24);
      transition: transform 0.15s ease, filter 0.15s ease, box-shadow 0.15s ease;
      white-space: nowrap;
      width: 140px;
    }}
    .btn:hover {{
      filter: brightness(1.05);
      transform: translateY(-1px);
      box-shadow: 0 8px 18px rgba(8, 54, 61, 0.28);
    }}
    .btn.primary {{
      background: linear-gradient(135deg, #238f9e 0%, #1d7d91 100%);
      border-color: rgba(125, 239, 233, 0.2);
    }}
    .btn.sync-btn {{
      background: linear-gradient(135deg, #1b877f 0%, #176f75 100%);
      border-color: rgba(79, 214, 197, 0.18);
    }}
    .btn.sync-btn.loading {{
      opacity: 0.82;
      cursor: default;
      position: relative;
    }}
    .btn.sync-btn.loading::after {{
      content: "";
      width: 12px;
      height: 12px;
      border-radius: 999px;
      border: 2px solid rgba(255, 255, 255, 0.35);
      border-top-color: #ffffff;
      margin-left: 8px;
      animation: sync-spin 0.8s linear infinite;
    }}
    .btn.sync-btn:disabled {{
      opacity: 0.75;
      cursor: default;
      box-shadow: none;
    }}
    @keyframes sync-spin {{
      to {{ transform: rotate(360deg); }}
    }}
    .grid {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(210px, 1fr));
      gap: 12px;
      margin: 16px 0 22px;
    }}
    .card {{
      background: var(--card);
      border: 1px solid var(--line);
      border-radius: 14px;
      padding: 14px;
    }}
    a {{ color: #7dd3fc; }}
    a:visited {{ color: #a5b4fc; }}
    .k {{ font-size: 12px; text-transform: uppercase; letter-spacing: .08em; color: var(--muted); }}
    .v {{ font-size: 28px; font-weight: 700; margin-top: 4px; }}
    .layout {{
      display: grid;
      grid-template-columns: 1.1fr .9fr;
      gap: 16px;
      align-items: start;
    }}
    .tab-fold > summary {{
      cursor: pointer;
      font-family: inherit;
      font-size: 24px;
      font-weight: 700;
      margin-bottom: 10px;
    }}
    .tab-fold[open] > summary {{
      margin-bottom: 12px;
    }}
    table {{ width: 100%; border-collapse: collapse; font-size: 14px; }}
    th, td {{ border-bottom: 1px solid var(--line); padding: 8px 6px; text-align: left; vertical-align: top; }}
    th {{ color: var(--muted); font-weight: 600; }}
    .report h1 {{ font-size: 28px; }}
    .report h2 {{ font-size: 20px; }}
    .report ul {{ margin: 0 0 8px 20px; padding: 0; }}
    .report li {{ margin-bottom: 6px; }}
    .review-detail {{
      border: 1px solid var(--line);
      border-radius: 10px;
      background: #0d1728;
      padding: 10px 12px;
      margin-bottom: 10px;
    }}
    .review-detail summary {{
      cursor: pointer;
      display: flex;
      justify-content: space-between;
      gap: 10px;
      flex-wrap: wrap;
      font-weight: 600;
      color: var(--accent2);
    }}
    .view-report-link {{
      color: var(--accent);
      text-decoration: none;
      font-size: 13px;
      padding: 2px 8px;
      border: 1px solid var(--accent);
      border-radius: 4px;
      transition: background 0.2s;
    }}
    .view-report-link:hover {{
      background: var(--accent);
      color: white;
    }}
    .finding {{
      margin-top: 10px;
      border-top: 1px dashed var(--line);
      padding-top: 10px;
    }}
    .finding-meta {{ color: var(--muted); font-size: 13px; margin: 4px 0; }}
    .verdict {{
      display: inline-block;
      border-radius: 999px;
      padding: 1px 8px;
      font-size: 11px;
      letter-spacing: .04em;
      border: 1px solid var(--line);
      margin-right: 6px;
    }}
    .verdict.high {{ background: #3a1f27; border-color: #ad4b64; }}
    .verdict.medium {{ background: #3b2f1d; border-color: #b88a37; }}
    .verdict.low {{ background: #183227; border-color: #3f9f70; }}
    .job-status {{
      display: inline-block;
      border-radius: 999px;
      padding: 2px 8px;
      font-size: 11px;
      border: 1px solid var(--line);
      text-transform: uppercase;
      letter-spacing: .04em;
    }}
    .job-status-queued {{ background: #1b2638; border-color: #485d7a; }}
    .job-status-running {{ background: #0f3042; border-color: #2779a7; }}
    .job-status-done {{ background: #173628; border-color: #3d8a62; }}
    .action-btn {{
      border: 1px solid var(--line);
      border-radius: 8px;
      background: var(--accent2);
      color: #fff;
      padding: 4px 10px;
      font-size: 12px;
      cursor: pointer;
    }}
    .action-btn[disabled] {{
      opacity: 0.65;
      cursor: default;
    }}
    .folded-section {{
      margin-top: 10px;
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 8px;
      background: #0d1728;
    }}
    .folded-section > summary {{
      cursor: pointer;
      font-weight: 600;
      color: var(--accent2);
      margin-bottom: 6px;
    }}
    .queue-section > summary {{
      cursor: pointer;
      font-family: inherit;
      font-size: 20px;
      font-weight: 700;
      color: var(--ink);
      margin-bottom: 8px;
    }}
    .queue-section[open] > summary {{
      margin-bottom: 10px;
    }}
    .status-panel {{
      display: grid;
      grid-template-columns: repeat(2, minmax(220px, 1fr));
      gap: 12px;
      margin: 0;
      min-width: 0;
    }}
    .status-item {{
      padding: 9px 14px 9px 16px;
      border-radius: 16px;
      background:
        radial-gradient(circle at top right, rgba(45, 212, 191, 0.08) 0%, transparent 30%),
        linear-gradient(180deg, rgba(14, 26, 44, 0.96) 0%, rgba(11, 21, 37, 0.98) 100%);
      border: 1px solid rgba(125, 211, 252, 0.14);
      box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.04), 0 8px 20px rgba(2, 8, 18, 0.24);
      position: relative;
      overflow: hidden;
      white-space: nowrap;
    }}
    .status-item::before {{
      content: "";
      position: absolute;
      inset: 7px auto 7px 0;
      width: 4px;
      border-radius: 999px;
      background: linear-gradient(180deg, var(--accent) 0%, #7dd3fc 100%);
    }}
    .status-item .k {{
      font-size: 11px;
      letter-spacing: 0.08em;
      text-transform: uppercase;
      color: #9fb7d8;
      position: relative;
      margin: 0;
      display: inline;
    }}
    .status-item .v {{
      font-size: 14px;
      font-weight: 500;
      color: #f8fbff;
      text-shadow: 0 1px 0 rgba(0, 0, 0, 0.3);
      position: relative;
      line-height: 1.15;
      white-space: nowrap;
      display: inline;
      margin-left: 8px;
    }}
    .hero-spacer {{
      min-width: 0;
    }}
    .brand-copy h1 {{
      white-space: nowrap;
    }}
    .brand-copy .muted {{
      white-space: nowrap;
    }}
    @media (max-width: 960px) {{
      .hero-top {{ grid-template-columns: 1fr; }}
      .status-panel {{ grid-template-columns: 1fr; }}
      .layout {{ grid-template-columns: 1fr; }}
      h1 {{ font-size: 28px; }}
    }}
  </style>
  <script>
    async function refreshAll(btn) {{
      const original = btn.textContent;
      btn.disabled = true;
      btn.classList.add("loading");
      try {{
        const res = await fetch("/refresh?per_page=100&max_pages=20", {{ method: "POST" }});
        const data = await res.json();
        if (data.ok) {{
          btn.textContent = "Done";
          setTimeout(() => window.location.reload(), 600);
        }} else {{
          btn.textContent = "Failed";
          console.error("refresh failed", data);
        }}
      }} catch (e) {{
        btn.textContent = "Failed";
        console.error(e);
      }} finally {{
        setTimeout(() => {{
          btn.disabled = false;
          btn.classList.remove("loading");
          if (btn.textContent !== "Failed") btn.textContent = original;
        }}, 2000);
      }}
    }}

    async function runPrReview(prNumber, btn) {{
      const original = btn.textContent;
      btn.disabled = true;
      btn.textContent = "Queued...";
      try {{
        const res = await fetch(`/reviews/pr/${{prNumber}}/run`, {{ method: "POST" }});
        const data = await res.json();
        if (data.ok && data.accepted) {{
          btn.textContent = "Queued";
        }} else {{
          btn.textContent = "Failed";
          console.error("review enqueue failed", data);
        }}
      }} catch (e) {{
        btn.textContent = "Failed";
        console.error(e);
      }} finally {{
        setTimeout(() => {{
          btn.disabled = false;
          if (btn.textContent !== "Failed") btn.textContent = original;
        }}, 2000);
      }}
    }}

    function formatDuration(totalSeconds) {{
      if (totalSeconds == null) return "N/A";
      const totalMinutes = Math.max(0, Math.floor(Number(totalSeconds) / 60));
      const hours = Math.floor(totalMinutes / 60);
      const minutes = totalMinutes - hours * 60;
      const parts = [];
      if (hours) parts.push(`${{hours}}h`);
      parts.push(`${{minutes}}m`);
      return parts.join(" ");
    }}

    function startRefreshCountdown() {{
      const countdownEl = document.getElementById("next-refresh-countdown");
      if (!countdownEl) return;
      let remaining = Number(countdownEl.dataset.remainingSeconds || "");
      if (!Number.isFinite(remaining)) return;
      countdownEl.textContent = formatDuration(remaining);
      window.setInterval(() => {{
        remaining = Math.max(0, remaining - 1);
        countdownEl.textContent = formatDuration(remaining);
      }}, 1000);
    }}

    window.addEventListener("DOMContentLoaded", startRefreshCountdown);
  </script>
</head>
	<body>
	  <div class="wrap">
	    <section class="hero">
	      <div class="hero-top">
          <div class="hero-heading">
            <div class="brand-copy">
	          <h1>PR Intelligence</h1>
	          <p class="muted">LLM Provider: {escape(configured_llm_display)}</p>
            </div>
          </div>
          <div class="hero-spacer"></div>
          <article class="status-item">
            <div class="k">Last Update</div>
            <div class="v">{escape(_fmt_local_timestamp(refresh_status["last_sync_at"]))}</div>
          </article>
          <article class="status-item">
            <div class="k">Next Update In:</div>
            <div class="v" id="next-refresh-countdown" data-remaining-seconds="{'' if refresh_status['seconds_until_next_refresh'] is None else refresh_status['seconds_until_next_refresh']}">{escape(_fmt_duration(refresh_status["seconds_until_next_refresh"]))}</div>
          </article>
          <button class="btn sync-btn" type="button" onclick="refreshAll(this)">Sync</button>
        </div>
    </section>

    <section class="grid">
      <article class="card"><div class="k">PRs Tracked</div><div class="v">{stats["prs_tracked"]}</div></article>
      <article class="card"><div class="k">Issues Tracked</div><div class="v">{stats["issues_tracked"]}</div></article>
      <article class="card"><div class="k">Needs Review Queue</div><div class="v">{stats["needs_review_queue"]}</div></article>
      <article class="card"><div class="k">Interesting Issues</div><div class="v">{stats["interesting_issues_queue"]}</div></article>
      <article class="card"><div class="k">Deep PR Reviews</div><div class="v">{stats["deep_pr_reviews"]}</div></article>
    </section>

    <section class="layout">
      <div>
        <article class="card">
          <details class="tab-fold">
            <summary>New/Updated PRs Today</summary>
            <table>
              <thead><tr><th>PR</th><th>Title</th><th>Updated</th><th>Action</th></tr></thead>
              <tbody>{''.join(visible_new_updated_rows) if new_updated_rows else '<tr><td colspan="4">No PR updates observed today.</td></tr>'}</tbody>
            </table>
            {folded_new_updated_html}
          </details>
        </article>
        <article class="card" style="margin-top: 14px;">
          <details class="tab-fold">
            <summary>Latest Report</summary>
            <p class="muted">Date: {escape(stats["latest_report_date"] or "N/A")}</p>
            <div class="report">{latest_report_html}</div>
          </details>
        </article>
      </div>
      <aside>
        <article class="card">
          <details class="queue-section">
            <summary>PRs Needing Review ({stats["needs_review_queue"]})</summary>
            <table>
              <thead><tr><th>PR</th><th>Title</th><th>Score</th><th>Reasons</th></tr></thead>
              <tbody>{''.join(visible_review_rows) if review_rows else '<tr><td colspan="4">No PRs queued.</td></tr>'}</tbody>
            </table>
            {folded_review_html}
          </details>
        </article>
        <article class="card" style="margin-top: 14px;">
          <details class="queue-section">
            <summary>Interesting Issues ({stats["interesting_issues_queue"]})</summary>
            <table>
              <thead><tr><th>Issue</th><th>Title</th><th>Score</th><th>Reasons</th></tr></thead>
              <tbody>{''.join(visible_issue_rows) if issue_rows else '<tr><td colspan="4">No issues queued.</td></tr>'}</tbody>
            </table>
            {folded_issue_html}
          </details>
        </article>
        <article class="card" style="margin-top: 14px;">
          <details class="queue-section">
            <summary>Deep PR Reviews ({len(deep_review_entries)})</summary>
            <div>{''.join(deep_review_entries) if deep_review_entries else '<p class="muted">No deep reviews yet.</p>'}</div>
          </details>
        </article>
        <article class="card" style="margin-top: 14px;">
          <h3>Review Jobs</h3>
          <p class="muted">Shows recent async PR review jobs (queued/running/completed).</p>
          <table>
            <thead><tr><th>Job ID</th><th>PR</th><th>Status</th><th>Created</th><th>Finished</th><th>Details</th></tr></thead>
            <tbody>{''.join(job_rows) if job_rows else '<tr><td colspan="6">No review jobs yet.</td></tr>'}</tbody>
          </table>
        </article>
      </aside>
    </section>
  </div>
</body>
</html>"""

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
        # Step 1: Sync GitHub data
        synced = snapshot_ingestor.sync_recent(
            per_page=per_page,
            max_pages=max_pages,
            since=None,
            prune_missing_open_prs=prune_missing_open_prs,
        )

        # Step 2: Recompute scores
        prs_scored = 0
        issues_scored = 0
        needs_review = 0
        interesting_issues = 0

        for pr in repo.prs.values():
            if pr.state != "open":
                continue
            signal = review_need_agent.run(pr)
            repo.save_review_signal(signal)
            prs_scored += 1
            if signal.needs_review:
                needs_review += 1

        for issue in repo.issues.values():
            if issue.state != "open":
                continue
            signal = issue_insight_agent.run(issue)
            repo.save_issue_signal(signal)
            issues_scored += 1
            if signal.interesting:
                interesting_issues += 1

        # Step 3: Run analysis & generate report
        out = daily_graph.invoke()
        analysis_run = out.get("analysis_run")
        report = out.get("daily_report")

        return {
            "ok": True,
            "synced": synced,
            "scored": {
                "prs": prs_scored,
                "issues": issues_scored,
                "needs_review": needs_review,
                "interesting_issues": interesting_issues,
            },
            "analysis_run": analysis_run.model_dump() if analysis_run else None,
            "report": report.model_dump() if report else None,
            "notifications": out.get("notifications", []),
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
            "## Findings",
            "",
        ]

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
        # Get the markdown content
        markdown_content = latest_pr_review_markdown(pr_number)

        # Wrap in HTML with styling and markdown renderer
        html = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>PR #{pr_number} Review</title>
    <script src="https://cdn.jsdelivr.net/npm/marked@11.1.1/marked.min.js"></script>
    <style>
        * {{
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }}

        body {{
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif;
            line-height: 1.6;
            color: #24292f;
            background: #f6f8fa;
            padding: 20px;
        }}

        .container {{
            max-width: 980px;
            margin: 0 auto;
            background: white;
            padding: 40px;
            border-radius: 8px;
            box-shadow: 0 1px 3px rgba(0,0,0,0.1);
        }}

        h1 {{
            font-size: 2em;
            margin-bottom: 0.5em;
            padding-bottom: 0.3em;
            border-bottom: 1px solid #d0d7de;
            color: #0969da;
        }}

        h2 {{
            font-size: 1.5em;
            margin-top: 1.5em;
            margin-bottom: 0.5em;
            padding-bottom: 0.3em;
            border-bottom: 1px solid #d0d7de;
        }}

        h3 {{
            font-size: 1.25em;
            margin-top: 1.5em;
            margin-bottom: 0.5em;
        }}

        h4 {{
            font-size: 1.1em;
            margin-top: 1em;
            margin-bottom: 0.5em;
            color: #57606a;
        }}

        p {{
            margin-bottom: 1em;
        }}

        strong {{
            color: #24292f;
            font-weight: 600;
        }}

        a {{
            color: #0969da;
            text-decoration: none;
        }}

        a:hover {{
            text-decoration: underline;
        }}

        hr {{
            border: none;
            border-top: 1px solid #d0d7de;
            margin: 1.5em 0;
        }}

        ul, ol {{
            margin-left: 2em;
            margin-bottom: 1em;
        }}

        li {{
            margin-bottom: 0.25em;
        }}

        code {{
            background: #f6f8fa;
            padding: 0.2em 0.4em;
            border-radius: 3px;
            font-family: 'SF Mono', Monaco, 'Courier New', monospace;
            font-size: 0.9em;
        }}

        pre {{
            background: #f6f8fa;
            padding: 16px;
            border-radius: 6px;
            overflow-x: auto;
            margin-bottom: 1em;
        }}

        pre code {{
            background: none;
            padding: 0;
        }}

        .back-link {{
            display: inline-block;
            margin-bottom: 20px;
            padding: 8px 16px;
            background: #f6f8fa;
            border: 1px solid #d0d7de;
            border-radius: 6px;
            color: #24292f;
            text-decoration: none;
            font-size: 0.9em;
        }}

        .back-link:hover {{
            background: #eaeef2;
            text-decoration: none;
        }}

        @media (max-width: 768px) {{
            body {{
                padding: 10px;
            }}

            .container {{
                padding: 20px;
            }}

            h1 {{
                font-size: 1.5em;
            }}
        }}
    </style>
</head>
<body>
    <div class="container">
        <a href="/ui" class="back-link">&larr; Back to Dashboard</a>
        <div id="content"></div>
    </div>

    <script>
        const markdown = {json.dumps(markdown_content)};
        document.getElementById('content').innerHTML = marked.parse(markdown);
    </script>
</body>
</html>"""
        return html

    @app.get("/reviews/pr/top")
    def top_pr_reviews(limit: int = 20) -> dict:
        reports = [r.model_dump() for r in repo.top_pr_review_reports(limit=limit)]
        return {"ok": True, "reports": reports}

    @app.get("/reports/daily/latest.md", response_class=PlainTextResponse)
    def latest_report_markdown() -> str:
        report = repo.latest_daily_report()
        if not report:
            return "# Polaris PR Intelligence Report\n\nNo report has been generated yet.\n"
        return report.markdown

    @app.get("/queues/needs-review", response_model=list[QueueItem])
    def needs_review() -> list[QueueItem]:
        items: list[QueueItem] = []
        for signal in sorted(repo.review_signals.values(), key=lambda s: s.score, reverse=True):
            if not signal.needs_review:
                continue
            pr = repo.prs.get(signal.pr_number)
            if not pr:
                continue
            if pr.state != "open":
                continue
            if not _is_target_review_pr(pr, signal):
                continue
            items.append(QueueItem(number=pr.number, title=pr.title, score=signal.score, reasons=signal.reasons, url=pr.html_url))
        return items

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
