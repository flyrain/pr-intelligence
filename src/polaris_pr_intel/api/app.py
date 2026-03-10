from __future__ import annotations

import hashlib
import hmac
import json

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import PlainTextResponse

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
                "POST /sync/recent to pull latest PRs/issues from GitHub",
                "POST /reports/daily/run to generate a report",
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
    def run_daily_report() -> dict:
        out = daily_graph.invoke()
        return {"ok": True, "notifications": out.get("notifications", [])}

    @app.post("/sync/recent")
    def sync_recent(per_page: int = 30, max_pages: int = 1, since: str | None = None) -> dict:
        synced = snapshot_ingestor.sync_recent(per_page=per_page, max_pages=max_pages, since=since)
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
