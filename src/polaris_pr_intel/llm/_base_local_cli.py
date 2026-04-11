from __future__ import annotations

import json
import logging
import re
import shlex
from dataclasses import dataclass
from pathlib import Path

from polaris_pr_intel.llm._heuristic import HeuristicLLMAdapter
from polaris_pr_intel.llm._utils import _clamp
from polaris_pr_intel.models import PRAttentionContext, PRAttentionDecision, PRSubagentFinding, PullRequestSnapshot

logger = logging.getLogger(__name__)


@dataclass
class BaseLocalCLIAdapter(HeuristicLLMAdapter):
    review_skill_file: str = ""
    analysis_skill_file: str = ""
    fail_review_job_on_generation_error: bool = False

    def __post_init__(self):
        if self.review_skill_file:
            aspects = self._parse_review_aspects(self.review_skill_file)
            if aspects:
                self.review_aspects = aspects
        super().__post_init__()

    def _parse_review_aspects(self, skill_file: str) -> list[tuple[str, str]] | None:
        if not skill_file:
            return None
        try:
            text = self._read_skill_body(skill_file)
            aspects = []
            in_aspects_section = False
            for line in text.split("\n"):
                if line.startswith("## Review Aspects"):
                    in_aspects_section = True
                    continue
                if in_aspects_section and line.startswith("## ") and not line.startswith("### "):
                    break
                if in_aspects_section and line.startswith("### "):
                    match = re.match(r"###\s+\d+\.\s+([a-z-]+):\s+(.+)", line)
                    if match:
                        aspects.append((match.group(1), match.group(2)))
            return aspects if aspects else None
        except Exception:
            return None

    @staticmethod
    def _read_skill_body(skill_file: str) -> str:
        text = Path(skill_file).expanduser().read_text(encoding="utf-8")
        if text.startswith("---"):
            end = text.find("---", 3)
            if end != -1:
                text = text[end + 3 :].strip()
        return text

    def _wrap_skill_prompt(self, skill_body: str) -> str:
        return skill_body

    def _load_skill_prompt(self, skill_file: str) -> str:
        if not skill_file:
            return ""
        try:
            return self._wrap_skill_prompt(self._read_skill_body(skill_file))
        except Exception:
            return ""

    @staticmethod
    def _format_diff_section(pr: PullRequestSnapshot, limit: int) -> str:
        if not pr.diff_text:
            return ""
        truncated_diff = pr.diff_text[:limit]
        if len(pr.diff_text) > limit:
            truncated_diff += "\n... (diff truncated)"
        return f"""

Code diff (patch):
```
{truncated_diff}
```"""

    @staticmethod
    def _format_pr_metadata(pr: PullRequestSnapshot) -> str:
        return f"""- number: {pr.number}
- title: {pr.title}
- author: {pr.author}
- state: {pr.state}
- draft: {pr.draft}
- commits: {pr.commits}
- changed_files: {pr.changed_files}
- additions: {pr.additions}
- deletions: {pr.deletions}
- labels: {", ".join(pr.labels) if pr.labels else "(none)"}
- requested_reviewers: {", ".join(pr.requested_reviewers) if pr.requested_reviewers else "(none)"}"""

    def _build_pr_prompt_document(
        self,
        *,
        skill_prompt: str,
        instructions: str,
        pr: PullRequestSnapshot,
        diff_limit: int,
    ) -> str:
        skill_prefix = skill_prompt + "\n\n" if skill_prompt else ""
        return f"""{skill_prefix}{instructions}

Pull request metadata:
{self._format_pr_metadata(pr)}

PR description:
{pr.body[:4000]}
{self._format_diff_section(pr, diff_limit)}"""

    def _build_catalog_batch_prompt_document(
        self,
        *,
        skill_prompt: str,
        instructions: str,
        prs: list[PullRequestSnapshot],
        diff_limit: int,
    ) -> str:
        sections: list[str] = []
        for pr in prs:
            diff_section = ""
            if pr.diff_text:
                truncated_diff = pr.diff_text[:diff_limit]
                if len(pr.diff_text) > diff_limit:
                    truncated_diff += "\n... (diff truncated)"
                diff_section = f"\nDiff:\n```\n{truncated_diff}\n```"
            sections.append(
                f"""PR #{pr.number}
- title: {pr.title}
- author: {pr.author}
- state: {pr.state}
- draft: {pr.draft}
- commits: {pr.commits}
- changed_files: {pr.changed_files}
- additions: {pr.additions}
- deletions: {pr.deletions}
- labels: {", ".join(pr.labels) if pr.labels else "(none)"}
- requested_reviewers: {", ".join(pr.requested_reviewers) if pr.requested_reviewers else "(none)"}

Description:
{pr.body[:2000]}{diff_section}"""
            )
        skill_prefix = skill_prompt + "\n\n" if skill_prompt else ""
        return f"""{skill_prefix}{instructions}

Pull requests:
""" + "\n\n".join(sections)

    @staticmethod
    def _build_attention_batch_prompt(skill_prompt: str, contexts: list[PRAttentionContext]) -> str:
        sections: list[str] = []
        for ctx in contexts:
            sections.append(
                f"""PR #{ctx.pr_number}
- title: {ctx.title}
- author: {ctx.author}
- state: {ctx.state}
- draft: {ctx.draft}
- labels: {", ".join(ctx.labels) if ctx.labels else "(none)"}
- requested_reviewers: {", ".join(ctx.requested_reviewers) if ctx.requested_reviewers else "(none)"}
- updated_at: {ctx.updated_at.isoformat()}
- age_hours: {ctx.age_hours:.1f}
- inactive_days: {ctx.inactive_days:.1f}
- comments_total: {ctx.comments_total}
- review_comments_total: {ctx.review_comments_total}
- comments_24h: {ctx.comments_24h}
- comments_7d: {ctx.comments_7d}
- reviews_24h: {ctx.reviews_24h}
- reviews_7d: {ctx.reviews_7d}
- commits: {ctx.commits}
- changed_files: {ctx.changed_files}
- additions: {ctx.additions}
- deletions: {ctx.deletions}
- diff_size: {ctx.diff_size}
- has_prior_review_activity: {ctx.has_prior_review_activity}
- has_prior_deep_review: {ctx.has_prior_deep_review}
- heuristic_signals: {", ".join(ctx.rule_reasons) if ctx.rule_reasons else "(none)"}

Summary:
{ctx.body[:1200]}"""
            )
        intro = skill_prompt + "\n\n" if skill_prompt else ""
        return intro + """You are ranking pull requests for attention after sync.

This is a batch prioritization task across the full PR set. Compare PRs to each other and decide what deserves attention now.

Return ONLY valid JSON in this shape:
{
  "decisions": {
    "<pr_number>": {
      "needs_review": true,
      "priority_score": 0.0-10.0,
      "priority_band": "high|medium|low|defer",
      "priority_reason": "1-2 short operational sentences",
      "defer_reason": "optional short defer reason",
      "tags": ["optional-short-tag"],
      "suggested_catalogs": ["needs-review|aging-prs|security-risk|release-risk|recently-updated"],
      "confidence": 0.0-1.0
    }
  }
}

Guidance:
- Rank PRs relative to each other, not independently.
- Recent discussion and active review threads are strong attention signals.
- Long inactivity is usually a reason to defer, unless the PR is clearly high risk or blocking.
- Use `needs-review` for PRs that should be in the active review queue.
- Use `aging-prs` for stale PRs that need a nudge more than immediate review.
- Keep reasons short and concrete.

Pull requests:
""" + "\n\n".join(sections)

    def _log_cli_invocation(self, cmd: list[str], prompt: str) -> None:
        logger.info(
            "Invoking %s LLM command: %s [prompt_chars=%d]",
            self.provider,
            shlex.join([*cmd, "<prompt>"]),
            len(prompt),
        )

    def _maybe_raise_non_review_failure(self, exc: Exception) -> None:
        return None

    def _run_prompt(self, prompt: str, agent_name: str, focus_area: str, pr: PullRequestSnapshot) -> PRSubagentFinding:
        data = self._run_raw_prompt(prompt)
        if not isinstance(data, dict):
            raise ValueError("Expected JSON object from model")
        data["agent_name"] = agent_name
        data["focus_area"] = focus_area
        finding = PRSubagentFinding.model_validate(data)
        finding.score = _clamp(finding.score, 0.0, 1.0)
        finding.confidence = _clamp(finding.confidence, 0.0, 1.0)
        return finding

    def _extract_findings(self, findings_data: object) -> list[PRSubagentFinding]:
        if not isinstance(findings_data, list):
            raise ValueError("Expected 'findings' array in response")

        findings: list[PRSubagentFinding] = []
        for item in findings_data:
            if not isinstance(item, dict):
                continue
            finding = PRSubagentFinding.model_validate(item)
            finding.score = _clamp(finding.score, 0.0, 1.0)
            finding.confidence = _clamp(finding.confidence, 0.0, 1.0)
            findings.append(finding)

        if not findings:
            raise ValueError("No valid findings extracted from response")
        return findings

    def _extract_catalog_routing_batch_findings(
        self,
        findings_data: object,
        prs: list[PullRequestSnapshot],
    ) -> dict[int, PRSubagentFinding]:
        if not isinstance(findings_data, dict):
            raise ValueError("Missing findings object in batch result")

        results: dict[int, PRSubagentFinding] = {}
        for pr in prs:
            raw_finding = findings_data.get(str(pr.number))
            if not isinstance(raw_finding, dict):
                continue
            raw_finding["agent_name"] = "catalog-router"
            raw_finding["focus_area"] = "catalog routing and prioritization"
            finding = PRSubagentFinding.model_validate(raw_finding)
            finding.score = _clamp(finding.score, 0.0, 1.0)
            finding.confidence = _clamp(finding.confidence, 0.0, 1.0)
            results[pr.number] = finding
        return results

    def _extract_attention_batch_decisions(
        self,
        decisions_data: object,
        contexts: list[PRAttentionContext],
    ) -> dict[int, PRAttentionDecision]:
        if not isinstance(decisions_data, dict):
            raise ValueError("Missing decisions object in batch result")

        decisions: dict[int, PRAttentionDecision] = {}
        for ctx in contexts:
            raw = decisions_data.get(str(ctx.pr_number))
            if not isinstance(raw, dict):
                continue
            raw["pr_number"] = ctx.pr_number
            decision = PRAttentionDecision.model_validate(raw)
            decision.priority_score = _clamp(decision.priority_score, 0.0, 10.0)
            decision.confidence = _clamp(decision.confidence, 0.0, 1.0)
            decisions[ctx.pr_number] = decision
        return decisions

    def _run_comprehensive_review(self, pr: PullRequestSnapshot) -> list[PRSubagentFinding]:
        payload = self._run_raw_prompt(self._build_comprehensive_prompt(pr))
        if not isinstance(payload, dict):
            raise ValueError("Expected JSON object from model")
        return self._extract_findings(payload.get("findings"))

    def _format_review_failure(self, exc: Exception) -> str:
        return str(exc)

    def _format_followup_failure(self, exc: Exception) -> str:
        return self._format_review_failure(exc)

    def _review_failure(self, exc: Exception) -> RuntimeError:
        return RuntimeError(f"{self.provider} review failed: {self._format_review_failure(exc)}")

    def _should_fail_review_job(self, exc: Exception) -> bool:
        return self.fail_review_job_on_generation_error

    def _handle_generation_failure(
        self,
        context: str,
        exc: Exception,
        pr: PullRequestSnapshot,
    ) -> list[PRSubagentFinding]:
        if self._should_fail_review_job(exc):
            failure = self._review_failure(exc)
            logger.warning("%s failed, failing review job: %s", context, failure)
            raise failure from exc
        logger.warning("%s failed, falling back to heuristic: %s", context, self._format_review_failure(exc))
        return super().analyze_pr_comprehensive(pr)

    def analyze_pr_comprehensive(self, pr: PullRequestSnapshot) -> list[PRSubagentFinding]:
        try:
            return self._run_comprehensive_review(pr)
        except Exception as exc:
            return self._handle_generation_failure("Comprehensive review", exc, pr)

    def analyze_pr_with_self_review(self, pr: PullRequestSnapshot) -> list[PRSubagentFinding]:
        logger.info("Step 1/3: Generating initial review for PR #%d", pr.number)
        try:
            initial_findings = self._run_comprehensive_review(pr)
        except Exception as exc:
            return self._handle_generation_failure("Step 1", exc, pr)

        logger.info("Step 2/3: Critiquing initial findings for PR #%d", pr.number)
        critique_prompt = self._build_critique_prompt(pr, initial_findings)
        try:
            critique_payload = self._run_raw_prompt(critique_prompt)
            if not isinstance(critique_payload, dict):
                logger.warning("Critique returned non-dict, skipping revision")
                return initial_findings

            issues = critique_payload.get("issues", [])
            if not issues:
                logger.info("No critique issues found, using initial findings")
                return initial_findings
        except Exception as exc:
            logger.warning("Step 2 (critique) failed, using initial findings: %s", self._format_followup_failure(exc))
            return initial_findings

        logger.info("Step 3/3: Revising findings based on critique for PR #%d", pr.number)
        revision_prompt = self._build_revision_prompt(pr, initial_findings, critique_payload)
        try:
            revised_payload = self._run_raw_prompt(revision_prompt)
            if not isinstance(revised_payload, dict):
                logger.warning("Revision returned non-dict, using initial findings")
                return initial_findings

            revised_findings = self._extract_findings(revised_payload.get("findings"))
            logger.info("Self-review complete for PR #%d: %d findings revised", pr.number, len(revised_findings))
            return revised_findings
        except Exception as exc:
            logger.warning("Step 3 (revision) failed, using initial findings: %s", self._format_followup_failure(exc))
            return initial_findings

    def analyze_catalog_routing_batch(self, prs: list[PullRequestSnapshot]) -> dict[int, PRSubagentFinding]:
        if not prs:
            return {}
        try:
            payload = self._run_raw_prompt(self._build_catalog_batch_prompt(prs))
            findings = payload.get("findings") if isinstance(payload, dict) else None
            return self._extract_catalog_routing_batch_findings(findings, prs)
        except Exception as exc:
            self._maybe_raise_non_review_failure(exc)
            return super().analyze_catalog_routing_batch(prs)

    def analyze_attention_batch(self, contexts: list[PRAttentionContext]) -> dict[int, PRAttentionDecision]:
        if not contexts:
            return {}
        try:
            payload = self._run_raw_prompt(
                self._build_attention_batch_prompt(self._load_skill_prompt(self.analysis_skill_file), contexts)
            )
            raw_decisions = payload.get("decisions") if isinstance(payload, dict) else None
            return self._extract_attention_batch_decisions(raw_decisions, contexts)
        except Exception as exc:
            self._maybe_raise_non_review_failure(exc)
            return super().analyze_attention_batch(contexts)

    def _build_critique_prompt(self, pr: PullRequestSnapshot, findings: list[PRSubagentFinding]) -> str:
        findings_json = json.dumps([f.model_dump() for f in findings], indent=2)
        return f"""You are a quality reviewer examining a PR review that was just generated.

Review the findings below and identify specific quality issues:

**Specificity Check:**
- Missing file:line references in recommendations?
- Vague language like "consider improving" instead of actionable steps?

**Coverage Check:**
- Critical checks from skill.md that were missed?
- Obvious patterns not addressed (repeated logic, error handling gaps)?

**Consistency Check:**
- Verdict (low/medium/high) mismatched with score (0.0-1.0)?
- Contradictions between findings?

**Clarity Check:**
- Unnecessary hedging or filler words?
- Overly long summaries (>2 sentences)?

Original PR context:
- number: {pr.number}
- title: {pr.title}
- changed_files: {pr.changed_files}
- additions: {pr.additions}
- deletions: {pr.deletions}

Review findings to critique:
{findings_json}

Respond with ONLY valid JSON:
{{
  "issues": [
    {{
      "aspect": "code-risk",
      "problem": "Recommendation too vague - 'improve error handling'",
      "fix": "Specify file:line and exact change needed"
    }}
  ],
  "strengths": ["Good coverage of auth patterns", "Clear file references in test-impact"]
}}"""

    def _build_revision_prompt(self, pr: PullRequestSnapshot, original_findings: list[PRSubagentFinding], critique: dict) -> str:
        review_skill_prompt = self._load_skill_prompt(self.review_skill_file)
        original_json = json.dumps([f.model_dump() for f in original_findings], indent=2)
        critique_json = json.dumps(critique, indent=2)

        return f"""{review_skill_prompt + chr(10) + chr(10) if review_skill_prompt else ""}You previously generated a PR review. A quality check identified issues. Now revise your findings.

Original PR context:
- number: {pr.number}
- title: {pr.title}
- author: {pr.author}
- state: {pr.state}
- draft: {pr.draft}
- commits: {pr.commits}
- changed_files: {pr.changed_files}
- additions: {pr.additions}
- deletions: {pr.deletions}
- labels: {", ".join(pr.labels) if pr.labels else "(none)"}
- requested_reviewers: {", ".join(pr.requested_reviewers) if pr.requested_reviewers else "(none)"}

PR description:
{pr.body[:4000]}
{self._format_diff_section(pr, 80_000)}

Your original findings:
{original_json}

Quality issues identified:
{critique_json}

Regenerate the review addressing all issues listed in the critique. Keep strengths, fix problems.

Follow the "Review Style" and "Automated Review Output Format" guidance from the skill above.

Respond with ONLY valid JSON:
{{
  "findings": [
    {{ <revised finding object per skill format> }}
  ]
}}"""
