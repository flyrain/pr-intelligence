from __future__ import annotations

from datetime import datetime
from typing import Any

from polaris_pr_intel.models import IssueSnapshot, PullRequestSnapshot


def _parse_github_timestamp(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def to_pr_snapshot(pr: dict[str, Any]) -> PullRequestSnapshot:
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
        updated_at=_parse_github_timestamp(pr["updated_at"]),
    )


def to_issue_snapshot(issue: dict[str, Any]) -> IssueSnapshot:
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
        updated_at=_parse_github_timestamp(issue["updated_at"]),
    )
