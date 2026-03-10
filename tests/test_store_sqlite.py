from __future__ import annotations

from datetime import datetime, timezone

from polaris_pr_intel.models import DailyReport, PullRequestSnapshot
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
    repo.save_daily_report(DailyReport(date="2026-03-10", markdown="# Report"))
    repo.mark_processed_event("evt-7")
    repo.last_sync_at = datetime(2026, 3, 10, 1, 2, 3, tzinfo=timezone.utc)
    repo.close()

    repo2 = SQLiteRepository(str(db_path))
    assert 7 in repo2.prs
    assert repo2.latest_daily_report() is not None
    assert repo2.has_processed_event("evt-7")
    assert repo2.last_sync_at is not None
    repo2.close()
