from __future__ import annotations

from datetime import datetime

from polaris_pr_intel.models import DailyReport
from polaris_pr_intel.store.repository import InMemoryRepository


class DailyReporterAgent:
    def run(self, repo: InMemoryRepository) -> DailyReport:
        now = datetime.utcnow().strftime("%Y-%m-%d")

        pr_signals = sorted(repo.review_signals.values(), key=lambda s: s.score, reverse=True)
        issue_signals = sorted(repo.issue_signals.values(), key=lambda s: s.score, reverse=True)

        lines = [f"# Polaris PR Intelligence Report ({now})", "", "## PRs Needing Review"]
        if pr_signals:
            for s in pr_signals[:10]:
                pr = repo.prs.get(s.pr_number)
                if not pr:
                    continue
                lines.append(f"- [#{pr.number}]({pr.html_url}) {pr.title} | score={s.score:.1f} | {', '.join(s.reasons)}")
        else:
            lines.append("- No PR review signals captured yet.")

        lines += ["", "## Interesting Issues"]
        if issue_signals:
            for s in issue_signals[:10]:
                issue = repo.issues.get(s.issue_number)
                if not issue:
                    continue
                lines.append(f"- [#{issue.number}]({issue.html_url}) {issue.title} | score={s.score:.1f} | {', '.join(s.reasons)}")
        else:
            lines.append("- No issue signals captured yet.")

        return DailyReport(date=now, markdown="\n".join(lines))
