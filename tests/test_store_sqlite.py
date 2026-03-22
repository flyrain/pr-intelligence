from __future__ import annotations

from datetime import datetime, timezone

from fastapi.testclient import TestClient

from polaris_pr_intel.agents.pr_reviewer import PRSubagentReviewer
from polaris_pr_intel.api.app import create_app
from polaris_pr_intel.config import Settings
from polaris_pr_intel.graphs.daily_report_graph import DailyReportGraph
from polaris_pr_intel.graphs.event_graph import EventGraph
from polaris_pr_intel.graphs.pr_review_graph import PRReviewGraph
from polaris_pr_intel.ingest import SnapshotIngestor
from polaris_pr_intel.llm.adapters import HeuristicLLMAdapter
from polaris_pr_intel.models import AnalysisRun, PullRequestSnapshot, ReportArtifact
from polaris_pr_intel.models import PRReviewReport
from polaris_pr_intel.store.sqlite_repository import SQLiteRepository


def test_sqlite_repository_persists_data_across_reopen(tmp_path) -> None:
    db_path = tmp_path / "intel.db"

    repo = SQLiteRepository(str(db_path))
    pr = PullRequestSnapshot(
        number=7,
        title="Persist me",
        body="",
        state="open",
        draft=False,
        author="alice",
        labels=[],
        requested_reviewers=[],
        comments=0,
        review_comments=0,
        commits=1,
        changed_files=1,
        additions=10,
        deletions=2,
        html_url="https://example.com/pr/7",
        updated_at=datetime.now(timezone.utc),
    )
    repo.upsert_pr(pr)
    repo.save_analysis_run(
        AnalysisRun(artifacts=[ReportArtifact(name="executive-summary", title="Executive Summary", markdown="# Executive Summary")])
    )
    repo.mark_processed_event("evt-7")
    repo.last_sync_at = datetime(2026, 3, 10, 1, 2, 3, tzinfo=timezone.utc)
    repo.close()

    repo2 = SQLiteRepository(str(db_path))
    assert 7 in repo2.prs
    assert repo2.latest_analysis_run() is not None
    assert repo2.has_processed_event("evt-7")
    assert repo2.last_sync_at is not None
    repo2.close()


def test_sqlite_backed_ui_stats_reads_pr_review_reports(tmp_path) -> None:
    db_path = tmp_path / "intel.db"
    repo = SQLiteRepository(str(db_path))
    repo.save_pr_review_report(
        PRReviewReport(
            pr_number=7,
            provider="heuristic",
            model="local-heuristic",
            findings=[],
            overall_priority=0.5,
            overall_recommendation="Review when convenient.",
        )
    )

    class _DummyGitHub:
        def list_recent_pull_requests(self, per_page: int = 30, page: int = 1):
            return []

        def list_recent_issues(self, per_page: int = 30, page: int = 1, since: str | None = None):
            return []

        def get_pull_request(self, pr_number: int):
            raise RuntimeError("not used in this test")

    app = create_app(
        repo,
        EventGraph(repo, settings=Settings(github_token="")),
        DailyReportGraph(repo, llm=HeuristicLLMAdapter(), settings=Settings(github_token="")),
        pr_review_graph=PRReviewGraph(repo, reviewer=PRSubagentReviewer(HeuristicLLMAdapter())),
        snapshot_ingestor=SnapshotIngestor(_DummyGitHub(), repo),
        settings=Settings(github_token=""),
    )

    client = TestClient(app)
    resp = client.get("/stats")

    assert resp.status_code == 200
    assert resp.json()["stats"]["deep_pr_reviews"] == 1
    repo.close()
