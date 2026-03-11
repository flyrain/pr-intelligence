from __future__ import annotations

import hashlib
import hmac
import json
import os
import threading
from datetime import datetime, timezone
from html import escape
from uuid import uuid4

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import HTMLResponse, PlainTextResponse

from polaris_pr_intel.graphs.daily_report_graph import DailyReportGraph
from polaris_pr_intel.graphs.event_graph import EventGraph
from polaris_pr_intel.graphs.pr_review_graph import PRReviewGraph
from polaris_pr_intel.ingest import SnapshotIngestor
from polaris_pr_intel.models import GitHubEvent, QueueItem
from polaris_pr_intel.store.base import Repository



def create_app(
    repo: Repository,
    event_graph: EventGraph,
    daily_graph: DailyReportGraph,
    pr_review_graph: PRReviewGraph,
    snapshot_ingestor: SnapshotIngestor,
    webhook_secret: str = "",
) -> FastAPI:
    app = FastAPI(title="Polaris PR Intelligence")
    review_jobs: dict[str, dict] = {}
    review_jobs_lock = threading.Lock()
    review_job_timeout_sec = int(os.getenv("REVIEW_JOB_TIMEOUT_SEC", "1200"))

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

    def _remove_markdown_section(markdown: str, heading: str) -> str:
        lines = markdown.splitlines()
        out: list[str] = []
        skip = False
        target = f"## {heading}".strip()
        for line in lines:
            if line.strip() == target:
                skip = True
                continue
            if skip and line.startswith("## "):
                skip = False
            if not skip:
                out.append(line)
        return "\n".join(out).strip()

    def _remove_legacy_report_title(markdown: str) -> str:
        lines = markdown.splitlines()
        out: list[str] = []
        removed = False
        for line in lines:
            if not removed and line.startswith("# Polaris PR Intelligence Report"):
                removed = True
                continue
            out.append(line)
        return "\n".join(out).strip()

    def _stats() -> dict:
        needs_review_count = sum(1 for s in repo.review_signals.values() if s.needs_review)
        interesting_issue_count = sum(1 for s in repo.issue_signals.values() if s.interesting)
        latest_report = repo.latest_daily_report()
        return {
            "prs_tracked": len(repo.prs),
            "issues_tracked": len(repo.issues),
            "review_signals": len(repo.review_signals),
            "issue_signals": len(repo.issue_signals),
            "deep_pr_reviews": len(repo.pr_review_reports),
            "needs_review_queue": needs_review_count,
            "interesting_issues_queue": interesting_issue_count,
            "daily_reports": len(repo.daily_reports),
            "latest_report_date": latest_report.date if latest_report else None,
            "last_sync_at": repo.last_sync_at.isoformat() if repo.last_sync_at else None,
        }

    @app.get("/")
    def index() -> dict:
        return {
            "service": "Polaris PR Intelligence",
            "status": "ok",
            "stats": _stats(),
            "next_steps": [
                "POST /sync/all-open to pull open PRs/issues from GitHub",
                "POST /reports/daily/run to refresh and generate a report",
                "POST /reviews/pr/{number}/run to run subagent deep review",
                "GET /queues/needs-review to see prioritized PRs",
                "GET /queues/interesting-issues to see prioritized issues",
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
        local_now = datetime.now().astimezone()
        local_today = local_now.date()
        local_tz = local_now.tzinfo
        latest = repo.latest_daily_report()
        report_markdown_for_ui = (
            _remove_markdown_section(_remove_legacy_report_title(latest.markdown), "New/Updated PRs Today")
            if latest
            else ""
        )
        latest_report_html = (
            _report_markdown_to_html(report_markdown_for_ui)
            if latest
            else "<h2>No Report Yet</h2><p>Run <code>POST /reports/daily/run</code> to generate one.</p>"
        )
        new_updated_rows = []
        def _is_updated_today_local(updated_at: datetime) -> bool:
            dt = updated_at if updated_at.tzinfo else updated_at.replace(tzinfo=timezone.utc)
            if local_tz is None:
                return dt.date() == local_today
            return dt.astimezone(local_tz).date() == local_today

        new_updated_prs = sorted(
            [pr for pr in repo.prs.values() if _is_updated_today_local(pr.updated_at)],
            key=lambda p: p.updated_at,
            reverse=True,
        )[:20]
        for pr in new_updated_prs:
            new_updated_rows.append(
                "<tr>"
                f"<td><a href=\"{escape(pr.html_url)}\" target=\"_blank\" rel=\"noopener noreferrer\">#{pr.number}</a></td>"
                f"<td>{escape(pr.title)}</td>"
                f"<td>{escape(pr.updated_at.isoformat())}</td>"
                f"<td><button class=\"action-btn\" onclick=\"runPrReview({pr.number}, this)\">Run Review</button></td>"
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
        for signal in sorted(repo.review_signals.values(), key=lambda s: s.score, reverse=True)[:20]:
            pr = repo.prs.get(signal.pr_number)
            if not pr or not signal.needs_review:
                continue
            review_rows.append(
                f"<tr><td><a href=\"{escape(pr.html_url)}\" target=\"_blank\" rel=\"noopener noreferrer\">#{pr.number}</a></td>"
                f"<td>{escape(pr.title)}</td><td>{signal.score:.1f}</td><td>{escape(', '.join(signal.reasons))}</td></tr>"
            )
        issue_rows = []
        for signal in sorted(repo.issue_signals.values(), key=lambda s: s.score, reverse=True)[:20]:
            issue = repo.issues.get(signal.issue_number)
            if not issue or not signal.interesting:
                continue
            issue_rows.append(
                f"<tr><td><a href=\"{escape(issue.html_url)}\" target=\"_blank\" rel=\"noopener noreferrer\">#{issue.number}</a></td>"
                f"<td>{escape(issue.title)}</td><td>{signal.score:.1f}</td><td>{escape(', '.join(signal.reasons))}</td></tr>"
            )
        deep_review_rows = []
        deep_review_details = []
        for report in repo.top_pr_review_reports(limit=20):
            pr = repo.prs.get(report.pr_number)
            if not pr:
                continue
            deep_review_rows.append(
                f"<tr><td><a href=\"{escape(pr.html_url)}\" target=\"_blank\" rel=\"noopener noreferrer\">#{pr.number}</a></td>"
                f"<td>{escape(pr.title)}</td><td>{report.overall_priority:.2f}</td><td>{escape(report.provider)}</td></tr>"
            )
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
            deep_review_details.append(
                "<details class=\"review-detail\">"
                f"<summary>PR #{pr.number} · priority={report.overall_priority:.2f} · {escape(pr.title)}</summary>"
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
      --bg: #f4f7f3;
      --card: #ffffff;
      --ink: #1a1f18;
      --muted: #5f6f5a;
      --line: #d3ded0;
      --accent: #0f766e;
      --accent2: #166534;
      --good: #14532d;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      color: var(--ink);
      font-family: "IBM Plex Sans", "Segoe UI", sans-serif;
      background:
        radial-gradient(circle at 0% 0%, #d9f3e6 0%, transparent 35%),
        radial-gradient(circle at 100% 20%, #d8ecff 0%, transparent 35%),
        var(--bg);
    }}
    .wrap {{ max-width: 1200px; margin: 0 auto; padding: 24px; }}
    .hero {{
      border: 1px solid var(--line);
      background: linear-gradient(120deg, #effcf4 0%, #f8fffa 55%, #eef8ff 100%);
      border-radius: 18px;
      padding: 20px;
      box-shadow: 0 10px 30px rgba(0,0,0,0.06);
    }}
    h1,h2,h3 {{ font-family: "IBM Plex Serif", Georgia, serif; margin: 0 0 12px 0; }}
    h1 {{ font-size: 34px; }}
    h2 {{ font-size: 24px; margin-top: 18px; }}
    .muted {{ color: var(--muted); margin: 0 0 14px 0; }}
    .actions {{ display: flex; gap: 10px; flex-wrap: wrap; margin-top: 12px; }}
    .btn {{
      border: 1px solid var(--line);
      background: var(--card);
      color: var(--ink);
      padding: 8px 12px;
      border-radius: 10px;
      text-decoration: none;
      font-weight: 600;
      cursor: pointer;
    }}
    .btn.primary {{ background: var(--accent); color: white; border-color: var(--accent); }}
    .btn.sync-btn {{
      background: linear-gradient(135deg, #0f766e 0%, #166534 100%);
      color: #fff;
      border-color: #0f766e;
      font-weight: 700;
      box-shadow: 0 6px 16px rgba(15, 118, 110, 0.28);
    }}
    .btn.sync-btn:hover {{
      filter: brightness(1.05);
    }}
    .btn.sync-btn:disabled {{
      opacity: 0.75;
      cursor: default;
      box-shadow: none;
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
    .k {{ font-size: 12px; text-transform: uppercase; letter-spacing: .08em; color: var(--muted); }}
    .v {{ font-size: 28px; font-weight: 700; margin-top: 4px; }}
    .layout {{
      display: grid;
      grid-template-columns: 1.1fr .9fr;
      gap: 16px;
      align-items: start;
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
      background: #fbfffd;
      padding: 10px 12px;
      margin-bottom: 10px;
    }}
    .review-detail summary {{
      cursor: pointer;
      font-weight: 600;
      color: var(--accent2);
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
    .verdict.high {{ background: #ffeaea; border-color: #e5a2a2; }}
    .verdict.medium {{ background: #fff6e5; border-color: #e6c27a; }}
    .verdict.low {{ background: #eafaf0; border-color: #9fcdaf; }}
    .job-status {{
      display: inline-block;
      border-radius: 999px;
      padding: 2px 8px;
      font-size: 11px;
      border: 1px solid var(--line);
      text-transform: uppercase;
      letter-spacing: .04em;
    }}
    .job-status-queued {{ background: #f2f4f7; border-color: #cfd6de; }}
    .job-status-running {{ background: #e6f4ff; border-color: #98c9f0; }}
    .job-status-done {{ background: #eafaf0; border-color: #9fcdaf; }}
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
      background: #fbfffd;
    }}
    .folded-section > summary {{
      cursor: pointer;
      font-weight: 600;
      color: var(--accent2);
      margin-bottom: 6px;
    }}
    @media (max-width: 960px) {{
      .layout {{ grid-template-columns: 1fr; }}
      h1 {{ font-size: 28px; }}
    }}
  </style>
  <script>
    async function syncAllOpen(btn) {{
      const original = btn.textContent;
      btn.disabled = true;
      btn.textContent = "Syncing...";
      try {{
        const res = await fetch("/sync/all-open?per_page=100&max_pages=20", {{ method: "POST" }});
        const data = await res.json();
        if (data.ok) {{
          btn.textContent = "Synced";
          setTimeout(() => window.location.reload(), 600);
        }} else {{
          btn.textContent = "Failed";
          console.error("sync all-open failed", data);
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
  </script>
</head>
<body>
  <div class="wrap">
    <section class="hero">
      <h1>Polaris PR Intelligence</h1>
      <p class="muted">Daily PR/issue triage dashboard for apache/polaris.</p>
      <div class="actions">
        <a class="btn primary" href="/docs">Open API Docs</a>
        <button class="btn sync-btn" type="button" onclick="syncAllOpen(this)">Sync All Open PRs/Issues</button>
        <a class="btn" href="/reports/daily/latest.md">Latest Report Markdown</a>
        <a class="btn" href="/queues/needs-review">Needs Review JSON</a>
        <a class="btn" href="/queues/interesting-issues">Interesting Issues JSON</a>
      </div>
    </section>

    <section class="grid">
      <article class="card"><div class="k">PRs Tracked</div><div class="v">{stats["prs_tracked"]}</div></article>
      <article class="card"><div class="k">Issues Tracked</div><div class="v">{stats["issues_tracked"]}</div></article>
      <article class="card"><div class="k">Needs Review Queue</div><div class="v">{stats["needs_review_queue"]}</div></article>
      <article class="card"><div class="k">Interesting Issues Queue</div><div class="v">{stats["interesting_issues_queue"]}</div></article>
      <article class="card"><div class="k">Deep PR Reviews</div><div class="v">{stats["deep_pr_reviews"]}</div></article>
    </section>

    <section class="layout">
      <article class="card">
        <h2>Latest Report</h2>
        <p class="muted">Date: {escape(stats["latest_report_date"] or "N/A")}</p>
        <h3 style="margin-top:14px;">New/Updated PRs Today</h3>
        <table>
          <thead><tr><th>PR</th><th>Title</th><th>Updated</th><th>Action</th></tr></thead>
          <tbody>{''.join(visible_new_updated_rows) if new_updated_rows else '<tr><td colspan="4">No PR updates observed today.</td></tr>'}</tbody>
        </table>
        {folded_new_updated_html}
        <div class="report">{latest_report_html}</div>
      </article>
      <aside>
        <article class="card">
          <h3>PRs Needing Review</h3>
          <table>
            <thead><tr><th>PR</th><th>Title</th><th>Score</th><th>Reasons</th></tr></thead>
            <tbody>{''.join(review_rows) if review_rows else '<tr><td colspan="4">No PRs queued.</td></tr>'}</tbody>
          </table>
        </article>
        <article class="card" style="margin-top: 14px;">
          <h3>Interesting Issues</h3>
          <table>
            <thead><tr><th>Issue</th><th>Title</th><th>Score</th><th>Reasons</th></tr></thead>
            <tbody>{''.join(issue_rows) if issue_rows else '<tr><td colspan="4">No issues queued.</td></tr>'}</tbody>
          </table>
        </article>
        <article class="card" style="margin-top: 14px;">
          <h3>Deep PR Reviews</h3>
          <table>
            <thead><tr><th>PR</th><th>Title</th><th>Priority</th><th>Provider</th></tr></thead>
            <tbody>{''.join(deep_review_rows) if deep_review_rows else '<tr><td colspan="4">No deep reviews yet.</td></tr>'}</tbody>
          </table>
          <h3 style="margin-top:14px;">Deep Review Details</h3>
          <div>{''.join(deep_review_details[:6]) if deep_review_details else '<p class="muted">No detailed findings yet.</p>'}</div>
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

    @app.post("/reports/daily/run")
    def run_daily_report(refresh: bool = True, per_page: int = 100, max_pages: int = 20) -> dict:
        synced: dict | None = None
        if refresh:
            synced = snapshot_ingestor.sync_recent(per_page=per_page, max_pages=max_pages, since=None)
        out = daily_graph.invoke()
        resp = {"ok": True, "notifications": out.get("notifications", [])}
        if synced is not None:
            resp["synced"] = synced
        return resp

    @app.post("/sync/recent")
    def sync_recent(per_page: int = 30, max_pages: int = 1, since: str | None = None) -> dict:
        synced = snapshot_ingestor.sync_recent(per_page=per_page, max_pages=max_pages, since=since)
        return {"ok": True, "synced": synced}

    @app.post("/sync/all-open")
    def sync_all_open(per_page: int = 100, max_pages: int = 20) -> dict:
        synced = snapshot_ingestor.sync_recent(per_page=per_page, max_pages=max_pages, since=None)
        return {"ok": True, "synced": synced}

    @app.post("/reviews/pr/{pr_number}/run")
    def run_pr_review(pr_number: int, wait: bool = False) -> dict:
        def _execute() -> dict:
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

        if wait:
            result = _execute()
            result["mode"] = "sync"
            return result

        job_id = str(uuid4())
        now = datetime.now(timezone.utc).isoformat()
        with review_jobs_lock:
            review_jobs[job_id] = {
                "job_id": job_id,
                "pr_number": pr_number,
                "status": "queued",
                "created_at": now,
                "started_at": None,
                "finished_at": None,
                "result": None,
            }

        def _run_job() -> None:
            with review_jobs_lock:
                job = review_jobs[job_id]
                job["status"] = "running"
                job["started_at"] = datetime.now(timezone.utc).isoformat()
            try:
                result = _execute()
                status = "completed" if result.get("ok") else "failed"
            except Exception as exc:  # defensive path to avoid untracked crashes
                result = {"ok": False, "errors": [str(exc)], "report": None}
                status = "failed"

            with review_jobs_lock:
                job = review_jobs[job_id]
                job["status"] = status
                job["finished_at"] = datetime.now(timezone.utc).isoformat()
                job["result"] = result

        threading.Thread(target=_run_job, daemon=True).start()
        return {
            "ok": True,
            "accepted": True,
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
        if pr_number not in repo.prs:
            fetched = snapshot_ingestor.sync_pr(pr_number)
            if not fetched:
                return {"ok": False, "errors": [f"pr-not-found:{pr_number}"], "report": None}
        out = pr_review_graph.invoke(pr_number)
        report = repo.latest_pr_review_report(pr_number)
        return {
            "ok": True,
            "mode": "sync",
            "notifications": out.get("notifications", []),
            "errors": out.get("errors", []),
            "report": report.model_dump() if report else None,
        }

    @app.post("/reviews/run-open")
    def run_open_pr_reviews(limit: int = 50) -> dict:
        if limit < 1:
            limit = 1
        prs = sorted(repo.prs.values(), key=lambda p: p.updated_at, reverse=True)[:limit]
        reviewed: list[int] = []
        skipped: list[int] = []
        for pr in prs:
            out = pr_review_graph.invoke(pr.number)
            if out.get("errors"):
                skipped.append(pr.number)
            else:
                reviewed.append(pr.number)
        return {"ok": True, "reviewed": reviewed, "skipped": skipped, "total": len(prs)}

    @app.get("/reviews/pr/{pr_number}/latest")
    def latest_pr_review(pr_number: int) -> dict:
        report = repo.latest_pr_review_report(pr_number)
        return {"ok": True, "report": report.model_dump() if report else None}

    @app.get("/reviews/pr/top")
    def top_pr_reviews(limit: int = 20) -> dict:
        reports = [r.model_dump() for r in repo.top_pr_review_reports(limit=limit)]
        return {"ok": True, "reports": reports}

    @app.get("/reports/daily/latest")
    def latest_report() -> dict:
        report = repo.latest_daily_report()
        if not report:
            return {"ok": True, "report": None}
        return {"ok": True, "report": report.model_dump()}

    @app.get("/reports/daily/latest.md", response_class=PlainTextResponse)
    def latest_report_markdown() -> str:
        report = repo.latest_daily_report()
        if not report:
            return "# Polaris PR Intelligence Report\n\nNo report has been generated yet.\n"
        return report.markdown

    @app.get("/reports/daily")
    def list_reports(limit: int = 30, offset: int = 0) -> dict:
        reports = [r.model_dump() for r in repo.list_daily_reports(limit=limit, offset=offset)]
        return {"ok": True, "reports": reports, "limit": limit, "offset": offset}

    @app.get("/queues/needs-review", response_model=list[QueueItem])
    def needs_review() -> list[QueueItem]:
        items: list[QueueItem] = []
        for signal in sorted(repo.review_signals.values(), key=lambda s: s.score, reverse=True):
            if not signal.needs_review:
                continue
            pr = repo.prs.get(signal.pr_number)
            if not pr:
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
