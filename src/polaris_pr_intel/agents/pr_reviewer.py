from __future__ import annotations

from dataclasses import dataclass

from polaris_pr_intel.llm.base import LLMAdapter
from polaris_pr_intel.models import PRReviewReport, PRSubagentFinding, PullRequestSnapshot


@dataclass(frozen=True)
class PRSubagentSpec:
    agent_name: str
    focus_area: str


class PRSubagentReviewer:
    def __init__(self, llm: LLMAdapter) -> None:
        self.llm = llm
        self.specs = [
            PRSubagentSpec(agent_name="code-risk", focus_area="code risk and complexity"),
            PRSubagentSpec(agent_name="test-impact", focus_area="test impact and coverage"),
            PRSubagentSpec(agent_name="docs-quality", focus_area="documentation and release notes"),
            PRSubagentSpec(agent_name="security-signal", focus_area="security and permission model"),
        ]

    def run(self, pr: PullRequestSnapshot) -> list[PRSubagentFinding]:
        return [self.llm.analyze_pr(spec.agent_name, spec.focus_area, pr) for spec in self.specs]

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
