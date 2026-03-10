from __future__ import annotations

from polaris_pr_intel.models import PRSummary, PullRequestSnapshot


class PRSummarizerAgent:
    def run(self, pr: PullRequestSnapshot) -> PRSummary:
        diff_size = pr.additions + pr.deletions
        if diff_size > 800 or pr.changed_files > 20:
            risk = "high"
        elif diff_size > 250:
            risk = "medium"
        else:
            risk = "low"

        impacted = []
        body = pr.body.lower()
        for area in ["runtime", "persistence", "helm", "docs", "python", "spark", "security"]:
            if area in pr.title.lower() or area in body:
                impacted.append(area)

        headline = f"PR #{pr.number}: {pr.title}"
        technical_summary = (
            f"Open PR by @{pr.author}. {pr.commits} commits, {pr.changed_files} files changed, "
            f"+{pr.additions}/-{pr.deletions}."
        )

        return PRSummary(
            pr_number=pr.number,
            headline=headline,
            technical_summary=technical_summary,
            impact_areas=impacted,
            risk_level=risk,
            suggested_reviewers=pr.requested_reviewers,
        )
