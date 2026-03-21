from __future__ import annotations

from datetime import datetime, timezone
import time

from fastapi.testclient import TestClient

from polaris_pr_intel.api.app import create_app
from polaris_pr_intel.config import Settings
from polaris_pr_intel.models import AnalysisItem, AnalysisRun, DailyReport, GitHubEvent, IssueSignal, PRReviewReport, PRSubagentFinding, PullRequestSnapshot, ReportArtifact, ReviewSignal
from polaris_pr_intel.store.repository import InMemoryRepository


class _DummyEventGraph:
    def invoke(self, event: GitHubEvent) -> dict:
        return {"notifications": [f"processed:{event.event_type}"]}


class _DummyDailyGraph:
    def __init__(self, repo: InMemoryRepository) -> None:
        self.repo = repo

    def invoke(self) -> dict:
        needs_review = sum(1 for signal in self.repo.review_signals.values() if signal.needs_review)
        run = AnalysisRun(
            artifacts=[ReportArtifact(name="executive-summary", title="Executive Summary", markdown="# Executive Summary\n\n- ok")],
            catalog_counts={"needs-review": needs_review},
        )
        report = DailyReport(date="2026-03-10", markdown="# Executive Summary\n\n- ok")
        self.repo.save_analysis_run(run)
        self.repo.save_daily_report(report)
        return {"notifications": ["daily-report"], "analysis_run": run, "daily_report": report}


class _DummyIngestor:
    def __init__(self) -> None:
        self.calls: list[dict] = []
        self.pr_calls: list[int] = []

    def sync_recent(
        self,
        per_page: int = 30,
        max_pages: int = 1,
        since: str | None = None,
        prune_missing_open_prs: bool = False,
    ) -> dict[str, int]:
        self.calls.append(
            {
                "per_page": per_page,
                "max_pages": max_pages,
                "since": since,
                "prune_missing_open_prs": prune_missing_open_prs,
            }
        )
        return {"prs": per_page * max_pages, "issues": 1 if since else 0, "closed_prs_marked": 0}

    def sync_pr(self, pr_number: int) -> bool:
        self.pr_calls.append(pr_number)
        return True


class _DummyPRReviewGraph:
    def __init__(self, repo: InMemoryRepository) -> None:
        self.repo = repo
        self.calls: list[int] = []

    def invoke(self, pr_number: int) -> dict:
        self.calls.append(pr_number)
        report = PRReviewReport(
            pr_number=pr_number,
            provider="heuristic",
            model="local-heuristic",
            findings=[
                PRSubagentFinding(
                    agent_name="code-risk",
                    focus_area="code risk and complexity",
                    verdict="medium",
                    score=0.6,
                    summary="moderate complexity",
                    recommendations=["Review changed modules in smaller chunks."],
                    confidence=0.7,
                )
            ],
            overall_priority=0.6,
            overall_recommendation="Schedule targeted review this cycle and validate critical areas first.",
        )
        self.repo.save_pr_review_report(report)
        return {"notifications": [f"pr-review:{pr_number}"], "errors": []}


def _client(settings: Settings | None = None) -> tuple[TestClient, InMemoryRepository, _DummyIngestor, _DummyPRReviewGraph]:
    repo = InMemoryRepository()
    ingestor = _DummyIngestor()
    pr_review_graph = _DummyPRReviewGraph(repo)
    app = create_app(
        repo,
        _DummyEventGraph(),
        _DummyDailyGraph(repo),
        pr_review_graph=pr_review_graph,
        snapshot_ingestor=ingestor,
        settings=settings or Settings(github_token=""),
    )
    return TestClient(app), repo, ingestor, pr_review_graph


def test_github_webhook_deduplicates_delivery_id() -> None:
    client, _, _, _ = _client()
    payload = {
        "action": "opened",
        "pull_request": {
            "number": 12,
            "title": "Add planner fix",
            "updated_at": "2026-03-10T00:00:00Z",
        },
    }
    headers = {"X-GitHub-Event": "pull_request", "X-GitHub-Delivery": "evt-1"}

    first = client.post("/webhooks/github", json=payload, headers=headers)
    second = client.post("/webhooks/github", json=payload, headers=headers)

    assert first.status_code == 200
    assert first.json()["ok"] is True
    assert second.status_code == 200
    assert second.json() == {"ok": True, "duplicate": True, "notifications": ["ignored-duplicate"]}


