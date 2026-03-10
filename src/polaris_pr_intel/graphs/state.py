from __future__ import annotations

from typing import TypedDict

from polaris_pr_intel.models import DailyReport, GitHubEvent, IssueSignal, IssueSnapshot, PRSummary, PullRequestSnapshot, ReviewSignal


class PRIntelState(TypedDict, total=False):
    event: GitHubEvent
    pr: PullRequestSnapshot
    issue: IssueSnapshot
    pr_summary: PRSummary
    review_signal: ReviewSignal
    issue_signal: IssueSignal
    daily_report: DailyReport
    notifications: list[str]
    errors: list[str]
