from __future__ import annotations

from polaris_pr_intel.llm.llm_adapter import LLMAdapter
from polaris_pr_intel.models import PRReviewReport, PRSubagentFinding, PullRequestSnapshot


class PRSubagentReviewer:
    """Single-agent PR reviewer that analyzes all aspects in one comprehensive pass."""

    def __init__(self, llm: LLMAdapter, enable_self_review: bool = False) -> None:
        self.llm = llm
        self.enable_self_review = enable_self_review

    def _reset_review_session_ids(self) -> None:
        reset_session_ids = getattr(self.llm, "reset_session_ids", None)
        if callable(reset_session_ids):
            reset_session_ids()
        reset_resume_context = getattr(self.llm, "reset_resume_context", None)
        if callable(reset_resume_context):
            reset_resume_context()

    def _review_session_ids(self) -> list[str]:
        session_ids = getattr(self.llm, "session_ids", [])
        if not isinstance(session_ids, list):
            return []
        return [session_id for session_id in session_ids if isinstance(session_id, str) and session_id]

    def current_session_ids(self) -> list[str]:
        session_ids = self._review_session_ids()
        return session_ids[-1:] if session_ids else []

    def current_resume_context(self) -> dict[str, str]:
        resume_context = getattr(self.llm, "resume_context", {})
        if not isinstance(resume_context, dict):
            return {}
        return {
            key: value
            for key, value in resume_context.items()
            if isinstance(key, str) and isinstance(value, str)
        }

    def run(self, pr: PullRequestSnapshot) -> list[PRSubagentFinding]:
        """Run comprehensive PR review covering all aspects (code risk, security, tests, docs)."""
        self._reset_review_session_ids()
        if self.enable_self_review:
            return self.llm.analyze_pr_with_self_review(pr)
        return self.llm.analyze_pr_comprehensive(pr)

    def blocked_report(self, pr: PullRequestSnapshot, reason: str) -> PRReviewReport:
        return PRReviewReport(
            pr_number=pr.number,
            provider=self.llm.provider,
            model=self.llm.model,
            session_ids=[],
            resume_cwd="",
            resume_branch="",
            findings=[],
            overall_priority=0.0,
            overall_recommendation="Review blocked until the PR patch is available.",
            blocked_reason=reason,
        )

    def aggregate(
        self,
        pr: PullRequestSnapshot,
        findings: list[PRSubagentFinding],
        *,
        session_ids: list[str] | None = None,
        resume_context: dict[str, str] | None = None,
    ) -> PRReviewReport:
        review_session_ids = session_ids if session_ids is not None else self.current_session_ids()
        review_resume_context = (
            resume_context if resume_context is not None else self.current_resume_context()
        )
        if not findings:
            return PRReviewReport(
                pr_number=pr.number,
                provider=self.llm.provider,
                model=self.llm.model,
                session_ids=review_session_ids,
                resume_cwd=review_resume_context.get("cwd", ""),
                resume_branch=review_resume_context.get("branch", ""),
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
            session_ids=review_session_ids,
            resume_cwd=review_resume_context.get("cwd", ""),
            resume_branch=review_resume_context.get("branch", ""),
            findings=findings,
            overall_priority=round(avg_score, 3),
            overall_recommendation=recommendation,
        )