def test_root_and_stats_endpoints_return_useful_summary() -> None:
    client, repo, _, _ = _client()
    repo.upsert_pr(
        PullRequestSnapshot(
            number=1,
            title="Needs review",
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
            html_url="https://example.com/pr/1",
            updated_at=datetime.now(timezone.utc),
        )
    )
    repo.save_review_signal(ReviewSignal(pr_number=1, score=3.0, reasons=["reviewers-requested"], needs_review=True))
    repo.save_issue_signal(IssueSignal(issue_number=2, score=2.5, reasons=["label:bug"], interesting=True))
    repo.save_daily_report(DailyReport(date="2026-03-10", markdown="report"))

    root = client.get("/")
    stats = client.get("/stats")

    assert root.status_code == 200
    root_data = root.json()
    assert root_data["service"] == "Polaris PR Intelligence"
    assert root_data["status"] == "ok"
    assert root_data["stats"]["needs_review_queue"] == 1
    assert root_data["links"]["docs"] == "/docs"

    assert stats.status_code == 200
    stats_data = stats.json()
    assert stats_data["ok"] is True
    assert stats_data["stats"]["interesting_issues_queue"] == 1


def test_needs_review_filters_to_target_login_when_configured(monkeypatch) -> None:
    monkeypatch.setenv("REVIEW_TARGET_LOGIN", "alice")
    client, repo, _, _ = _client(Settings(github_token="", review_target_login="alice"))
    now = datetime.now(timezone.utc)
    repo.upsert_pr(
        PullRequestSnapshot(
            number=1,
            title="Mine",
            body="",
            state="open",
            draft=False,
            author="x",
            labels=[],
            requested_reviewers=["alice"],
            comments=0,
            review_comments=0,
            commits=1,
            changed_files=1,
            additions=1,
            deletions=1,
            html_url="https://example.com/pr/1",
            updated_at=now,
        )
    )
    repo.upsert_pr(
        PullRequestSnapshot(
            number=2,
            title="Others",
            body="",
            state="open",
            draft=False,
            author="x",
            labels=[],
            requested_reviewers=["bob"],
            comments=0,
            review_comments=0,
            commits=1,
            changed_files=1,
            additions=1,
            deletions=1,
            html_url="https://example.com/pr/2",
            updated_at=now,
        )
    )
    repo.save_review_signal(ReviewSignal(pr_number=1, score=3.0, reasons=["requested-you"], needs_review=True))
    repo.save_review_signal(ReviewSignal(pr_number=2, score=3.0, reasons=["reviewers-requested"], needs_review=True))

    queued = client.get("/queues/needs-review")
    assert queued.status_code == 200
    data = queued.json()
    assert [item["number"] for item in data] == [1]

    stats = client.get("/stats").json()
    assert stats["stats"]["needs_review_queue"] == 1


def test_needs_review_excludes_closed_prs() -> None:
    client, repo, _, _ = _client()
    now = datetime.now(timezone.utc)
    repo.upsert_pr(
        PullRequestSnapshot(
            number=10,
            title="Closed PR",
            body="",
            state="closed",
            draft=False,
            author="alice",
            labels=[],
            requested_reviewers=["alice"],
            comments=0,
            review_comments=0,
            commits=1,
            changed_files=1,
            additions=1,
            deletions=1,
            html_url="https://example.com/pr/10",
            updated_at=now,
        )
    )
    repo.save_review_signal(ReviewSignal(pr_number=10, score=5.0, reasons=["requested-you"], needs_review=True))

    queued = client.get("/queues/needs-review")
    assert queued.status_code == 200
    assert queued.json() == []

    stats = client.get("/stats").json()
    assert stats["stats"]["needs_review_queue"] == 0


def test_latest_report_markdown_endpoint() -> None:
    client, repo, _, _ = _client()
    empty = client.get("/reports/daily/latest.md")
    assert empty.status_code == 200
    assert "No report has been generated yet." in empty.text

    repo.save_daily_report(DailyReport(date="2026-03-10", markdown="# Report\n\nHello"))
    filled = client.get("/reports/daily/latest.md")
    assert filled.status_code == 200
    assert filled.text == "# Report\n\nHello"


