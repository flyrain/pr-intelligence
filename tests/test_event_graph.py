from __future__ import annotations

from polaris_pr_intel.config import Settings
from polaris_pr_intel.graphs.daily_report_graph import DailyReportGraph
from polaris_pr_intel.graphs.event_graph import EventGraph
from polaris_pr_intel.graphs.pr_review_graph import PRReviewGraph
from polaris_pr_intel.llm.adapters import HeuristicLLMAdapter
from polaris_pr_intel.models import GitHubEvent
from polaris_pr_intel.store.repository import InMemoryRepository
from polaris_pr_intel.agents.pr_reviewer import PRSubagentReviewer


def _settings() -> Settings:
    return Settings(github_token="token")


def test_pull_request_review_event_routes_like_pr() -> None:
    repo = InMemoryRepository()
    graph = EventGraph(repo, settings=_settings())
    event = GitHubEvent(
        event_type="pull_request_review",
        action="submitted",
        payload={
            "pull_request": {
                "number": 44,
                "title": "Planner cleanup",
                "body": "",
                "state": "open",
                "draft": False,
                "user": {"login": "alice"},
                "labels": [],
                "requested_reviewers": [{"login": "bob"}],
                "comments": 1,
                "review_comments": 2,
                "commits": 3,
                "changed_files": 10,
                "additions": 100,
                "deletions": 50,
                "html_url": "https://example.com/pr/44",
                "updated_at": "2026-03-09T00:00:00Z",
            }
        },
    )

    out = graph.invoke(event)

    assert "ingested-pr:44" in out["notifications"]
    assert repo.prs[44].number == 44
    assert repo.pr_summaries[44].pr_number == 44
    assert repo.review_signals[44].pr_number == 44


def test_issue_comment_on_pr_is_ignored_for_issue_queue() -> None:
    repo = InMemoryRepository()
    graph = EventGraph(repo, settings=_settings())
    event = GitHubEvent(
        event_type="issue_comment",
        action="created",
        payload={
            "issue": {
                "number": 88,
                "title": "PR discussion",
                "pull_request": {"url": "https://api.github.com/repos/x/y/pulls/88"},
                "updated_at": "2026-03-09T00:00:00Z",
            }
        },
    )

    out = graph.invoke(event)

    assert out["notifications"] == ["ignored-pr-comment-event"]
    assert repo.issue_signals == {}


def test_daily_report_graph_runs_with_empty_repo() -> None:
    repo = InMemoryRepository()
    graph = DailyReportGraph(repo)

    out = graph.invoke()

    assert out["notifications"][0].startswith("daily-report:")
    assert repo.latest_daily_report() is not None


def test_pr_review_graph_creates_report_for_existing_pr() -> None:
    repo = InMemoryRepository()
    event_graph = EventGraph(repo, settings=_settings())
    event_graph.invoke(
        GitHubEvent(
            event_type="pull_request",
            action="opened",
            payload={
                "pull_request": {
                    "number": 51,
                    "title": "Security hardening for access checks",
                    "body": "Adds stricter permission validation.",
                    "state": "open",
                    "draft": False,
                    "user": {"login": "alice"},
                    "labels": [],
                    "requested_reviewers": [],
                    "comments": 0,
                    "review_comments": 0,
                    "commits": 4,
                    "changed_files": 5,
                    "additions": 120,
                    "deletions": 40,
                    "html_url": "https://example.com/pr/51",
                    "updated_at": "2026-03-09T00:00:00Z",
                }
            },
        )
    )
    reviewer = PRSubagentReviewer(HeuristicLLMAdapter())
    graph = PRReviewGraph(repo, reviewer)
    out = graph.invoke(51)

    assert out["notifications"] == ["pr-review:51"]
    report = repo.latest_pr_review_report(51)
    assert report is not None
    assert report.pr_number == 51
    assert len(report.findings) == 4
