from __future__ import annotations

import hashlib
import hmac
import json

from fastapi import FastAPI, Header, HTTPException, Request

from polaris_pr_intel.graphs.daily_report_graph import DailyReportGraph
from polaris_pr_intel.graphs.event_graph import EventGraph
from polaris_pr_intel.ingest import SnapshotIngestor
from polaris_pr_intel.models import GitHubEvent, QueueItem
from polaris_pr_intel.store.repository import InMemoryRepository



def create_app(
    repo: InMemoryRepository,
    event_graph: EventGraph,
    daily_graph: DailyReportGraph,
    snapshot_ingestor: SnapshotIngestor,
    webhook_secret: str = "",
) -> FastAPI:
    app = FastAPI(title="Polaris PR Intelligence")

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/webhooks/github")
    async def github_webhook(
        request: Request,
        x_github_event: str = Header(default=""),
        x_hub_signature_256: str = Header(default=""),
    ) -> dict:
        body = await request.body()
        if webhook_secret:
            digest = hmac.new(webhook_secret.encode(), body, hashlib.sha256).hexdigest()
            expected = f"sha256={digest}"
            if not hmac.compare_digest(expected, x_hub_signature_256):
                raise HTTPException(status_code=401, detail="invalid signature")

        payload = json.loads(body.decode("utf-8"))
        event = GitHubEvent(event_type=x_github_event, action=payload.get("action"), payload=payload)
        out = event_graph.invoke(event)
        return {"ok": True, "notifications": out.get("notifications", [])}

    @app.post("/reports/daily/run")
    def run_daily_report() -> dict:
        out = daily_graph.invoke()
        return {"ok": True, "notifications": out.get("notifications", [])}

    @app.post("/sync/recent")
    def sync_recent(per_page: int = 30) -> dict:
        synced = snapshot_ingestor.sync_recent(per_page=per_page)
        return {"ok": True, "synced": synced}

    @app.get("/reports/daily/latest")
    def latest_report() -> dict:
        report = repo.latest_daily_report()
        if not report:
            return {"ok": True, "report": None}
        return {"ok": True, "report": report.model_dump()}

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