def test_run_daily_report_refreshes_by_default() -> None:
    """Test that refresh endpoint (replacing reports/daily/run) works correctly."""
    client, _, ingestor, _ = _client()
    resp = client.post("/refresh")
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert data["synced"]["prs"] == 2000
    assert data["scored"] == {"prs": 0, "issues": 0, "needs_review": 0, "interesting_issues": 0}
    assert data["analysis_run"]["artifacts"][0]["name"] == "executive-summary"
    assert data["report"]["date"] == "2026-03-10"
    assert ingestor.calls[0] == {"per_page": 100, "max_pages": 20, "since": None, "prune_missing_open_prs": True}


def test_analysis_run_endpoint_returns_catalogs_and_report() -> None:
    """Test that refresh endpoint (replacing analysis/run) works correctly."""
    client, repo, _, _ = _client()
    now = datetime.now(timezone.utc)
    repo.upsert_pr(
        PullRequestSnapshot(
            number=12,
            title="Security hardening",
            body="permissions update",
            state="open",
            draft=False,
            author="alice",
            labels=["security"],
            requested_reviewers=["bob"],
            comments=0,
            review_comments=0,
            commits=2,
            changed_files=4,
            additions=80,
            deletions=10,
            html_url="https://example.com/pr/12",
            updated_at=now,
        )
    )
    repo.save_review_signal(ReviewSignal(pr_number=12, score=3.0, reasons=["reviewers-requested"], needs_review=True))

    resp = client.post("/refresh")

    assert resp.status_code == 200
    payload = resp.json()
    assert payload["ok"] is True
    assert payload["analysis_run"]["catalog_counts"]["needs-review"] >= 1
    assert payload["report"]["markdown"].startswith("# Executive Summary")


def test_scores_recompute_endpoint_populates_queues() -> None:
    """Test that refresh endpoint (replacing scores/recompute) populates queues."""
    client, repo, _, _ = _client()
    repo.upsert_pr(
        PullRequestSnapshot(
            number=77,
            title="Large auth update",
            body="security",
            state="open",
            draft=False,
            author="alice",
            labels=[],
            requested_reviewers=["bob"],
            comments=0,
            review_comments=0,
            commits=5,
            changed_files=30,
            additions=900,
            deletions=200,
            html_url="https://example.com/pr/77",
            updated_at=datetime.now(timezone.utc),
        )
    )
    from polaris_pr_intel.models import IssueSnapshot

    repo.upsert_issue(
        IssueSnapshot(
            number=88,
            title="Bug in planner",
            body="",
            state="open",
            author="alice",
            labels=["bug"],
            comments=6,
            assignees=[],
            html_url="https://example.com/issues/88",
            updated_at=datetime.now(timezone.utc),
        )
    )

    resp = client.post("/refresh")
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["ok"] is True
    assert payload["scored"]["prs"] == 1
    assert payload["scored"]["issues"] == 1
    assert payload["scored"]["needs_review"] >= 1
    assert payload["scored"]["interesting_issues"] >= 1

    needs_review = client.get("/queues/needs-review")
    assert needs_review.status_code == 200
    assert needs_review.json()[0]["number"] == 77


