from __future__ import annotations

from dataclasses import dataclass

from polaris_pr_intel.llm._utils import _clamp
from polaris_pr_intel.models import PRAttentionContext, PRAttentionDecision, PRSubagentFinding, PullRequestSnapshot


@dataclass
class HeuristicLLMAdapter:
    provider: str = "heuristic"
    model: str = "local-heuristic"
    review_aspects: list[tuple[str, str]] = None

    def __post_init__(self):
        if self.review_aspects is None:
            self.review_aspects = [
                ("code-risk", "code risk and complexity"),
                ("test-impact", "test impact and coverage"),
                ("docs-quality", "documentation and release notes"),
                ("security-signal", "security and permission model"),
            ]

    def _heuristic_analyze_pr(self, agent_name: str, focus_area: str, pr: PullRequestSnapshot) -> PRSubagentFinding:
        churn = pr.additions + pr.deletions
        score = 0.5
        reasons: list[str] = []

        if churn > 1000:
            score += 0.35
            reasons.append("very large diff")
        elif churn > 400:
            score += 0.2
            reasons.append("large diff")
        if pr.changed_files > 25:
            score += 0.2
            reasons.append("many files touched")
        if pr.commits > 12:
            score += 0.1
            reasons.append("many commits")
        if "security" in pr.title.lower() or "security" in pr.body.lower():
            score += 0.25
            reasons.append("security-sensitive change")
        if "docs" in pr.title.lower() or "docs" in pr.body.lower():
            score -= 0.1
            reasons.append("documentation-oriented")

        score = _clamp(score, 0.05, 0.99)
        if score >= 0.75:
            verdict = "high"
        elif score >= 0.45:
            verdict = "medium"
        else:
            verdict = "low"

        tags: list[str] = []
        suggested_catalogs: list[str] = []
        title_body = f"{pr.title}\n{pr.body}".lower()
        if "security" in title_body or "permission" in title_body:
            tags.append("security")
            suggested_catalogs.append("security-risk")
        if churn > 400 or pr.changed_files > 25:
            tags.append("large-change")
            suggested_catalogs.append("release-risk")
        if pr.requested_reviewers:
            suggested_catalogs.append("needs-review")

        recommendations = [f"Review {focus_area} changes in touched files."]
        if pr.requested_reviewers:
            recommendations.append(f"Confirm requested reviewers: {', '.join(pr.requested_reviewers)}.")
        if churn > 400:
            recommendations.append("Split review into focused passes by subsystem.")

        summary_reasons = ", ".join(reasons) if reasons else "standard risk profile"
        summary = f"{focus_area} check for PR #{pr.number}: {summary_reasons}."
        return PRSubagentFinding(
            agent_name=agent_name,
            focus_area=focus_area,
            verdict=verdict,
            score=score,
            summary=summary,
            recommendations=recommendations,
            tags=list(dict.fromkeys(tags)),
            suggested_catalogs=list(dict.fromkeys(suggested_catalogs)),
            confidence=0.65,
        )

    def analyze_pr(self, agent_name: str, focus_area: str, pr: PullRequestSnapshot) -> PRSubagentFinding:
        return self._heuristic_analyze_pr(agent_name, focus_area, pr)

    def _heuristic_analyze_pr_comprehensive(self, pr: PullRequestSnapshot) -> list[PRSubagentFinding]:
        return [
            self._heuristic_analyze_pr(agent_name, focus_area, pr)
            for agent_name, focus_area in self.review_aspects
        ]

    def analyze_pr_comprehensive(self, pr: PullRequestSnapshot) -> list[PRSubagentFinding]:
        return self._heuristic_analyze_pr_comprehensive(pr)

    def analyze_pr_with_self_review(self, pr: PullRequestSnapshot) -> list[PRSubagentFinding]:
        return self.analyze_pr_comprehensive(pr)

    def _heuristic_analyze_catalog_routing(self, pr: PullRequestSnapshot) -> PRSubagentFinding:
        return self._heuristic_analyze_pr("catalog-router", "catalog routing and prioritization", pr)

    def analyze_catalog_routing(self, pr: PullRequestSnapshot) -> PRSubagentFinding:
        return self._heuristic_analyze_catalog_routing(pr)

    def _heuristic_analyze_catalog_routing_batch(self, prs: list[PullRequestSnapshot]) -> dict[int, PRSubagentFinding]:
        return {pr.number: self._heuristic_analyze_catalog_routing(pr) for pr in prs}

    def analyze_catalog_routing_batch(self, prs: list[PullRequestSnapshot]) -> dict[int, PRSubagentFinding]:
        return self._heuristic_analyze_catalog_routing_batch(prs)

    def analyze_attention_batch(self, contexts: list[PRAttentionContext]) -> dict[int, PRAttentionDecision]:
        decisions: dict[int, PRAttentionDecision] = {}
        for ctx in contexts:
            score = 3.0
            tags: list[str] = []
            catalogs: list[str] = []
            if ctx.comments_24h >= 5 or ctx.reviews_24h >= 2:
                score += 3.0
                tags.append("active-discussion")
                catalogs.extend(["needs-review", "recently-updated"])
            elif ctx.comments_24h >= 2:
                score += 1.5
                tags.append("warm-discussion")
                catalogs.append("needs-review")
            if ctx.inactive_days >= 7:
                score -= 2.5
                tags.append("inactive")
                catalogs.append("aging-prs")
            if ctx.requested_reviewers:
                score += 2.0
                tags.append("review-requested")
                catalogs.append("needs-review")
            if ctx.diff_size >= 500 or ctx.changed_files >= 20:
                score += 1.5
                tags.append("release-risk")
                catalogs.append("release-risk")
            text = f"{ctx.title}\n{ctx.body}".lower()
            if "security" in text or "permission" in text or "security" in {label.lower() for label in ctx.labels}:
                score += 1.5
                tags.append("security-risk")
                catalogs.append("security-risk")
            score = _clamp(score, 0.0, 10.0)
            needs_review = "needs-review" in catalogs or score >= 5.0
            if ctx.inactive_days >= 7 and score < 5.0:
                band = "defer"
                reason = "Inactive for a while and no stronger competing urgency signals."
                defer_reason = f"Inactive for {ctx.inactive_days:.1f} days."
            elif score >= 8.0:
                band = "high"
                reason = "High attention candidate due to active discussion or elevated review risk."
                defer_reason = ""
            elif score >= 5.0:
                band = "medium"
                reason = "Worth review soon based on current activity and change scope."
                defer_reason = ""
            else:
                band = "low"
                reason = "Lower urgency relative to the rest of the current PR queue."
                defer_reason = ""
            decisions[ctx.pr_number] = PRAttentionDecision(
                pr_number=ctx.pr_number,
                needs_review=needs_review,
                priority_score=score,
                priority_band=band,
                priority_reason=reason,
                defer_reason=defer_reason,
                tags=list(dict.fromkeys(tags)),
                suggested_catalogs=list(dict.fromkeys(catalogs)),
                confidence=0.55,
            )
        return decisions
