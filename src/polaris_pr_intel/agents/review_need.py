from __future__ import annotations

from polaris_pr_intel.config import Settings
from polaris_pr_intel.models import PullRequestSnapshot, ReviewSignal
from polaris_pr_intel.scoring.rules import score_review_need


class ReviewNeedAgent:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def run(self, pr: PullRequestSnapshot) -> ReviewSignal:
        score, reasons = score_review_need(pr, settings=self.settings)
        requested_you = False
        target_login = (self.settings.review_target_login or "").strip().lower()
        if target_login:
            requested_you = any((r or "").strip().lower() == target_login for r in pr.requested_reviewers)
            if requested_you and "requested-you" not in reasons:
                reasons.append("requested-you")
        return ReviewSignal(
            pr_number=pr.number,
            score=score,
            reasons=reasons,
            needs_review=(score >= self.settings.review_needed_threshold) or requested_you,
            rule_version="v1",
        )
