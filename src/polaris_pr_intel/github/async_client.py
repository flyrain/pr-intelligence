from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

from polaris_pr_intel.models import IssueSnapshot, PullRequestSnapshot


class AsyncGitHubClient:
    """Async GitHub REST API client with parallel request execution."""

    def __init__(self, token: str, owner: str, repo: str, max_concurrent: int = 20) -> None:
        self.owner = owner
        self.repo = repo
        self.max_concurrent = max_concurrent
        self._client: httpx.AsyncClient | None = None
        self._token = token
        self._semaphore: asyncio.Semaphore | None = None

    async def _ensure_client(self) -> None:
        """Lazy initialization of async client."""
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url="https://api.github.com",
                headers={
                    "Accept": "application/vnd.github+json",
                    "Authorization": f"Bearer {self._token}",
                    "X-GitHub-Api-Version": "2022-11-28",
                },
                timeout=30.0,
                limits=httpx.Limits(max_connections=self.max_concurrent),
            )
            self._semaphore = asyncio.Semaphore(self.max_concurrent)

    async def close(self) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None

    async def _get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        await self._ensure_client()
        assert self._client is not None
        assert self._semaphore is not None
        async with self._semaphore:
            resp = await self._client.get(path, params=params)
            resp.raise_for_status()
            return resp.json()

    async def get_pull_request(self, number: int, include_diff: bool = False) -> PullRequestSnapshot:
        """Fetch PR with activity metrics in parallel."""
        # Run PR details and activity metrics in parallel
        pr_task = self._get(f"/repos/{self.owner}/{self.repo}/pulls/{number}")
        activity_task = self.get_pull_request_activity_metrics(number)

        pr_data, activity = await asyncio.gather(pr_task, activity_task)

        pr = self._to_pr_snapshot(pr_data)
        pr.activity_comments_24h = activity["comments_24h"]
        pr.activity_comments_7d = activity["comments_7d"]
        pr.activity_reviews_24h = activity["reviews_24h"]
        pr.activity_reviews_7d = activity["reviews_7d"]

        if include_diff:
            pr.diff_text = await self.get_pull_request_diff(number)

        return pr

    async def get_pull_request_activity_metrics(self, number: int) -> dict[str, int]:
        """Fetch all activity metrics in parallel."""
        now = datetime.now(timezone.utc)
        since_24h = now - timedelta(hours=24)
        since_7d = now - timedelta(days=7)

        # Fetch all three endpoints concurrently
        issue_comments_task = self._count_recent_items(
            f"/repos/{self.owner}/{self.repo}/issues/{number}/comments",
            since_cutoffs=[since_24h, since_7d],
            timestamp_key="created_at",
        )
        review_comments_task = self._count_recent_items(
            f"/repos/{self.owner}/{self.repo}/pulls/{number}/comments",
            since_cutoffs=[since_24h, since_7d],
            timestamp_key="created_at",
        )
        reviews_task = self._count_recent_items(
            f"/repos/{self.owner}/{self.repo}/pulls/{number}/reviews",
            since_cutoffs=[since_24h, since_7d],
            timestamp_key="submitted_at",
            require_body=True,
        )

        results = await asyncio.gather(issue_comments_task, review_comments_task, reviews_task)
        issue_comments_24h, issue_comments_7d = results[0]
        review_comments_24h, review_comments_7d = results[1]
        reviews_24h, reviews_7d = results[2]

        return {
            "comments_24h": issue_comments_24h + review_comments_24h,
            "comments_7d": issue_comments_7d + review_comments_7d,
            "reviews_24h": reviews_24h,
            "reviews_7d": reviews_7d,
        }

    async def get_pull_request_diff(self, number: int, max_chars: int = 120_000) -> str:
        """Fetch the combined patch for all files in a PR."""
        files = await self._get(
            f"/repos/{self.owner}/{self.repo}/pulls/{number}/files",
            params={"per_page": 100},
        )
        parts: list[str] = []
        total = 0
        for f in files:
            patch = f.get("patch", "")
            header = f"--- {f['filename']}\n"
            chunk = header + patch + "\n"
            if total + len(chunk) > max_chars:
                parts.append(f"\n... diff truncated at {max_chars} chars ...")
                break
            parts.append(chunk)
            total += len(chunk)
        return "".join(parts)

    async def list_recent_pull_requests(
        self, per_page: int = 30, page: int = 1
    ) -> list[PullRequestSnapshot]:
        """Fetch PRs list, then hydrate all PRs in parallel."""
        data = await self._get(
            f"/repos/{self.owner}/{self.repo}/pulls",
            params={
                "state": "open",
                "sort": "updated",
                "direction": "desc",
                "per_page": per_page,
                "page": page,
            },
        )
        # Fetch all PR details in parallel
        tasks = [self.get_pull_request(pr["number"]) for pr in data]
        return await asyncio.gather(*tasks)

    async def list_recent_issues(
        self, per_page: int = 30, page: int = 1, since: str | None = None
    ) -> list[IssueSnapshot]:
        params: dict[str, Any] = {
            "state": "open",
            "sort": "updated",
            "direction": "desc",
            "per_page": per_page,
            "page": page,
        }
        if since:
            params["since"] = since
        data = await self._get(
            f"/repos/{self.owner}/{self.repo}/issues",
            params=params,
        )
        issues = [i for i in data if "pull_request" not in i]
        return [self._to_issue_snapshot(issue) for issue in issues]

    async def _count_recent_items(
        self,
        path: str,
        *,
        since_cutoffs: list[datetime],
        timestamp_key: str,
        require_body: bool = False,
    ) -> tuple[int, ...]:
        """Count items with pagination support."""
        counts = [0 for _ in since_cutoffs]
        page = 1
        while True:
            data = await self._get(path, params={"per_page": 100, "page": page})
            if not data:
                break
            for item in data:
                timestamp = item.get(timestamp_key)
                if not timestamp:
                    continue
                created_at = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
                if require_body and not (item.get("body") or "").strip():
                    continue
                for index, cutoff in enumerate(since_cutoffs):
                    if created_at >= cutoff:
                        counts[index] += 1
            if len(data) < 100:
                break
            page += 1
        return tuple(counts)

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


