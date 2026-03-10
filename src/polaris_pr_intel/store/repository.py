from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from polaris_pr_intel.models import DailyReport, IssueSignal, IssueSnapshot, PRSummary, PullRequestSnapshot, ReviewSignal


@dataclass
class InMemoryRepository:
    prs: dict[int, PullRequestSnapshot] = field(default_factory=dict)
    issues: dict[int, IssueSnapshot] = field(default_factory=dict)
    pr_summaries: dict[int, PRSummary] = field(default_factory=dict)
    review_signals: dict[int, ReviewSignal] = field(default_factory=dict)
    issue_signals: dict[int, IssueSignal] = field(default_factory=dict)
    daily_reports: list[DailyReport] = field(default_factory=list)
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

    def save_daily_report(self, report: DailyReport) -> None:
        self.daily_reports.append(report)

    def latest_daily_report(self) -> DailyReport | None:
        return self.daily_reports[-1] if self.daily_reports else None

    def list_daily_reports(self, limit: int = 30, offset: int = 0) -> list[DailyReport]:
        if offset < 0:
            offset = 0
        if limit < 1:
            limit = 1
        reports = list(reversed(self.daily_reports))
        return reports[offset : offset + limit]

    def has_processed_event(self, delivery_id: str) -> bool:
        return delivery_id in self.processed_events

    def mark_processed_event(self, delivery_id: str) -> None:
        self.processed_events.add(delivery_id)
