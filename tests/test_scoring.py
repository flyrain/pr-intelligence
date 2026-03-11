from __future__ import annotations

from datetime import datetime, timedelta, timezone

from polaris_pr_intel.config import Settings
from polaris_pr_intel.agents.review_need import ReviewNeedAgent
from polaris_pr_intel.models import PullRequestSnapshot
from polaris_pr_intel.scoring.rules import score_review_need


def _settings() -> Settings:
    return Settings(github_token="test-token")


def _pr(**overrides) -> PullRequestSnapshot:
    base = {
        "number": 101,
        "title": "Improve runtime planner",
        "body": "",
        "state": "open",
        "draft": False,
        "author": "alice",
        "labels": [],
        "requested_reviewers": [],
        "comments": 0,
        "review_comments": 0,
        "commits": 1,
        "changed_files": 3,
        "additions": 50,
        "deletions": 10,
        "html_url": "https://example.com/pr/101",
        "updated_at": datetime.now(timezone.utc) - timedelta(hours=2),
    }
    base.update(overrides)
    return PullRequestSnapshot.model_validate(base)


def test_score_review_need_stale_requested_and_large_diff() -> None:
    pr = _pr(
        updated_at=datetime.now(timezone.utc) - timedelta(hours=80),
        requested_reviewers=["bob"],
        additions=700,
        deletions=250,
        changed_files=30,
    )
    score, reasons = score_review_need(pr, settings=_settings())

    assert score == 7.5
    assert reasons == [
        "stale-over-24h",
        "stale-over-72h",
        "reviewers-requested",
        "large-diff",
        "many-files",
    ]


def test_score_review_need_draft_short_circuit() -> None:
    pr = _pr(draft=True, updated_at=datetime.now(timezone.utc) - timedelta(hours=100))
    score, reasons = score_review_need(pr, settings=_settings())
    assert score == 0.1
    assert reasons == ["draft-pr"]


def test_review_need_includes_requested_you_even_below_threshold() -> None:
    settings = Settings(github_token="test-token", review_needed_threshold=10.0, review_target_login="alice")
    agent = ReviewNeedAgent(settings)
    pr = _pr(requested_reviewers=["alice"], additions=5, deletions=1, changed_files=1)

    signal = agent.run(pr)

    assert signal.needs_review is True
    assert "requested-you" in signal.reasons


def test_review_need_does_not_force_when_requested_reviewer_is_different() -> None:
    settings = Settings(github_token="test-token", review_needed_threshold=10.0, review_target_login="alice")
    agent = ReviewNeedAgent(settings)
    pr = _pr(requested_reviewers=["bob"], additions=5, deletions=1, changed_files=1)

    signal = agent.run(pr)

    assert signal.needs_review is False
    assert "requested-you" not in signal.reasons
