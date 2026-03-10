from __future__ import annotations

from fastapi.testclient import TestClient

from polaris_pr_intel.api.app import create_app
from polaris_pr_intel.models import DailyReport, GitHubEvent, IssueSignal, ReviewSignal
from polaris_pr_intel.store.repository import InMemoryRepository


class _DummyEventGraph:
    def invoke(self, event: GitHubEvent) -> dict:
        return {"notifications": [f"processed:{event.event_type}"]}


class _DummyDailyGraph:
    def invoke(self) -> dict:
        return {"notifications": ["daily-report"]}


class _DummyIngestor:
    def sync_recent(self, per_page: int = 30, max_pages: int = 1, since: str | None = None) -> dict[str, int]:
        return {"prs": per_page * max_pages, "issues": 1 if since else 0}


def _client() -> tuple[TestClient, InMemoryRepository]:
    repo = InMemoryRepository()
    app = create_app(repo, _DummyEventGraph(), _DummyDailyGraph(), snapshot_ingestor=_DummyIngestor())
    return TestClient(app), repo


def test_github_webhook_deduplicates_delivery_id() -> None:
    client, _ = _client()
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
    client, repo = _client()
    repo.save_daily_report(DailyReport(date="2026-03-08", markdown="old"))
    repo.save_daily_report(DailyReport(date="2026-03-09", markdown="newer"))
    repo.save_daily_report(DailyReport(date="2026-03-10", markdown="newest"))

    resp = client.get("/reports/daily", params={"limit": 2, "offset": 1})
    data = resp.json()

    assert resp.status_code == 200
    assert data["ok"] is True
    assert [item["date"] for item in data["reports"]] == ["2026-03-09", "2026-03-08"]


def test_root_and_stats_endpoints_return_useful_summary() -> None:
    client, repo = _client()
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
    client, repo = _client()
    empty = client.get("/reports/daily/latest.md")
    assert empty.status_code == 200
    assert "No report has been generated yet." in empty.text

    repo.save_daily_report(DailyReport(date="2026-03-10", markdown="# Report\n\nHello"))
    filled = client.get("/reports/daily/latest.md")
    assert filled.status_code == 200
    assert filled.text == "# Report\n\nHello"
