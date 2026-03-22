from __future__ import annotations

from datetime import datetime, timedelta, timezone

from polaris_pr_intel.config import Settings
from polaris_pr_intel.graphs.daily_report_graph import DailyReportGraph
from polaris_pr_intel.graphs.event_graph import EventGraph
from polaris_pr_intel.graphs.pr_review_graph import PRReviewGraph
from polaris_pr_intel.llm.adapters import HeuristicLLMAdapter
from polaris_pr_intel.models import GitHubEvent, PRAttentionDecision, PullRequestSnapshot, ReviewSignal
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


def test_pr_review_graph_persists_blocked_report_when_diff_unavailable() -> None:
    class _FailingGitHubClient:
        def get_pull_request_diff(self, number: int) -> str:
            raise RuntimeError("sandbox blocked fetch")

    repo = InMemoryRepository()
    repo.upsert_pr(
        PullRequestSnapshot(
            number=91,
            title="Dispatcher wiring cleanup",
            body="",
            state="open",
            draft=False,
            author="alice",
            labels=[],
            requested_reviewers=[],
            comments=0,
            review_comments=0,
            commits=1,
            changed_files=3,
            additions=20,
            deletions=10,
            html_url="https://example.com/pr/91",
            updated_at=datetime.now(timezone.utc),
        )
    )
    reviewer = PRSubagentReviewer(HeuristicLLMAdapter())
    graph = PRReviewGraph(repo, reviewer, gh=_FailingGitHubClient())  # type: ignore[arg-type]

    out = graph.invoke(91)

    assert out["notifications"] == ["review-blocked", "pr-review:91"]
    assert out["errors"] == ["pr-diff-unavailable:91"]
    report = repo.latest_pr_review_report(91)
    assert report is not None
    assert report.blocked_reason
    assert report.findings == []


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