def test_ui_endpoint_renders_dashboard() -> None:
    client, repo, _, _ = _client(Settings(github_token="", llm_provider="codex_local", llm_model="gpt-5-codex"))
    repo.save_daily_report(
        DailyReport(
            date="2026-03-10",
            markdown="# Polaris PR Intelligence Report\n\n## PRs Needing Review\n- none\n\n## Aging Open PRs (72h+)\n- [#1](https://example.com/pr/1) old PR | age=100h\n\n## New/Updated PRs Today\n- [#99](https://example.com/pr/99) UI wiring | updated=2026-03-10T00:00:00+00:00",
        )
    )
    repo.upsert_pr(
        PullRequestSnapshot(
            number=99,
            title="UI wiring",
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
            additions=3,
            deletions=1,
            html_url="https://example.com/pr/99",
            updated_at=datetime.now(timezone.utc),
        )
    )

    resp = client.get("/ui")

    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]
    assert "Polaris PR Intelligence" in resp.text
    assert "LLM Provider: codex_local / gpt-5-codex" in resp.text
    assert "Latest Report" in resp.text
    assert "PRs Needing Review" in resp.text
    assert "Deep PR Reviews" in resp.text
    assert "No deep reviews yet." in resp.text
    assert "Review Jobs" in resp.text
    assert '>Sync</button>' in resp.text
    assert 'fetch("/refresh' in resp.text
    assert '<details class="tab-fold">' in resp.text
    assert '<details class="tab-fold" open>' not in resp.text
    assert '<details class="queue-section">' in resp.text
    assert '<details class="queue-section" open>' not in resp.text
    assert "New/Updated PRs Today" in resp.text
    assert "Aging Open PRs (72h+)" in resp.text
    assert "Review" in resp.text
    assert resp.text.count("New/Updated PRs Today") >= 1
    assert resp.text.index("<summary>New/Updated PRs Today</summary>") < resp.text.index("<summary>Latest Report</summary>")


def test_ui_new_updated_prs_folds_after_first_ten() -> None:
    client, repo, _, _ = _client()
    now = datetime.now(timezone.utc)
    for pr_number in range(100, 112):
        repo.upsert_pr(
            PullRequestSnapshot(
                number=pr_number,
                title=f"PR {pr_number}",
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
                additions=3,
                deletions=1,
                html_url=f"https://example.com/pr/{pr_number}",
                updated_at=now,
            )
        )

    resp = client.get("/ui")

    assert resp.status_code == 200
    assert '<details class="folded-section">' in resp.text
    assert "Show 2 more PRs" in resp.text


def test_ui_new_updated_prs_excludes_closed_prs() -> None:
    client, repo, _, _ = _client()
    now = datetime.now(timezone.utc)
    repo.upsert_pr(
        PullRequestSnapshot(
            number=901,
            title="Open PR",
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
            html_url="https://example.com/pr/901",
            updated_at=now,
        )
    )
    repo.upsert_pr(
        PullRequestSnapshot(
            number=902,
            title="Merged PR",
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
            html_url="https://example.com/pr/902",
            updated_at=now,
        )
    )

    resp = client.get("/ui")
    assert resp.status_code == 200
    assert "Open PR" in resp.text
    assert "Merged PR" not in resp.text


def test_ui_deep_reviews_ordered_by_review_time_desc() -> None:
    client, repo, _, _ = _client()
    now = datetime.now(timezone.utc)
    repo.upsert_pr(
        PullRequestSnapshot(
            number=200,
            title="Older deep review",
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
            additions=3,
            deletions=1,
            html_url="https://example.com/pr/200",
            updated_at=now,
        )
    )
    repo.upsert_pr(
        PullRequestSnapshot(
            number=201,
            title="Newer deep review",
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
            additions=3,
            deletions=1,
            html_url="https://example.com/pr/201",
            updated_at=now,
        )
    )
    repo.save_pr_review_report(
        PRReviewReport(
            pr_number=200,
            generated_at=datetime(2026, 3, 10, 10, 0, tzinfo=timezone.utc),
            provider="codex_local",
            model="gpt-5-codex",
            overall_priority=0.99,
            overall_recommendation="older",
        )
    )
    repo.save_pr_review_report(
        PRReviewReport(
            pr_number=201,
            generated_at=datetime(2026, 3, 11, 10, 0, tzinfo=timezone.utc),
            provider="claude_code_local",
            model="claude-code-local",
            overall_priority=0.10,
            overall_recommendation="newer",
        )
    )

    resp = client.get("/ui")
    assert resp.status_code == 200
    assert resp.text.index("priority=0.10") < resp.text.index("priority=0.99")


def test_ui_needs_review_folds_after_first_ten() -> None:
    client, repo, _, _ = _client()
    now = datetime.now(timezone.utc)
    for pr_number in range(200, 212):
        repo.upsert_pr(
            PullRequestSnapshot(
                number=pr_number,
                title=f"Needs review PR {pr_number}",
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
                additions=3,
                deletions=1,
                html_url=f"https://example.com/pr/{pr_number}",
                updated_at=now,
            )
        )
        repo.save_review_signal(
            ReviewSignal(
                pr_number=pr_number,
                score=10.0 - ((pr_number - 200) * 0.1),
                reasons=["reviewers-requested"],
                needs_review=True,
            )
        )

    resp = client.get("/ui")
    assert resp.status_code == 200
    assert resp.text.count("Show 2 more PRs") >= 1


