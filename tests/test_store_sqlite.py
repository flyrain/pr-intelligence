from __future__ import annotations

from datetime import datetime, timezone

from polaris_pr_intel.models import AnalysisRun, PullRequestSnapshot, ReportArtifact
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
    repo.scheduled_refresh_attempted_at = datetime(2026, 3, 10, 2, 0, 0, tzinfo=timezone.utc)
    repo.scheduled_refresh_succeeded_at = datetime(2026, 3, 10, 2, 0, 30, tzinfo=timezone.utc)
    repo.scheduled_refresh_failed_at = datetime(2026, 3, 9, 23, 59, 0, tzinfo=timezone.utc)
    repo.scheduled_refresh_last_error = "RuntimeError: example failure"
    repo.close()

    repo2 = SQLiteRepository(str(db_path))
    assert 7 in repo2.prs
    assert repo2.latest_analysis_run() is not None
    assert repo2.has_processed_event("evt-7")
    assert repo2.last_sync_at is not None
    assert repo2.scheduled_refresh_attempted_at == datetime(2026, 3, 10, 2, 0, 0, tzinfo=timezone.utc)
    assert repo2.scheduled_refresh_succeeded_at == datetime(2026, 3, 10, 2, 0, 30, tzinfo=timezone.utc)
    assert repo2.scheduled_refresh_failed_at == datetime(2026, 3, 9, 23, 59, 0, tzinfo=timezone.utc)
    assert repo2.scheduled_refresh_last_error == "RuntimeError: example failure"
    repo2.close()