class GitHubClientWrapper:
    """Synchronous wrapper around async client for drop-in compatibility.

    Uses asyncio.run() to execute async operations in a thread-safe manner.
    Each operation gets its own event loop to avoid conflicts with FastAPI's threading.
    """

    def __init__(self, token: str, owner: str, repo: str) -> None:
        self._token = token
        self._owner = owner
        self._repo = repo

    def close(self) -> None:
        """No-op for compatibility. Async client auto-closes after each operation."""
        pass

    def get_pull_request(self, number: int, include_diff: bool = False) -> PullRequestSnapshot:
        async def _run():
            client = AsyncGitHubClient(self._token, self._owner, self._repo)
            try:
                return await client.get_pull_request(number, include_diff)
            finally:
                await client.close()

        return asyncio.run(_run())

    def list_recent_pull_requests(self, per_page: int = 30, page: int = 1) -> list[PullRequestSnapshot]:
        async def _run():
            client = AsyncGitHubClient(self._token, self._owner, self._repo)
            try:
                return await client.list_recent_pull_requests(per_page, page)
            finally:
                await client.close()

        return asyncio.run(_run())

    def list_recent_issues(
        self, per_page: int = 30, page: int = 1, since: str | None = None
    ) -> list[IssueSnapshot]:
        async def _run():
            client = AsyncGitHubClient(self._token, self._owner, self._repo)
            try:
                return await client.list_recent_issues(per_page, page, since)
            finally:
                await client.close()

        return asyncio.run(_run())

    def get_pull_request_diff(self, number: int, max_chars: int = 120_000) -> str:
        async def _run():
            client = AsyncGitHubClient(self._token, self._owner, self._repo)
            try:
                return await client.get_pull_request_diff(number, max_chars)
            finally:
                await client.close()

        return asyncio.run(_run())

    def get_pull_request_activity_metrics(self, number: int) -> dict[str, int]:
        async def _run():
            client = AsyncGitHubClient(self._token, self._owner, self._repo)
            try:
                return await client.get_pull_request_activity_metrics(number)
            finally:
                await client.close()

        return asyncio.run(_run())
