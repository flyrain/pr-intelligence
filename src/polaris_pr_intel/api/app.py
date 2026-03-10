from __future__ import annotations

import hashlib
import hmac
import json
from html import escape

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import HTMLResponse, PlainTextResponse

from polaris_pr_intel.graphs.daily_report_graph import DailyReportGraph
from polaris_pr_intel.graphs.event_graph import EventGraph
from polaris_pr_intel.ingest import SnapshotIngestor
from polaris_pr_intel.models import GitHubEvent, QueueItem
from polaris_pr_intel.store.base import Repository



def create_app(
    repo: Repository,
    event_graph: EventGraph,
    daily_graph: DailyReportGraph,
    snapshot_ingestor: SnapshotIngestor,
    webhook_secret: str = "",
) -> FastAPI:
    app = FastAPI(title="Polaris PR Intelligence")

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

    def _stats() -> dict:
        needs_review_count = sum(1 for s in repo.review_signals.values() if s.needs_review)
        interesting_issue_count = sum(1 for s in repo.issue_signals.values() if s.interesting)
        latest_report = repo.latest_daily_report()
        return {
            "prs_tracked": len(repo.prs),
            "issues_tracked": len(repo.issues),
            "review_signals": len(repo.review_signals),
            "issue_signals": len(repo.issue_signals),
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
                "GET /queues/needs-review to see prioritized PRs",
                "GET /queues/interesting-issues to see prioritized issues",
            ],
            "links": {
                "docs": "/docs",
                "health": "/healthz",
                "stats": "/stats",
                "latest_report": "/reports/daily/latest",
            },
        }

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/ui", response_class=HTMLResponse)
    def dashboard() -> str:
        stats = _stats()
        latest = repo.latest_daily_report()
        latest_report_html = (
            _report_markdown_to_html(latest.markdown)
            if latest
            else "<h2>No Report Yet</h2><p>Run <code>POST /reports/daily/run</code> to generate one.</p>"
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
    }}
    .btn.primary {{ background: var(--accent); color: white; border-color: var(--accent); }}
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
    @media (max-width: 960px) {{
      .layout {{ grid-template-columns: 1fr; }}
      h1 {{ font-size: 28px; }}
    }}
  </style>
</head>
<body>
  <div class="wrap">
    <section class="hero">
      <h1>Polaris PR Intelligence</h1>
      <p class="muted">Daily PR/issue triage dashboard for apache/polaris.</p>
      <div class="actions">
        <a class="btn primary" href="/docs">Open API Docs</a>
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
    </section>

    <section class="layout">
      <article class="card">
        <h2>Latest Report</h2>
        <p class="muted">Date: {escape(stats["latest_report_date"] or "N/A")}</p>
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
