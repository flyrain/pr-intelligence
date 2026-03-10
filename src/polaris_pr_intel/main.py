from __future__ import annotations

import argparse

import uvicorn

from polaris_pr_intel.api.app import create_app
from polaris_pr_intel.config import load_settings
from polaris_pr_intel.github.client import GitHubClient
from polaris_pr_intel.graphs.daily_report_graph import DailyReportGraph
from polaris_pr_intel.graphs.event_graph import EventGraph
from polaris_pr_intel.ingest import SnapshotIngestor
from polaris_pr_intel.store.repository import InMemoryRepository



def build_runtime():
    settings = load_settings()
    repo = InMemoryRepository()
    gh = GitHubClient(settings.github_token, settings.github_owner, settings.github_repo)
    snapshot_ingestor = SnapshotIngestor(gh, repo)
    event_graph = EventGraph(repo)
    daily_graph = DailyReportGraph(repo)
    app = create_app(
        repo,
        event_graph,
        daily_graph,
        snapshot_ingestor=snapshot_ingestor,
        webhook_secret=settings.github_webhook_secret,
    )
    return app, daily_graph



def main() -> None:
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
