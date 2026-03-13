from __future__ import annotations

from datetime import datetime, timezone

from polaris_pr_intel.config import Settings
from polaris_pr_intel.graphs.daily_report_graph import DailyReportGraph
from polaris_pr_intel.graphs.event_graph import EventGraph
from polaris_pr_intel.graphs.pr_review_graph import PRReviewGraph
from polaris_pr_intel.llm.adapters import HeuristicLLMAdapter
from polaris_pr_intel.models import GitHubEvent, PRSubagentFinding, PullRequestSnapshot, ReviewSignal
from polaris_pr_intel.store.repository import InMemoryRepository
from polaris_pr_intel.agents.pr_reviewer import PRSubagentReviewer
from polaris_pr_intel.agents.derived_analysis import DerivedAnalysisAgent


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
    graph = DailyReportGraph(repo, llm=HeuristicLLMAdapter(), settings=_settings())

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


def test_daily_report_new_updated_prs_excludes_closed_prs() -> None:
    repo = InMemoryRepository()
    now = datetime.now(timezone.utc)
    repo.upsert_pr(
        PullRequestSnapshot(
            number=601,
            title="Open PR today",
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
            additions=1,
            deletions=1,
            html_url="https://example.com/pr/601",
            updated_at=now,
        )
    )
    repo.upsert_pr(
        PullRequestSnapshot(
            number=602,
            title="Merged PR today",
            body="",
            state="closed",
            draft=False,
            author="alice",
            labels=[],
            requested_reviewers=[],
            comments=0,
            review_comments=0,
            commits=1,
            changed_files=1,
            additions=1,
            deletions=1,
            html_url="https://example.com/pr/602",
            updated_at=now,
        )
    )

    graph = DailyReportGraph(repo, llm=HeuristicLLMAdapter(), settings=_settings())
    graph.invoke()
    report = repo.latest_daily_report()
    assert report is not None
    assert "Open PR today" in report.markdown
    assert "Merged PR today" not in report.markdown


def test_daily_report_analysis_preserves_requested_you_and_security_label_catalogs() -> None:
    repo = InMemoryRepository()
    settings = Settings(github_token="token", review_target_login="alice", review_needed_threshold=10.0)
    now = datetime.now(timezone.utc)
    repo.upsert_pr(
        PullRequestSnapshot(
            number=603,
            title="Planner cleanup",
            body="small change",
            state="open",
            draft=False,
            author="alice",
            labels=["security"],
            requested_reviewers=["alice"],
            comments=0,
            review_comments=0,
            commits=1,
            changed_files=1,
            additions=5,
            deletions=1,
            html_url="https://example.com/pr/603",
            updated_at=now,
        )
    )
    repo.save_review_signal(ReviewSignal(pr_number=603, score=0.0, reasons=["normal-priority", "requested-you"], needs_review=True))

    graph = DailyReportGraph(repo, llm=HeuristicLLMAdapter(), settings=settings)
    graph.invoke()

    run = repo.latest_analysis_run()
    assert run is not None
    item = next(entry for entry in run.items if entry.item_type == "pr" and entry.number == 603)
    assert "needs-review" in item.catalogs
    assert "security-risk" in item.catalogs


def test_derived_analysis_uses_catalog_routing_method() -> None:
    class _RecordingLLM(HeuristicLLMAdapter):
        def __init__(self) -> None:
            super().__init__()
            self.catalog_batch_calls: list[list[int]] = []
            self.review_calls: list[int] = []

        def analyze_pr(self, agent_name: str, focus_area: str, pr: PullRequestSnapshot):
            self.review_calls.append(pr.number)
            return super().analyze_pr(agent_name, focus_area, pr)

        def analyze_catalog_routing_batch(self, prs: list[PullRequestSnapshot]):
            self.catalog_batch_calls.append([pr.number for pr in prs])
            return {
                pr.number: PRSubagentFinding(
                    agent_name="catalog-router",
                    focus_area="catalog routing and prioritization",
                    verdict="medium",
                    score=0.6,
                    summary=f"route pr {pr.number}",
                    recommendations=["route to needs-review"],
                    suggested_catalogs=["needs-review"],
                    confidence=0.7,
                )
                for pr in prs
            }

    repo = InMemoryRepository()
    now = datetime.now(timezone.utc)
    repo.upsert_pr(
        PullRequestSnapshot(
            number=604,
            title="Security cleanup",
            body="permissions",
            state="open",
            draft=False,
            author="alice",
            labels=[],
            requested_reviewers=["bob"],
            comments=0,
            review_comments=0,
            commits=1,
            changed_files=1,
            additions=5,
            deletions=1,
            html_url="https://example.com/pr/604",
            updated_at=now,
        )
    )
    repo.save_review_signal(ReviewSignal(pr_number=604, score=3.0, reasons=["reviewers-requested"], needs_review=True))
    llm = _RecordingLLM()

    agent = DerivedAnalysisAgent(repo, llm=llm, settings=_settings())
    run, _ = agent.run()

    assert any(item.number == 604 for item in run.items)
    assert llm.catalog_batch_calls == [[604]]
    assert llm.review_calls == []
