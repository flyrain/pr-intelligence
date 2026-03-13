from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from polaris_pr_intel.models import (
    AnalysisRun,
    DailyReport,
    IssueSignal,
    IssueSnapshot,
    PRReviewReport,
    PRSummary,
    PullRequestSnapshot,
    ReviewSignal,
)


@dataclass
class InMemoryRepository:
    prs: dict[int, PullRequestSnapshot] = field(default_factory=dict)
    issues: dict[int, IssueSnapshot] = field(default_factory=dict)
    pr_summaries: dict[int, PRSummary] = field(default_factory=dict)
    review_signals: dict[int, ReviewSignal] = field(default_factory=dict)
    issue_signals: dict[int, IssueSignal] = field(default_factory=dict)
    pr_review_reports: dict[int, PRReviewReport] = field(default_factory=dict)
    daily_reports: list[DailyReport] = field(default_factory=list)
    analysis_runs: list[AnalysisRun] = field(default_factory=list)
    processed_events: set[str] = field(default_factory=set)
    last_sync_at: datetime | None = None

    def upsert_pr(self, pr: PullRequestSnapshot) -> None:
        self.prs[pr.number] = pr

    def upsert_issue(self, issue: IssueSnapshot) -> None:
        self.issues[issue.number] = issue

    def save_pr_summary(self, summary: PRSummary) -> None:
        self.pr_summaries[summary.pr_number] = summary

    def save_review_signal(self, signal: ReviewSignal) -> None:
        self.review_signals[signal.pr_number] = signal

    def save_issue_signal(self, signal: IssueSignal) -> None:
        self.issue_signals[signal.issue_number] = signal

    def save_pr_review_report(self, report: PRReviewReport) -> None:
        self.pr_review_reports[report.pr_number] = report

    def save_daily_report(self, report: DailyReport) -> None:
        self.daily_reports.append(report)

    def save_analysis_run(self, run: AnalysisRun) -> None:
        self.analysis_runs.append(run)

    def latest_daily_report(self) -> DailyReport | None:
        return self.daily_reports[-1] if self.daily_reports else None

    def list_daily_reports(self, limit: int = 30, offset: int = 0) -> list[DailyReport]:
        if offset < 0:
            offset = 0
        if limit < 1:
            limit = 1
        reports = list(reversed(self.daily_reports))
        return reports[offset : offset + limit]

    def latest_analysis_run(self) -> AnalysisRun | None:
        return self.analysis_runs[-1] if self.analysis_runs else None

    def list_analysis_runs(self, limit: int = 30, offset: int = 0) -> list[AnalysisRun]:
        if offset < 0:
            offset = 0
        if limit < 1:
            limit = 1
        runs = list(reversed(self.analysis_runs))
        return runs[offset : offset + limit]

    def latest_pr_review_report(self, pr_number: int) -> PRReviewReport | None:
        return self.pr_review_reports.get(pr_number)

    def top_pr_review_reports(self, limit: int = 20) -> list[PRReviewReport]:
        if limit < 1:
            limit = 1
        reports = sorted(self.pr_review_reports.values(), key=lambda r: r.overall_priority, reverse=True)
        return reports[:limit]

    def has_processed_event(self, delivery_id: str) -> bool:
        return delivery_id in self.processed_events

    def mark_processed_event(self, delivery_id: str) -> None:
        self.processed_events.add(delivery_id)
