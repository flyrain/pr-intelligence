from __future__ import annotations

from datetime import datetime, timezone

from polaris_pr_intel.github.client import GitHubClient
from polaris_pr_intel.store.base import Repository


class SnapshotIngestor:
    def __init__(self, gh: GitHubClient, repo: Repository) -> None:
        self.gh = gh
        self.repo = repo

    def sync_recent(self, per_page: int = 30, max_pages: int = 1, since: str | None = None) -> dict[str, int]:
        total_prs = 0
        total_issues = 0
        for page in range(1, max_pages + 1):
            prs = self.gh.list_recent_pull_requests(per_page=per_page, page=page)
            issues = self.gh.list_recent_issues(per_page=per_page, page=page, since=since)

            for pr in prs:
                self.repo.upsert_pr(pr)
                total_prs += 1
            for issue in issues:
                self.repo.upsert_issue(issue)
                total_issues += 1

            if len(prs) < per_page and len(issues) < per_page:
                break

        self.repo.last_sync_at = datetime.now(timezone.utc)
        return {"prs": total_prs, "issues": total_issues}

    def sync_pr(self, pr_number: int) -> bool:
        try:
            pr = self.gh.get_pull_request(pr_number)
        except Exception:
            return False
        self.repo.upsert_pr(pr)
        return True
