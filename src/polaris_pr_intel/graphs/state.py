from __future__ import annotations

from typing import TypedDict

from polaris_pr_intel.models import (
    AnalysisRun,
    GitHubEvent,
    IssueSignal,
    IssueSnapshot,
    PRReviewReport,
    PRSubagentFinding,
    PRSummary,
    PullRequestSnapshot,
    ReviewSignal,
)


class PRIntelState(TypedDict, total=False):
    pr_number: int
    event: GitHubEvent
    pr: PullRequestSnapshot
    issue: IssueSnapshot
    pr_summary: PRSummary
    pr_review_findings: list[PRSubagentFinding]
    pr_review_session_ids: list[str]
    pr_review_resume_context: dict[str, str]
    pr_review_report: PRReviewReport
    review_signal: ReviewSignal
    issue_signal: IssueSignal
    analysis_run: AnalysisRun
    report_markdown: str
    notifications: list[str]
    errors: list[str]
