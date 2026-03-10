from __future__ import annotations

from polaris_pr_intel.config import Settings
from polaris_pr_intel.models import PullRequestSnapshot, ReviewSignal
from polaris_pr_intel.scoring.rules import score_review_need


class ReviewNeedAgent:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def run(self, pr: PullRequestSnapshot) -> ReviewSignal:
        score, reasons = score_review_need(pr, settings=self.settings)
        return ReviewSignal(
            pr_number=pr.number,
            score=score,
            reasons=reasons,
            needs_review=score >= self.settings.review_needed_threshold,
            rule_version="v1",
        )
