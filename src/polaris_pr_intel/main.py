from __future__ import annotations

import argparse
import logging

import uvicorn

from polaris_pr_intel.api.app import create_app
from polaris_pr_intel.agents.pr_reviewer import PRSubagentReviewer
from polaris_pr_intel.config import load_settings
from polaris_pr_intel.github.client import GitHubClient
from polaris_pr_intel.graphs.daily_report_graph import DailyReportGraph
from polaris_pr_intel.graphs.event_graph import EventGraph
from polaris_pr_intel.graphs.pr_review_graph import PRReviewGraph
from polaris_pr_intel.ingest import SnapshotIngestor
from polaris_pr_intel.llm.factory import build_llm_adapter
from polaris_pr_intel.scheduler.daily import DailyScheduler
from polaris_pr_intel.store.base import Repository
from polaris_pr_intel.store.repository import InMemoryRepository
from polaris_pr_intel.store.sqlite_repository import SQLiteRepository

logger = logging.getLogger(__name__)



def _build_repository(store_backend: str, sqlite_path: str) -> Repository:
    if store_backend == "sqlite":
        return SQLiteRepository(sqlite_path)
    return InMemoryRepository()


def _llm_display(provider: str, model: str) -> str:
    model = model.strip()
    return f"{provider} / {model}" if model else provider


def _configure_logging() -> None:
    root_logger = logging.getLogger()
    if root_logger.handlers:
        return
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


def build_runtime():
    settings = load_settings()
    repo = _build_repository(settings.store_backend, settings.sqlite_path)
    llm = build_llm_adapter(settings)
    logger.info("Configured LLM provider: %s", _llm_display(llm.provider, llm.model))
    reviewer = PRSubagentReviewer(llm, enable_self_review=settings.enable_self_review)
    gh = GitHubClient(settings.github_token, settings.github_owner, settings.github_repo)
    snapshot_ingestor = SnapshotIngestor(gh, repo)
    event_graph = EventGraph(repo, settings=settings)
    daily_graph = DailyReportGraph(repo, llm=llm, settings=settings)
    pr_review_graph = PRReviewGraph(repo, reviewer=reviewer, gh=gh)
    scheduler = DailyScheduler(
        daily_graph,
        snapshot_ingestor=snapshot_ingestor,
        repo=repo,
        review_need_agent=event_graph.review_need,
        issue_insight_agent=event_graph.issue_insight,
        enable_periodic_refresh=settings.enable_periodic_refresh,
        refresh_interval_hours=settings.refresh_interval_hours,
    )
    app = create_app(
        repo,
        event_graph,
        daily_graph,
        pr_review_graph,
        snapshot_ingestor=snapshot_ingestor,
        settings=settings,
        webhook_secret=settings.github_webhook_secret,
        scheduler=scheduler,
    )
    app.add_event_handler("startup", scheduler.start)
    app.add_event_handler("shutdown", scheduler.stop)
    app.add_event_handler("shutdown", gh.close)
    if isinstance(repo, SQLiteRepository):
        app.add_event_handler("shutdown", repo.close)
    return app, daily_graph



def main() -> None:
    _configure_logging()
    parser = argparse.ArgumentParser(description="Polaris PR intelligence service")
    sub = parser.add_subparsers(dest="command", required=True)

    serve = sub.add_parser("serve", help="Run API server")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8080)

    sub.add_parser("run-daily", help="Generate one daily report")

    args = parser.parse_args()
    app, daily_graph = build_runtime()

    if args.command == "serve":
        uvicorn.run(app, host=args.host, port=args.port)
    elif args.command == "run-daily":
        out = daily_graph.invoke()
        notes = out.get("notifications", [])
        print({"ok": True, "notifications": notes})


if __name__ == "__main__":
    main()