def test_ui_interesting_issues_folds_after_first_ten() -> None:
    client, repo, _, _ = _client()
    now = datetime.now(timezone.utc)
    from polaris_pr_intel.models import IssueSnapshot

    for issue_number in range(300, 312):
        repo.upsert_issue(
            IssueSnapshot(
                number=issue_number,
                title=f"Interesting issue {issue_number}",
                body="",
                state="open",
                author="alice",
                labels=["bug"],
                comments=6,
                assignees=[],
                html_url=f"https://example.com/issues/{issue_number}",
                updated_at=now,
            )
        )
        repo.save_issue_signal(
            IssueSignal(
                issue_number=issue_number,
                score=10.0 - ((issue_number - 300) * 0.1),
                reasons=["label:bug", "high-discussion", "open"],
                interesting=True,
            )
        )

    resp = client.get("/ui")
    assert resp.status_code == 200
    assert "Show 2 more issues" in resp.text


def test_pr_review_endpoints() -> None:
    client, _, ingestor, pr_review_graph = _client()
    run = client.post("/reviews/pr/123/run", params={"wait": True})
    assert run.status_code == 200
    run_data = run.json()
    assert run_data["ok"] is True
    assert run_data["mode"] == "sync"
    assert run_data["report"]["pr_number"] == 123
    assert pr_review_graph.calls == [123]
    assert ingestor.pr_calls == [123]

    latest = client.get("/reviews/pr/123/latest")
    assert latest.status_code == 200
    assert latest.json()["report"]["provider"] == "heuristic"

    top = client.get("/reviews/pr/top")
    assert top.status_code == 200
    assert top.json()["reports"][0]["pr_number"] == 123


def test_pr_review_async_job_mode() -> None:
    client, _, _, pr_review_graph = _client()
    run = client.post("/reviews/pr/456/run")
    assert run.status_code == 200
    body = run.json()
    assert body["ok"] is True
    assert body["accepted"] is True
    assert body["deduplicated"] is False
    assert body["mode"] == "async"
    job_id = body["job_id"]

    final = None
    for _ in range(30):
        status = client.get(f"/reviews/jobs/{job_id}")
        assert status.status_code == 200
        payload = status.json()
        assert payload["ok"] is True
        if payload["job"]["status"] in {"completed", "failed"}:
            final = payload["job"]
            break
        time.sleep(0.01)

    assert final is not None
    assert final["status"] == "completed"
    assert final["result"]["report"]["pr_number"] == 456
    assert pr_review_graph.calls
    by_pr = client.get("/reviews/pr/456/job")
    assert by_pr.status_code == 200
    by_pr_data = by_pr.json()
    assert by_pr_data["ok"] is True
    assert by_pr_data["job"]["job_id"] == job_id
    ui = client.get("/ui")
    assert ui.status_code == 200
    assert "Review Jobs" in ui.text
    assert job_id in ui.text


def test_pr_review_async_deduplicates_same_pr_when_job_inflight(monkeypatch) -> None:
    monkeypatch.setenv("REVIEW_JOB_WORKERS", "1")
    client, _, _, pr_review_graph = _client()

    def _slow_invoke(pr_number: int) -> dict:
        time.sleep(0.2)
        return {"notifications": [f"pr-review:{pr_number}"], "errors": []}

    pr_review_graph.invoke = _slow_invoke  # type: ignore[method-assign]
    first = client.post("/reviews/pr/777/run")
    second = client.post("/reviews/pr/777/run")
    assert first.status_code == 200
    assert second.status_code == 200
    first_body = first.json()
    second_body = second.json()
    assert first_body["deduplicated"] is False
    assert second_body["deduplicated"] is True
    assert second_body["job_id"] == first_body["job_id"]


