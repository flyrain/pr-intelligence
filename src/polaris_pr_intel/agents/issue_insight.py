from __future__ import annotations

from polaris_pr_intel.config import Settings
from polaris_pr_intel.models import IssueSignal, IssueSnapshot
from polaris_pr_intel.scoring.rules import score_issue_interest


class IssueInsightAgent:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def run(self, issue: IssueSnapshot) -> IssueSignal:
        score, reasons = score_issue_interest(issue)
        return IssueSignal(
            issue_number=issue.number,
            score=score,
            reasons=reasons,
            interesting=score >= self.settings.issue_interesting_threshold,
            rule_version="v1",
        )
