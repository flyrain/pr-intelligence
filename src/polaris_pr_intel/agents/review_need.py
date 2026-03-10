from __future__ import annotations

from polaris_pr_intel.models import PullRequestSnapshot, ReviewSignal
from polaris_pr_intel.scoring.rules import score_review_need


class ReviewNeedAgent:
    def run(self, pr: PullRequestSnapshot) -> ReviewSignal:
        score, reasons = score_review_need(pr)
        return ReviewSignal(
            pr_number=pr.number,
            score=score,
            reasons=reasons,
            needs_review=score >= 2.0,
        )
