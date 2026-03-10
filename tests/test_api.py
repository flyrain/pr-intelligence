from __future__ import annotations

from datetime import datetime, timezone
import time

from fastapi.testclient import TestClient

from polaris_pr_intel.api.app import create_app
from polaris_pr_intel.models import DailyReport, GitHubEvent, IssueSignal, PRReviewReport, PRSubagentFinding, PullRequestSnapshot, ReviewSignal
from polaris_pr_intel.store.repository import InMemoryRepository


class _DummyEventGraph:
    def invoke(self, event: GitHubEvent) -> dict:
        return {"notifications": [f"processed:{event.event_type}"]}


class _DummyDailyGraph:
    def invoke(self) -> dict:
        return {"notifications": ["daily-report"]}


class _DummyIngestor:
    def __init__(self) -> None:
        self.calls: list[dict] = []
        self.pr_calls: list[int] = []

    def sync_recent(self, per_page: int = 30, max_pages: int = 1, since: str | None = None) -> dict[str, int]:
        self.calls.append({"per_page": per_page, "max_pages": max_pages, "since": since})
        return {"prs": per_page * max_pages, "issues": 1 if since else 0}

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


def _client() -> tuple[TestClient, InMemoryRepository, _DummyIngestor, _DummyPRReviewGraph]:
    repo = InMemoryRepository()
    ingestor = _DummyIngestor()
    pr_review_graph = _DummyPRReviewGraph(repo)
    app = create_app(repo, _DummyEventGraph(), _DummyDailyGraph(), pr_review_graph=pr_review_graph, snapshot_ingestor=ingestor)
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


def test_daily_report_list_supports_limit_and_offset() -> None:
    client, repo, _, _ = _client()
    repo.save_daily_report(DailyReport(date="2026-03-08", markdown="old"))
    repo.save_daily_report(DailyReport(date="2026-03-09", markdown="newer"))
    repo.save_daily_report(DailyReport(date="2026-03-10", markdown="newest"))

    resp = client.get("/reports/daily", params={"limit": 2, "offset": 1})
    data = resp.json()

    assert resp.status_code == 200
    assert data["ok"] is True
    assert [item["date"] for item in data["reports"]] == ["2026-03-09", "2026-03-08"]


def test_root_and_stats_endpoints_return_useful_summary() -> None:
    client, repo, _, _ = _client()
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
    client, _, ingestor, _ = _client()
    resp = client.post("/reports/daily/run")
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert data["synced"]["prs"] == 2000
    assert ingestor.calls[0] == {"per_page": 100, "max_pages": 20, "since": None}


def test_sync_all_open_endpoint() -> None:
    client, _, ingestor, _ = _client()
    resp = client.post("/sync/all-open", params={"per_page": 50, "max_pages": 3})
    assert resp.status_code == 200
    assert resp.json()["synced"]["prs"] == 150
    assert ingestor.calls[0] == {"per_page": 50, "max_pages": 3, "since": None}


def test_ui_endpoint_renders_dashboard() -> None:
    client, repo, _, _ = _client()
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
    assert "Latest Report" in resp.text
    assert "PRs Needing Review" in resp.text
    assert "Deep Review Details" in resp.text
    assert "Review Jobs" in resp.text
    assert "Sync All Open PRs/Issues" in resp.text
    assert "New/Updated PRs Today" in resp.text
    assert "Aging Open PRs (72h+)" in resp.text
    assert "Run Review" in resp.text
    assert resp.text.count("New/Updated PRs Today") == 1
    assert resp.text.index("New/Updated PRs Today") < resp.text.index("Aging Open PRs (72h+)")


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


def test_run_open_pr_reviews_endpoint() -> None:
    client, repo, _, pr_review_graph = _client()
    repo.upsert_pr(
        PullRequestSnapshot(
            number=10,
            title="A",
            body="",
            state="open",
            draft=False,
            author="a",
            labels=[],
            requested_reviewers=[],
            comments=0,
            review_comments=0,
            commits=1,
            changed_files=1,
            additions=1,
            deletions=1,
            html_url="https://example.com/pr/10",
            updated_at=datetime.now(timezone.utc),
        )
    )
    repo.upsert_pr(
        PullRequestSnapshot(
            number=11,
            title="B",
            body="",
            state="open",
            draft=False,
            author="b",
            labels=[],
            requested_reviewers=[],
            comments=0,
            review_comments=0,
            commits=1,
            changed_files=1,
            additions=1,
            deletions=1,
            html_url="https://example.com/pr/11",
            updated_at=datetime.now(timezone.utc),
        )
    )

    resp = client.post("/reviews/run-open", params={"limit": 2})
    assert resp.status_code == 200
    assert resp.json()["total"] == 2
    assert len(resp.json()["reviewed"]) == 2
    assert pr_review_graph.calls


def test_pr_review_returns_not_found_when_fetch_fails() -> None:
    client, _, ingestor, _ = _client()
    ingestor.sync_pr = lambda pr_number: False  # type: ignore[method-assign]
    resp = client.post("/reviews/pr/999/run", params={"wait": True})
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is False
    assert data["errors"] == ["pr-not-found:999"]