def test_derived_analysis_uses_attention_batch_method() -> None:
    class _RecordingLLM(HeuristicLLMAdapter):
        def __init__(self) -> None:
            super().__init__()
            self.attention_batch_calls: list[list[int]] = []
            self.review_calls: list[int] = []

        def analyze_pr(self, agent_name: str, focus_area: str, pr: PullRequestSnapshot):
            self.review_calls.append(pr.number)
            return super().analyze_pr(agent_name, focus_area, pr)

        def analyze_attention_batch(self, contexts):
            self.attention_batch_calls.append([ctx.pr_number for ctx in contexts])
            return {
                ctx.pr_number: PRAttentionDecision(
                    pr_number=ctx.pr_number,
                    needs_review=True,
                    priority_score=8.0,
                    priority_band="high",
                    priority_reason=f"prioritize pr {ctx.pr_number}",
                    tags=["active-discussion"],
                    suggested_catalogs=["needs-review", "recently-updated"],
                    confidence=0.7,
                )
                for ctx in contexts
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
    assert llm.attention_batch_calls == [[604]]
    assert llm.review_calls == []


def test_review_now_prefers_recent_unreviewed_prs_and_nudges_stale_ones() -> None:
    repo = InMemoryRepository()
    now = datetime.now(timezone.utc)
    recent = PullRequestSnapshot(
        number=701,
        title="Recent PR",
        body="",
        state="open",
        draft=False,
        author="alice",
        labels=[],
        requested_reviewers=["alice"],
        comments=0,
        review_comments=0,
        commits=1,
        changed_files=2,
        additions=20,
        deletions=5,
        html_url="https://example.com/pr/701",
        updated_at=now - timedelta(hours=4),
    )
    stale = PullRequestSnapshot(
        number=702,
        title="Stale PR",
        body="",
        state="open",
        draft=False,
        author="alice",
        labels=[],
        requested_reviewers=["alice"],
        comments=0,
        review_comments=0,
        commits=1,
        changed_files=2,
        additions=20,
        deletions=5,
        html_url="https://example.com/pr/702",
        updated_at=now - timedelta(hours=120),
    )
    repo.upsert_pr(recent)
    repo.upsert_pr(stale)
    repo.save_review_signal(ReviewSignal(pr_number=701, score=3.0, reasons=["requested-you"], needs_review=True))
    repo.save_review_signal(ReviewSignal(pr_number=702, score=4.0, reasons=["requested-you"], needs_review=True))

    graph = DailyReportGraph(repo, llm=HeuristicLLMAdapter(), settings=_settings())
    graph.invoke()

    report = repo.latest_daily_report()
    assert report is not None
    assert "Recent PR" in report.markdown
    assert "Stale PR" in report.markdown
    assert report.markdown.index("Recent PR") < report.markdown.index("## Aging PRs To Nudge")


def test_review_now_deprioritizes_already_reviewed_and_draft_prs() -> None:
    repo = InMemoryRepository()
    now = datetime.now(timezone.utc)
    already_reviewed = PullRequestSnapshot(
        number=703,
        title="Reviewed PR",
        body="",
        state="open",
        draft=False,
        author="alice",
        labels=[],
        requested_reviewers=["alice"],
        comments=0,
        review_comments=3,
        commits=1,
        changed_files=2,
        additions=20,
        deletions=5,
        html_url="https://example.com/pr/703",
        updated_at=now - timedelta(hours=2),
    )
    draft_pr = PullRequestSnapshot(
        number=704,
        title="Draft PR",
        body="",
        state="open",
        draft=True,
        author="alice",
        labels=[],
        requested_reviewers=["alice"],
        comments=0,
        review_comments=0,
        commits=1,
        changed_files=2,
        additions=20,
        deletions=5,
        html_url="https://example.com/pr/704",
        updated_at=now - timedelta(hours=1),
    )
    fresh = PullRequestSnapshot(
        number=705,
        title="Fresh PR",
        body="",
        state="open",
        draft=False,
        author="alice",
        labels=[],
        requested_reviewers=["alice"],
        comments=0,
        review_comments=0,
        commits=1,
        changed_files=2,
        additions=20,
        deletions=5,
        html_url="https://example.com/pr/705",
        updated_at=now - timedelta(hours=1),
    )
    for pr in (already_reviewed, draft_pr, fresh):
        repo.upsert_pr(pr)
    repo.save_review_signal(ReviewSignal(pr_number=703, score=4.0, reasons=["requested-you"], needs_review=True))
    repo.save_review_signal(ReviewSignal(pr_number=704, score=5.0, reasons=["requested-you"], needs_review=True))
    repo.save_review_signal(ReviewSignal(pr_number=705, score=3.0, reasons=["requested-you"], needs_review=True))

    graph = DailyReportGraph(repo, llm=HeuristicLLMAdapter(), settings=_settings())
    graph.invoke()

    report = repo.latest_daily_report()
    assert report is not None
    review_section = report.markdown.split("## Review Now", 1)[1].split("## Recently Updated PRs", 1)[0]
    assert "Fresh PR" in review_section
    assert "Draft PR" not in review_section
    assert "Reviewed PR" in review_section
    assert review_section.index("Fresh PR") < review_section.index("Reviewed PR")


def test_derived_analysis_includes_activity_velocity_note() -> None:
    repo = InMemoryRepository()
    now = datetime.now(timezone.utc)
    repo.upsert_pr(
        PullRequestSnapshot(
            number=706,
            title="Busy PR",
            body="",
            state="open",
            draft=False,
            author="alice",
            labels=[],
            requested_reviewers=["alice"],
            comments=5,
            review_comments=0,
            commits=1,
            changed_files=2,
            additions=20,
            deletions=5,
            activity_comments_24h=5,
            html_url="https://example.com/pr/706",
            updated_at=now - timedelta(hours=1),
        )
    )
    repo.save_review_signal(
        ReviewSignal(pr_number=706, score=3.0, reasons=["requested-you", "comments-24h:5"], needs_review=True)
    )

    graph = DailyReportGraph(repo, llm=HeuristicLLMAdapter(), settings=_settings())
    graph.invoke()

    report = repo.latest_daily_report()
    assert report is not None
    assert "5 comments in last 24h" in report.markdown


def test_derived_analysis_respects_needs_review_boolean_over_catalog_hint() -> None:
    class _LLM(HeuristicLLMAdapter):
        def analyze_attention_batch(self, contexts):
            return {
                contexts[0].pr_number: PRAttentionDecision(
                    pr_number=contexts[0].pr_number,
                    needs_review=False,
                    priority_score=4.0,
                    priority_band="defer",
                    priority_reason="defer for now",
                    suggested_catalogs=["needs-review", "aging-prs"],
                    confidence=0.8,
                )
            }

    repo = InMemoryRepository()
    now = datetime.now(timezone.utc)
    repo.upsert_pr(
        PullRequestSnapshot(
            number=707,
            title="Defer PR",
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
            additions=5,
            deletions=1,
            html_url="https://example.com/pr/707",
            updated_at=now - timedelta(days=5),
        )
    )

    graph = DailyReportGraph(repo, llm=_LLM(), settings=_settings())
    graph.invoke()

    run = repo.latest_analysis_run()
    assert run is not None
    item = next(entry for entry in run.items if entry.item_type == "pr" and entry.number == 707)
    assert "needs-review" not in item.catalogs
