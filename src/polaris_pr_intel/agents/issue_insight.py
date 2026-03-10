from __future__ import annotations

from polaris_pr_intel.models import IssueSignal, IssueSnapshot
from polaris_pr_intel.scoring.rules import score_issue_interest


class IssueInsightAgent:
    def run(self, issue: IssueSnapshot) -> IssueSignal:
        score, reasons = score_issue_interest(issue)
        return IssueSignal(
            issue_number=issue.number,
            score=score,
            reasons=reasons,
            interesting=score >= 2.0,
        )
