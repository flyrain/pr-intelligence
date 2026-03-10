from __future__ import annotations

from polaris_pr_intel.github.client import GitHubClient
from polaris_pr_intel.store.repository import InMemoryRepository


class SnapshotIngestor:
    def __init__(self, gh: GitHubClient, repo: InMemoryRepository) -> None:
        self.gh = gh
        self.repo = repo

    def sync_recent(self, per_page: int = 30) -> dict[str, int]:
        prs = self.gh.list_recent_pull_requests(per_page=per_page)
        issues = self.gh.list_recent_issues(per_page=per_page)

        for pr in prs:
            self.repo.upsert_pr(pr)
        for issue in issues:
            self.repo.upsert_issue(issue)

        return {"prs": len(prs), "issues": len(issues)}
