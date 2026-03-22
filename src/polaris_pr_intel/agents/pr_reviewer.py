from __future__ import annotations

from polaris_pr_intel.llm.base import LLMAdapter
from polaris_pr_intel.models import PRReviewReport, PRSubagentFinding, PullRequestSnapshot


class PRSubagentReviewer:
    """Single-agent PR reviewer that analyzes all aspects in one comprehensive pass."""

    def __init__(self, llm: LLMAdapter, enable_self_review: bool = False) -> None:
        self.llm = llm
        self.enable_self_review = enable_self_review

    def run(self, pr: PullRequestSnapshot) -> list[PRSubagentFinding]:
        """Run comprehensive PR review covering all aspects (code risk, security, tests, docs)."""
        if self.enable_self_review:
            return self.llm.analyze_pr_with_self_review(pr)
        return self.llm.analyze_pr_comprehensive(pr)

    def blocked_report(self, pr: PullRequestSnapshot, reason: str) -> PRReviewReport:
        return PRReviewReport(
            pr_number=pr.number,
            provider=self.llm.provider,
            model=self.llm.model,
            findings=[],
            overall_priority=0.0,
            overall_recommendation="Review blocked until the PR patch is available.",
            blocked_reason=reason,
        )

    def aggregate(self, pr: PullRequestSnapshot, findings: list[PRSubagentFinding]) -> PRReviewReport:
        if not findings:
            return PRReviewReport(
                pr_number=pr.number,
                provider=self.llm.provider,
                model=self.llm.model,
                findings=[],
                overall_priority=0.0,
                overall_recommendation="No subagent findings were produced.",
            )

        avg_score = sum(f.score for f in findings) / len(findings)
        high_count = sum(1 for f in findings if f.verdict == "high")
        if high_count >= 2 or avg_score >= 0.75:
            recommendation = "Prioritize immediate deep review by at least two maintainers."
        elif avg_score >= 0.45:
            recommendation = "Schedule targeted review this cycle and validate critical areas first."
        else:
            recommendation = "Standard review path is sufficient."

        return PRReviewReport(
            pr_number=pr.number,
            provider=self.llm.provider,
            model=self.llm.model,
            findings=findings,
            overall_priority=round(avg_score, 3),
            overall_recommendation=recommendation,
        )
