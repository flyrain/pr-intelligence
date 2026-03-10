from __future__ import annotations

from datetime import datetime, timezone

from polaris_pr_intel.models import DailyReport
from polaris_pr_intel.store.base import Repository


class DailyReporterAgent:
    def run(self, repo: Repository) -> DailyReport:
        now_dt = datetime.now(timezone.utc)
        today = now_dt.date()
        now = now_dt.strftime("%Y-%m-%d")

        pr_signals = sorted(repo.review_signals.values(), key=lambda s: s.score, reverse=True)
        issue_signals = sorted(repo.issue_signals.values(), key=lambda s: s.score, reverse=True)
        new_prs_today = [pr for pr in repo.prs.values() if pr.updated_at.date() == today]
        aging_prs = [pr for pr in repo.prs.values() if (now_dt - pr.updated_at).total_seconds() / 3600 >= 72 and pr.state == "open"]
        issue_label_counts: dict[str, int] = {}
        for issue in repo.issues.values():
            for label in issue.labels:
                key = label.lower()
                issue_label_counts[key] = issue_label_counts.get(key, 0) + 1

        lines = ["## PRs Needing Review"]
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

        lines += ["", "## Aging Open PRs (72h+)"]
        if aging_prs:
            for pr in sorted(aging_prs, key=lambda p: p.updated_at)[:10]:
                age_hours = int((now_dt - pr.updated_at).total_seconds() / 3600)
                lines.append(f"- [#{pr.number}]({pr.html_url}) {pr.title} | age={age_hours}h")
        else:
            lines.append("- No aging open PRs above 72h.")

        lines += ["", "## New/Updated PRs Today"]
        if new_prs_today:
            for pr in sorted(new_prs_today, key=lambda p: p.updated_at, reverse=True)[:10]:
                lines.append(f"- [#{pr.number}]({pr.html_url}) {pr.title} | updated={pr.updated_at.isoformat()}")
        else:
            lines.append("- No PR updates observed today.")

        lines += ["", "## Issue Label Trends"]
        if issue_label_counts:
            top_labels = sorted(issue_label_counts.items(), key=lambda item: item[1], reverse=True)[:10]
            for label, count in top_labels:
                lines.append(f"- {label}: {count}")
        else:
            lines.append("- No issue labels available.")

        lines += ["", "## Deep PR Review Signals"]
        review_reports = repo.top_pr_review_reports(limit=10)
        if review_reports:
            for report in review_reports:
                pr = repo.prs.get(report.pr_number)
                if not pr:
                    continue
                lines.append(
                    f"- [#{pr.number}]({pr.html_url}) {pr.title} | priority={report.overall_priority:.2f} | {report.overall_recommendation}"
                )
        else:
            lines.append("- No deep PR review reports yet.")

        return DailyReport(date=now, markdown="\n".join(lines))
