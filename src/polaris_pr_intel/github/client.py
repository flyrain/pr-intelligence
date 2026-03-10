from __future__ import annotations

from datetime import datetime
from typing import Any

import httpx

from polaris_pr_intel.models import IssueSnapshot, PullRequestSnapshot


class GitHubClient:
    def __init__(self, token: str, owner: str, repo: str) -> None:
        self.owner = owner
        self.repo = repo
        self._client = httpx.Client(
            base_url="https://api.github.com",
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {token}",
                "X-GitHub-Api-Version": "2022-11-28",
            },
            timeout=30.0,
        )

    def close(self) -> None:
        self._client.close()

    def _get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        resp = self._client.get(path, params=params)
        resp.raise_for_status()
        return resp.json()

    def list_recent_pull_requests(self, per_page: int = 30) -> list[PullRequestSnapshot]:
        data = self._get(
            f"/repos/{self.owner}/{self.repo}/pulls",
            params={"state": "open", "sort": "updated", "direction": "desc", "per_page": per_page},
        )
        return [self._to_pr_snapshot(pr) for pr in data]

    def list_recent_issues(self, per_page: int = 30) -> list[IssueSnapshot]:
        data = self._get(
            f"/repos/{self.owner}/{self.repo}/issues",
            params={"state": "open", "sort": "updated", "direction": "desc", "per_page": per_page},
        )
        issues = [i for i in data if "pull_request" not in i]
        return [self._to_issue_snapshot(issue) for issue in issues]

    @staticmethod
    def _to_pr_snapshot(pr: dict[str, Any]) -> PullRequestSnapshot:
        return PullRequestSnapshot(
            number=pr["number"],
            title=pr.get("title", ""),
            body=pr.get("body") or "",
            state=pr.get("state", "open"),
            draft=bool(pr.get("draft", False)),
            author=(pr.get("user") or {}).get("login", "unknown"),
            labels=[l["name"] for l in pr.get("labels", [])],
            requested_reviewers=[u["login"] for u in pr.get("requested_reviewers", [])],
            comments=pr.get("comments", 0),
            review_comments=pr.get("review_comments", 0),
            commits=pr.get("commits", 0),
            changed_files=pr.get("changed_files", 0),
            additions=pr.get("additions", 0),
            deletions=pr.get("deletions", 0),
            html_url=pr.get("html_url", ""),
            updated_at=datetime.fromisoformat(pr["updated_at"].replace("Z", "+00:00")),
        )

    @staticmethod
    def _to_issue_snapshot(issue: dict[str, Any]) -> IssueSnapshot:
        return IssueSnapshot(
            number=issue["number"],
            title=issue.get("title", ""),
            body=issue.get("body") or "",
            state=issue.get("state", "open"),
            author=(issue.get("user") or {}).get("login", "unknown"),
            labels=[l["name"] for l in issue.get("labels", [])],
            comments=issue.get("comments", 0),
            assignees=[a["login"] for a in issue.get("assignees", [])],
            html_url=issue.get("html_url", ""),
            updated_at=datetime.fromisoformat(issue["updated_at"].replace("Z", "+00:00")),
        )