def test_pr_review_async_different_prs_get_distinct_jobs() -> None:
    client, _, _, _ = _client()
    first = client.post("/reviews/pr/881/run")
    second = client.post("/reviews/pr/882/run")
    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json()["job_id"] != second.json()["job_id"]


def test_pr_review_async_jobs_queue_with_single_worker(monkeypatch) -> None:
    monkeypatch.setenv("REVIEW_JOB_WORKERS", "1")
    client, _, _, pr_review_graph = _client()

    def _slow_invoke(pr_number: int) -> dict:
        time.sleep(0.15)
        return {"notifications": [f"pr-review:{pr_number}"], "errors": []}

    pr_review_graph.invoke = _slow_invoke  # type: ignore[method-assign]
    first = client.post("/reviews/pr/701/run")
    second = client.post("/reviews/pr/702/run")
    assert first.status_code == 200
    assert second.status_code == 200
    first_id = first.json()["job_id"]
    second_id = second.json()["job_id"]

    # With one worker, second job should remain queued while first is running.
    seen = set()
    for _ in range(30):
        first_status = client.get(f"/reviews/jobs/{first_id}").json()["job"]["status"]
        second_status = client.get(f"/reviews/jobs/{second_id}").json()["job"]["status"]
        seen.add((first_status, second_status))
        if (first_status, second_status) in {("running", "queued"), ("completed", "running")}:
            break
        time.sleep(0.01)

    assert ("running", "queued") in seen or ("completed", "running") in seen


def test_pr_review_job_auto_expires_stuck_running(monkeypatch) -> None:
    monkeypatch.setenv("REVIEW_JOB_TIMEOUT_SEC", "0")
    client, _, _, pr_review_graph = _client()

    def _slow_invoke(pr_number: int) -> dict:
        time.sleep(0.2)
        return {"notifications": [f"pr-review:{pr_number}"], "errors": []}

    pr_review_graph.invoke = _slow_invoke  # type: ignore[method-assign]

    run = client.post("/reviews/pr/321/run")
    assert run.status_code == 200
    job_id = run.json()["job_id"]

    status = client.get(f"/reviews/jobs/{job_id}")
    assert status.status_code == 200
    body = status.json()
    assert body["ok"] is True
    assert body["job"]["status"] in {"running", "failed"}
    if body["job"]["status"] == "running":
        time.sleep(0.05)
        body = client.get(f"/reviews/jobs/{job_id}").json()
    assert body["job"]["status"] == "failed"
    assert body["job"]["result"]["errors"] == ["job-timeout:0s"]


def test_pr_review_returns_not_found_when_fetch_fails() -> None:
    client, _, ingestor, _ = _client()
    ingestor.sync_pr = lambda pr_number: False  # type: ignore[method-assign]
    resp = client.post("/reviews/pr/999/run", params={"wait": True})
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is False
    assert data["errors"] == ["pr-not-found:999"]


def test_refresh_endpoint() -> None:
    """Test the unified /refresh endpoint."""
    client, repo, ingestor, _ = _client()
    now = datetime.now(timezone.utc)
    repo.upsert_pr(
        PullRequestSnapshot(
            number=400,
            title="Test PR",
            body="",
            state="open",
            draft=False,
            author="alice",
            labels=[],
            requested_reviewers=["bob"],
            comments=0,
            review_comments=0,
            commits=2,
            changed_files=4,
            additions=80,
            deletions=10,
            html_url="https://example.com/pr/400",
            updated_at=now,
        )
    )

    resp = client.post("/refresh")
    assert resp.status_code == 200

    data = resp.json()
    assert data["ok"] is True
    assert "synced" in data
    assert "scored" in data
    assert "analysis_run" in data
    assert "report" in data

    # Verify sync happened
    assert data["synced"]["prs"] == 2000

    # Verify scoring happened
    assert data["scored"]["prs"] == 1

    # Verify analysis happened
    assert data["analysis_run"] is not None
    assert data["analysis_run"]["catalog_counts"] is not None

    # Verify report happened
    assert data["report"] is not None
    assert data["report"]["markdown"] is not None

    # Verify ingestor was called correctly
    assert ingestor.calls[0] == {"per_page": 100, "max_pages": 20, "since": None, "prune_missing_open_prs": True}
