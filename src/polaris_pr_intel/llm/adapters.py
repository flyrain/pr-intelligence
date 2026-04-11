from __future__ import annotations

import json
import logging
import os
import re
import shlex
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from polaris_pr_intel.models import PRAttentionContext, PRAttentionDecision, PRSubagentFinding, PullRequestSnapshot

logger = logging.getLogger(__name__)


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


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


def _log_cli_invocation(provider: str, cmd: list[str], prompt: str) -> None:
    logger.info(
        "Invoking %s LLM command: %s [prompt_chars=%d]",
        provider,
        shlex.join([*cmd, "<prompt>"]),
        len(prompt),
    )


def _codex_subprocess_env() -> dict[str, str]:
    env = os.environ.copy()
    # `subprocess.run()` inherits the parent environment by default. When this API
    # server is launched from Codex Desktop, env vars like `CODEX_SANDBOX` and
    # `CODEX_THREAD_ID` are present for the parent session. Passing them through to
    # a fresh nested `codex exec` can confuse the child CLI into booting with
    # parent-session sandbox/runtime state instead of starting a clean run.
    # Preserve `CODEX_HOME` so callers can isolate nested Codex state/log files.
    for key in list(env):
        if key.startswith("CODEX_") and key != "CODEX_HOME":
            env.pop(key, None)
    return env


@dataclass
class HeuristicLLMAdapter:
    provider: str = "heuristic"
    model: str = "local-heuristic"
    # Default review aspects - override in subclasses by parsing from skill file
    review_aspects: list[tuple[str, str]] = None

    def __post_init__(self):
        if self.review_aspects is None:
            # Failsafe defaults that mirror skill.md structure
            # TODO: Parse these from skill file in adapter subclasses
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
        """Comprehensive PR review covering all aspects. Uses review_aspects from skill or defaults."""
        return self._heuristic_analyze_pr_comprehensive(pr)

    def analyze_pr_with_self_review(self, pr: PullRequestSnapshot) -> list[PRSubagentFinding]:
        """Three-step self-review: generate → critique → revise. Base implementation just calls comprehensive."""
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


@dataclass
class OpenAIAdapter(HeuristicLLMAdapter):
    api_key: str = ""
    provider: str = "openai"
    model: str = "gpt-4o-mini"


@dataclass
class GeminiAdapter(HeuristicLLMAdapter):
    api_key: str = ""
    provider: str = "gemini"
    model: str = "gemini-1.5-pro"


@dataclass
class AnthropicAdapter(HeuristicLLMAdapter):
    api_key: str = ""
    provider: str = "anthropic"
    model: str = "claude-3-5-sonnet"


@dataclass
class ClaudeCodeLocalAdapter(HeuristicLLMAdapter):
    provider: str = "claude_code_local"
    model: str = "claude-code-local"
    command: str = "claude"
    timeout_sec: int = 300
    max_turns: int = 15
    repo_dir: str = ""
    review_skill_file: str = ""
    analysis_skill_file: str = ""

    def __post_init__(self):
        # Parse review aspects from skill file if available
        if self.review_skill_file:
            aspects = self._parse_review_aspects(self.review_skill_file)
            if aspects:
                self.review_aspects = aspects
        # Fall back to parent's defaults if parsing fails
        super().__post_init__()

    def _parse_review_aspects(self, skill_file: str) -> list[tuple[str, str]] | None:
        """Parse review aspects from skill file 'Review Aspects' section."""
        if not skill_file:
            return None
        try:
            text = Path(skill_file).expanduser().read_text(encoding="utf-8")
            # Look for "## Review Aspects" section
            aspects = []
            in_aspects_section = False
            for line in text.split('\n'):
                if line.startswith('## Review Aspects'):
                    in_aspects_section = True
                    continue
                elif in_aspects_section and line.startswith('## ') and not line.startswith('### '):
                    # Hit next ## section (but not ###), stop
                    break
                elif in_aspects_section and line.startswith('### '):
                    # Parse "### 1. code-risk: Code Risk and Complexity"
                    match = re.match(r'###\s+\d+\.\s+([a-z-]+):\s+(.+)', line)
                    if match:
                        agent_name = match.group(1)
                        focus_area = match.group(2)
                        aspects.append((agent_name, focus_area))
            return aspects if aspects else None
        except Exception:
            return None

    def _load_skill_prompt(self, skill_file: str) -> str:
        """Load skill content from skill_file path if it exists.

        Extracts the review criteria/checks from the skill file and wraps
        them with a note that the JSON output format from the user prompt
        takes precedence over any output formatting in the skill.
        """
        if not skill_file:
            return ""
        try:
            text = Path(skill_file).expanduser().read_text(encoding="utf-8")
            # Strip YAML frontmatter if present
            if text.startswith("---"):
                end = text.find("---", 3)
                if end != -1:
                    text = text[end + 3:].strip()
            return (
                "Use the following as your review checklist. Apply these checks to the PR. "
                "Ignore any output format instructions in the skill — use the JSON format from the user prompt. "
                "Keep all text short and casual — write like a teammate, not a report.\n\n"
                + text
            )
        except Exception:
            return ""

    @staticmethod
    def _extract_json_payload(text: str) -> object:
        """Extract JSON payload from model output.

        With --output-format json, Claude returns a JSON object with a "result"
        field containing the final text response.  We look for a JSON object
        or array payload inside that text.
        """
        try:
            envelope = json.loads(text)
            if isinstance(envelope, dict) and "result" in envelope:
                text = envelope["result"]
            else:
                return envelope
        except (json.JSONDecodeError, TypeError):
            pass

        try:
            return json.loads(text.strip())
        except (json.JSONDecodeError, TypeError):
            pass

        for match in re.finditer(r"```(?:json)?\s*\n(.*?)\n```", text, re.DOTALL):
            try:
                return json.loads(match.group(1).strip())
            except (json.JSONDecodeError, TypeError):
                continue

        brace_start = text.find("{")
        if brace_start >= 0:
            depth = 0
            for i in range(brace_start, len(text)):
                if text[i] == "{":
                    depth += 1
                elif text[i] == "}":
                    depth -= 1
                    if depth == 0:
                        try:
                            return json.loads(text[brace_start : i + 1])
                        except (json.JSONDecodeError, TypeError):
                            pass
                        break

        raise ValueError("No valid JSON payload found in model output")

    @classmethod
    def _extract_json_from_result(cls, text: str) -> dict:
        payload = cls._extract_json_payload(text)
        if isinstance(payload, dict) and "verdict" in payload:
            return payload
        raise ValueError("No valid finding JSON found in Claude output")

    def _build_prompt(self, agent_name: str, focus_area: str, pr: PullRequestSnapshot) -> str:
        review_skill_prompt = self._load_skill_prompt(self.review_skill_file)
        diff_section = ""
        if pr.diff_text:
            truncated_diff = pr.diff_text[:80_000]
            if len(pr.diff_text) > 80_000:
                truncated_diff += "\n... (diff truncated)"
            diff_section = f"""

Code diff (patch):
```
{truncated_diff}
```
"""
        return f"""{review_skill_prompt + chr(10) + chr(10) if review_skill_prompt else ""}You are a code reviewer focused on: {focus_area}

Review the PR below. Use tools to read source files when the diff isn't enough.

Follow the "Review Style" and "Automated Review Output Format" guidance from the skill above.

Respond with ONLY valid JSON for agent_name="{agent_name}" and focus_area="{focus_area}".

Pull request metadata:
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
{diff_section}"""

    def _build_comprehensive_prompt(self, pr: PullRequestSnapshot) -> str:
        """Build prompt for comprehensive single-pass PR review covering all aspects."""
        review_skill_prompt = self._load_skill_prompt(self.review_skill_file)
        diff_section = ""
        if pr.diff_text:
            truncated_diff = pr.diff_text[:80_000]
            if len(pr.diff_text) > 80_000:
                truncated_diff += "\n... (diff truncated)"
            diff_section = f"""

Code diff (patch):
```
{truncated_diff}
```
"""
        return f"""{review_skill_prompt + chr(10) + chr(10) if review_skill_prompt else ""}You are conducting a comprehensive code review covering multiple aspects.

Review the PR below according to the review aspects defined in the skill above. Use tools to read source files when the diff isn't enough.

Follow the "Review Style" and "Automated Review Output Format" guidance from the skill above.

Respond with ONLY valid JSON:
{{
  "findings": [
    {{ <finding object per skill format> }}
  ]
}}

Pull request metadata:
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
{diff_section}"""

    def _build_catalog_prompt(self, pr: PullRequestSnapshot) -> str:
        analysis_skill_prompt = self._load_skill_prompt(self.analysis_skill_file)
        diff_section = ""
        if pr.diff_text:
            truncated_diff = pr.diff_text[:60_000]
            if len(pr.diff_text) > 60_000:
                truncated_diff += "\n... (diff truncated)"
            diff_section = f"""

Code diff (patch):
```
{truncated_diff}
```
"""
        return f"""{analysis_skill_prompt + chr(10) + chr(10) if analysis_skill_prompt else ""}You are classifying a pull request for post-sync reporting and catalog routing.

This is not a line-by-line PR review. Your job is to assign the PR to the most relevant reporting catalogs and summarize why.

Prefer routing and triage signals over code review commentary. Keep the summary short and operational.

Respond with ONLY valid JSON:
{{
  "agent_name": "catalog-router",
  "focus_area": "catalog routing and prioritization",
  "verdict": "low|medium|high",
  "score": 0.0-1.0,
  "summary": "1-2 short sentences focused on routing/triage, not review comments",
  "recommendations": ["short triage action, e.g. 'put this in release-risk digest'"],
  "tags": ["optional-short-tag"],
  "suggested_catalogs": ["needs-review|aging-prs|security-risk|release-risk|interesting-issues|recently-updated"],
  "confidence": 0.0-1.0
}}

Routing guidance:
- `needs-review`: human review should be prioritized
- `aging-prs`: open and stale
- `security-risk`: auth, permissions, secrets, trust boundaries, security-sensitive changes
- `release-risk`: broad changes, risky diffs, regression potential, rollout concerns
- `recently-updated`: important fresh activity today
- `interesting-issues`: not applicable for PR routing unless clearly justified

Pull request metadata:
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
{diff_section}"""

    def analyze_pr(self, agent_name: str, focus_area: str, pr: PullRequestSnapshot) -> PRSubagentFinding:
        prompt = self._build_prompt(agent_name, focus_area, pr)
        try:
            return self._run_prompt(prompt, agent_name, focus_area, pr)
        except Exception as exc:
            detail = str(exc)
            if isinstance(exc, subprocess.CalledProcessError):
                stderr = (exc.stderr or "").strip().replace("\n", " ")
                stdout = (exc.stdout or "").strip().replace("\n", " ")
                detail = stderr or stdout or detail
            if "Failed to authenticate" in detail or "API Error: 401" in detail or "Not logged in" in detail:
                raise RuntimeError(
                    "claude-code-local authentication failed. Run `claude auth login` (or `claude setup-token`) and retry."
                ) from exc
            fallback = super().analyze_pr(agent_name, focus_area, pr)
            fallback.summary = f"(fallback heuristic: {type(exc).__name__}: {detail[:160]}) {fallback.summary}"
            return fallback

    def analyze_pr_comprehensive(self, pr: PullRequestSnapshot) -> list[PRSubagentFinding]:
        """Run comprehensive single-pass review covering all aspects."""
        prompt = self._build_comprehensive_prompt(pr)
        try:
            payload = self._run_raw_prompt(prompt)
            if not isinstance(payload, dict):
                raise ValueError("Expected JSON object from model")
            findings_data = payload.get("findings")
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
        except Exception as exc:
            detail = str(exc)
            if isinstance(exc, subprocess.CalledProcessError):
                stderr = (exc.stderr or "").strip().replace("\n", " ")
                stdout = (exc.stdout or "").strip().replace("\n", " ")
                detail = stderr or stdout or detail
            if "Failed to authenticate" in detail or "API Error: 401" in detail or "Not logged in" in detail:
                raise RuntimeError(
                    "claude-code-local authentication failed. Run `claude auth login` (or `claude setup-token`) and retry."
                ) from exc
            # Fallback to heuristic multi-finding
            return super().analyze_pr_comprehensive(pr)

    def analyze_pr_with_self_review(self, pr: PullRequestSnapshot) -> list[PRSubagentFinding]:
        """Three-step self-review: generate → critique → revise."""
        logger.info("Step 1/3: Generating initial review for PR #%d", pr.number)

        # Step 1: Generate initial review
        try:
            initial_findings = self.analyze_pr_comprehensive(pr)
        except Exception as exc:
            logger.warning("Step 1 failed, falling back to heuristic: %s", exc)
            return super().analyze_pr_comprehensive(pr)

        # Step 2: Critique the initial review
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
            logger.warning("Step 2 (critique) failed, using initial findings: %s", exc)
            return initial_findings

        # Step 3: Revise based on critique
        logger.info("Step 3/3: Revising findings based on critique for PR #%d", pr.number)
        revision_prompt = self._build_revision_prompt(pr, initial_findings, critique_payload)
        try:
            revised_payload = self._run_raw_prompt(revision_prompt)
            if not isinstance(revised_payload, dict):
                logger.warning("Revision returned non-dict, using initial findings")
                return initial_findings

            revised_findings_data = revised_payload.get("findings")
            if not isinstance(revised_findings_data, list):
                logger.warning("Revision missing findings array, using initial findings")
                return initial_findings

            revised_findings: list[PRSubagentFinding] = []
            for item in revised_findings_data:
                if not isinstance(item, dict):
                    continue
                finding = PRSubagentFinding.model_validate(item)
                finding.score = _clamp(finding.score, 0.0, 1.0)
                finding.confidence = _clamp(finding.confidence, 0.0, 1.0)
                revised_findings.append(finding)

            if not revised_findings:
                logger.warning("No valid revised findings, using initial findings")
                return initial_findings

            logger.info("Self-review complete for PR #%d: %d findings revised", pr.number, len(revised_findings))
            return revised_findings

        except Exception as exc:
            logger.warning("Step 3 (revision) failed, using initial findings: %s", exc)
            return initial_findings

    def _build_critique_prompt(self, pr: PullRequestSnapshot, findings: list[PRSubagentFinding]) -> str:
        """Build prompt for critiquing initial review findings."""
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
        """Build prompt for revising findings based on critique."""
        review_skill_prompt = self._load_skill_prompt(self.review_skill_file)
        original_json = json.dumps([f.model_dump() for f in original_findings], indent=2)
        critique_json = json.dumps(critique, indent=2)

        diff_section = ""
        if pr.diff_text:
            truncated_diff = pr.diff_text[:80_000]
            if len(pr.diff_text) > 80_000:
                truncated_diff += "\n... (diff truncated)"
            diff_section = f"""

Code diff (patch):
```
{truncated_diff}
```
"""

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
{diff_section}

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


    def analyze_catalog_routing(self, pr: PullRequestSnapshot) -> PRSubagentFinding:
        prompt = self._build_catalog_prompt(pr)
        try:
            return self._run_prompt(prompt, "catalog-router", "catalog routing and prioritization", pr)
        except Exception as exc:
            detail = str(exc)
            if isinstance(exc, subprocess.CalledProcessError):
                stderr = (exc.stderr or "").strip().replace("\n", " ")
                stdout = (exc.stdout or "").strip().replace("\n", " ")
                detail = stderr or stdout or detail
            if "Failed to authenticate" in detail or "API Error: 401" in detail or "Not logged in" in detail:
                raise RuntimeError(
                    "claude-code-local authentication failed. Run `claude auth login` (or `claude setup-token`) and retry."
                ) from exc
            fallback = super().analyze_catalog_routing(pr)
            fallback.summary = f"(fallback heuristic: {type(exc).__name__}: {detail[:160]}) {fallback.summary}"
            return fallback

    def analyze_catalog_routing_batch(self, prs: list[PullRequestSnapshot]) -> dict[int, PRSubagentFinding]:
        if not prs:
            return {}
        prompt = self._build_catalog_batch_prompt(prs)
        try:
            payload = self._run_raw_prompt(prompt)
            findings = payload.get("findings") if isinstance(payload, dict) else None
            if not isinstance(findings, dict):
                raise ValueError("Missing findings object in batch result")
            results: dict[int, PRSubagentFinding] = {}
            for pr in prs:
                raw_finding = findings.get(str(pr.number))
                if not isinstance(raw_finding, dict):
                    continue
                raw_finding["agent_name"] = "catalog-router"
                raw_finding["focus_area"] = "catalog routing and prioritization"
                finding = PRSubagentFinding.model_validate(raw_finding)
                finding.score = _clamp(finding.score, 0.0, 1.0)
                finding.confidence = _clamp(finding.confidence, 0.0, 1.0)
                results[pr.number] = finding
            return results
        except Exception as exc:
            detail = str(exc)
            if isinstance(exc, subprocess.CalledProcessError):
                stderr = (exc.stderr or "").strip().replace("\n", " ")
                stdout = (exc.stdout or "").strip().replace("\n", " ")
                detail = stderr or stdout or detail
            if "Failed to authenticate" in detail or "API Error: 401" in detail or "Not logged in" in detail:
                raise RuntimeError(
                    "claude-code-local authentication failed. Run `claude auth login` (or `claude setup-token`) and retry."
                ) from exc
            return super().analyze_catalog_routing_batch(prs)

    def analyze_attention_batch(self, contexts: list[PRAttentionContext]) -> dict[int, PRAttentionDecision]:
        if not contexts:
            return {}
        prompt = _build_attention_batch_prompt(self._load_skill_prompt(self.analysis_skill_file), contexts)
        try:
            payload = self._run_raw_prompt(prompt)
            raw_decisions = payload.get("decisions") if isinstance(payload, dict) else None
            if not isinstance(raw_decisions, dict):
                raise ValueError("Missing decisions object in batch result")
            decisions: dict[int, PRAttentionDecision] = {}
            for ctx in contexts:
                raw = raw_decisions.get(str(ctx.pr_number))
                if not isinstance(raw, dict):
                    continue
                raw["pr_number"] = ctx.pr_number
                decision = PRAttentionDecision.model_validate(raw)
                decision.priority_score = _clamp(decision.priority_score, 0.0, 10.0)
                decision.confidence = _clamp(decision.confidence, 0.0, 1.0)
                decisions[ctx.pr_number] = decision
            return decisions
        except Exception as exc:
            detail = str(exc)
            if isinstance(exc, subprocess.CalledProcessError):
                stderr = (exc.stderr or "").strip().replace("\n", " ")
                stdout = (exc.stdout or "").strip().replace("\n", " ")
                detail = stderr or stdout or detail
            if "Failed to authenticate" in detail or "API Error: 401" in detail or "Not logged in" in detail:
                raise RuntimeError(
                    "claude-code-local authentication failed. Run `claude auth login` (or `claude setup-token`) and retry."
                ) from exc
            return super().analyze_attention_batch(contexts)

    def _build_catalog_batch_prompt(self, prs: list[PullRequestSnapshot]) -> str:
        analysis_skill_prompt = self._load_skill_prompt(self.analysis_skill_file)
        sections: list[str] = []
        for pr in prs:
            diff_section = ""
            if pr.diff_text:
                truncated_diff = pr.diff_text[:20_000]
                if len(pr.diff_text) > 20_000:
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
        return (analysis_skill_prompt + "\n\n" if analysis_skill_prompt else "") + """You are classifying multiple pull requests for post-sync reporting and catalog routing.

This is a batch routing task, not a PR review task. For each PR, decide which reporting catalogs it belongs in and provide a short operational summary.

Respond with ONLY valid JSON in this shape:
{
  "findings": {
    "<pr_number>": {
      "verdict": "low|medium|high",
      "score": 0.0-1.0,
      "summary": "1-2 short routing sentences",
      "recommendations": ["short triage action"],
      "tags": ["optional-short-tag"],
      "suggested_catalogs": ["needs-review|aging-prs|security-risk|release-risk|interesting-issues|recently-updated"],
      "confidence": 0.0-1.0
    }
  }
}

Routing guidance:
- `needs-review`: human review should be prioritized
- `aging-prs`: open and stale
- `security-risk`: auth, permissions, secrets, trust boundaries, security-sensitive changes
- `release-risk`: broad changes, risky diffs, regression potential, rollout concerns
- `recently-updated`: important fresh activity today
- `interesting-issues`: generally not applicable for PR routing

Pull requests:
""" + "\n\n".join(sections)

    def _run_prompt(self, prompt: str, agent_name: str, focus_area: str, pr: PullRequestSnapshot) -> PRSubagentFinding:
        try:
            data = self._run_raw_prompt(prompt)
            if not isinstance(data, dict):
                raise ValueError("Expected JSON object from model")
            data["agent_name"] = agent_name
            data["focus_area"] = focus_area
            finding = PRSubagentFinding.model_validate(data)
            finding.score = _clamp(finding.score, 0.0, 1.0)
            finding.confidence = _clamp(finding.confidence, 0.0, 1.0)
            return finding
        except Exception:
            raise

    def _run_raw_prompt(self, prompt: str) -> object:
        try:
            cmd = [
                self.command,
                "--print",
                "--dangerously-skip-permissions",
                "--output-format", "json",
                "--allowedTools", "Read,Grep,Glob,Bash",
                "--setting-sources", "user,project,local",
            ]
            # Skills are already included in prompts by callers (_build_prompt, _build_catalog_prompt, etc.)
            # so we don't need to append them here
            if self.model and self.model not in {"claude-code-local", "local-heuristic"}:
                cmd.extend(["--model", self.model])
            _log_cli_invocation(self.provider, cmd, prompt)
            cmd.append(prompt)
            proc = subprocess.run(
                cmd,
                check=True,
                capture_output=True,
                text=True,
                timeout=self.timeout_sec,
                cwd=self.repo_dir or None,
            )
            return self._extract_json_payload(proc.stdout)
        except Exception:
            raise


@dataclass
class CodexLocalAdapter(HeuristicLLMAdapter):
    provider: str = "codex_local"
    model: str = "gpt-5-codex"
    command: str = "codex"
    timeout_sec: int = 300
    max_turns: int = 15
    reasoning_effort: str = "high"
    repo_dir: str = ""
    review_skill_file: str = ""
    analysis_skill_file: str = ""

    def __post_init__(self):
        # Parse review aspects from skill file if available
        if self.review_skill_file:
            aspects = self._parse_review_aspects(self.review_skill_file)
            if aspects:
                self.review_aspects = aspects
        # Fall back to parent's defaults if parsing fails
        super().__post_init__()

    def _parse_review_aspects(self, skill_file: str) -> list[tuple[str, str]] | None:
        """Parse review aspects from skill file 'Review Aspects' section."""
        if not skill_file:
            return None
        try:
            text = Path(skill_file).expanduser().read_text(encoding="utf-8")
            # Look for "## Review Aspects" section
            aspects = []
            in_aspects_section = False
            for line in text.split('\n'):
                if line.startswith('## Review Aspects'):
                    in_aspects_section = True
                    continue
                elif in_aspects_section and line.startswith('## ') and not line.startswith('### '):
                    # Hit next ## section (but not ###), stop
                    break
                elif in_aspects_section and line.startswith('### '):
                    # Parse "### 1. code-risk: Code Risk and Complexity"
                    match = re.match(r'###\s+\d+\.\s+([a-z-]+):\s+(.+)', line)
                    if match:
                        agent_name = match.group(1)
                        focus_area = match.group(2)
                        aspects.append((agent_name, focus_area))
            return aspects if aspects else None
        except Exception:
            return None

    def _load_skill_prompt(self, skill_file: str) -> str:
        if not skill_file:
            return ""
        try:
            text = Path(skill_file).expanduser().read_text(encoding="utf-8")
            if text.startswith("---"):
                end = text.find("---", 3)
                if end != -1:
                    text = text[end + 3 :].strip()
            return (
                "Use the following project skill as guidance. Follow its triage and review rules, "
                "but obey the task-specific JSON format and scope in the user prompt.\n\n" + text
            )
        except Exception:
            return ""

    @staticmethod
    def _format_failure_detail(exc: Exception) -> str:
        detail = str(exc)
        if isinstance(exc, subprocess.TimeoutExpired):
            timeout_sec = int(exc.timeout) if exc.timeout else "unknown"
            return (
                f"codex_local CLI timed out after {timeout_sec}s before producing output. "
                "Large PR reviews may need a higher CODEX_TIMEOUT_SEC, lower CODEX_REASONING_EFFORT, "
                "or self-review disabled."
            )
        if isinstance(exc, subprocess.CalledProcessError):
            stderr = (exc.stderr or "").strip().replace("\n", " ")
            stdout = (exc.stdout or "").strip().replace("\n", " ")
            detail = stderr or stdout or detail
        lowered = detail.lower()
        if "stream disconnected before completion" in lowered:
            return (
                "codex_local CLI started but could not reach the Codex backend from this runtime. "
                "Check network/auth for the service environment or switch providers."
            )
        if (
            "failed to open state db" in lowered
            or ("migration" in lowered and "missing in the resolved migrations" in lowered)
            or ("/.codex" in detail and "operation not permitted" in lowered)
        ):
            return (
                "codex_local CLI could not initialize or access its local Codex state in this runtime. "
                "Use a clean CODEX_HOME or run the service from a normal terminal."
            )
        if "attempted to create a null object" in lowered or "could not create otel exporter" in lowered:
            return (
                "codex_local CLI crashed before producing output. This commonly happens when the API "
                "server is running inside a sandboxed Codex Desktop/session environment on macOS. "
                "Run the service from a normal terminal or use claude_code_local in this environment."
            )
        return detail

    def _build_prompt(self, agent_name: str, focus_area: str, pr: PullRequestSnapshot) -> str:
        skill_prompt = self._load_skill_prompt(self.review_skill_file)
        diff_section = ""
        if pr.diff_text:
            truncated_diff = pr.diff_text[:80_000]
            if len(pr.diff_text) > 80_000:
                truncated_diff += "\n... (diff truncated)"
            diff_section = f"""

Code diff (patch):
```
{truncated_diff}
```
"""
        return f"""{skill_prompt + chr(10) + chr(10) if skill_prompt else ""}You are an expert code reviewer acting as a specialized PR review subagent.
Your focus area is: {focus_area}

Analyze the pull request below with concrete, code-specific findings.
You may inspect repository files for extra context if needed.

Follow the "Review Style" and "Automated Review Output Format" guidance from the skill above.

Return ONLY valid JSON for agent_name="{agent_name}" and focus_area="{focus_area}".

Pull request metadata:
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
{diff_section}"""

    def _build_catalog_prompt(self, pr: PullRequestSnapshot) -> str:
        skill_prompt = self._load_skill_prompt(self.analysis_skill_file)
        diff_section = ""
        if pr.diff_text:
            truncated_diff = pr.diff_text[:60_000]
            if len(pr.diff_text) > 60_000:
                truncated_diff += "\n... (diff truncated)"
            diff_section = f"""

Code diff (patch):
```
{truncated_diff}
```
"""
        return f"""{skill_prompt + chr(10) + chr(10) if skill_prompt else ""}You are analyzing a pull request for post-sync report generation and catalog routing.

Do not perform a normal PR review. Focus on triage, risk bucketing, and which downstream catalogs should include this PR.

Return ONLY valid JSON:
{{
  "agent_name": "catalog-router",
  "focus_area": "catalog routing and prioritization",
  "verdict": "low|medium|high",
  "score": 0.0-1.0,
  "summary": "2 short sentences about why this PR belongs in certain reports/catalogs",
  "recommendations": ["short routing action, e.g. 'include in security-risk and needs-review'"],
  "tags": ["optional-short-tag"],
  "suggested_catalogs": ["needs-review|aging-prs|security-risk|release-risk|interesting-issues|recently-updated"],
  "confidence": 0.0-1.0
}}

Routing guidance:
- `needs-review`: prioritize for human review
- `aging-prs`: stale open PR
- `security-risk`: security-sensitive scope
- `release-risk`: risky breadth or regression potential
- `recently-updated`: meaningful fresh activity today
- `interesting-issues`: generally not applicable for PRs

Pull request metadata:
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
{diff_section}"""

    def _build_comprehensive_prompt(self, pr: PullRequestSnapshot) -> str:
        """Build prompt for comprehensive single-pass PR review covering all aspects."""
        skill_prompt = self._load_skill_prompt(self.review_skill_file)
        diff_section = ""
        if pr.diff_text:
            truncated_diff = pr.diff_text[:80_000]
            if len(pr.diff_text) > 80_000:
                truncated_diff += "\n... (diff truncated)"
            diff_section = f"""

Code diff (patch):
```
{truncated_diff}
```
"""
        return f"""{skill_prompt + chr(10) + chr(10) if skill_prompt else ""}You are conducting a comprehensive PR code review.

Analyze the pull request below according to the review aspects defined in the skill above. You may inspect repository files for extra context if needed.

Follow the "Review Style" and "Automated Review Output Format" guidance from the skill above.

Return ONLY valid JSON:
{{
  "findings": [
    {{ <finding object per skill format> }}
  ]
}}

Pull request metadata:
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
{diff_section}"""

    def analyze_pr(self, agent_name: str, focus_area: str, pr: PullRequestSnapshot) -> PRSubagentFinding:
        prompt = self._build_prompt(agent_name, focus_area, pr)
        try:
            return self._run_prompt(prompt, agent_name, focus_area, pr)
        except Exception as exc:
            fallback = super().analyze_pr(agent_name, focus_area, pr)
            detail = self._format_failure_detail(exc)
            fallback.summary = f"(fallback heuristic: {type(exc).__name__}: {detail[:160]}) {fallback.summary}"
            return fallback

    def _run_comprehensive_review(self, pr: PullRequestSnapshot) -> list[PRSubagentFinding]:
        prompt = self._build_comprehensive_prompt(pr)
        payload = self._run_raw_prompt(prompt)
        if not isinstance(payload, dict):
            raise ValueError("Expected JSON object from model")
        findings_data = payload.get("findings")
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

    def analyze_pr_comprehensive(self, pr: PullRequestSnapshot) -> list[PRSubagentFinding]:
        """Run comprehensive single-pass review covering all aspects."""
        try:
            return self._run_comprehensive_review(pr)
        except Exception:
            return super().analyze_pr_comprehensive(pr)

    def analyze_pr_with_self_review(self, pr: PullRequestSnapshot) -> list[PRSubagentFinding]:
        """Three-step self-review: generate → critique → revise."""
        logger.info("Step 1/3: Generating initial review for PR #%d", pr.number)

        # Step 1: Generate initial review
        try:
            initial_findings = self._run_comprehensive_review(pr)
        except Exception as exc:
            logger.warning("Step 1 failed, falling back to heuristic: %s", self._format_failure_detail(exc))
            return super().analyze_pr_comprehensive(pr)

        # Step 2: Critique the initial review
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
            logger.warning("Step 2 (critique) failed, using initial findings: %s", self._format_failure_detail(exc))
            return initial_findings

        # Step 3: Revise based on critique
        logger.info("Step 3/3: Revising findings based on critique for PR #%d", pr.number)
        revision_prompt = self._build_revision_prompt(pr, initial_findings, critique_payload)
        try:
            revised_payload = self._run_raw_prompt(revision_prompt)
            if not isinstance(revised_payload, dict):
                logger.warning("Revision returned non-dict, using initial findings")
                return initial_findings

            revised_findings_data = revised_payload.get("findings")
            if not isinstance(revised_findings_data, list):
                logger.warning("Revision missing findings array, using initial findings")
                return initial_findings

            revised_findings: list[PRSubagentFinding] = []
            for item in revised_findings_data:
                if not isinstance(item, dict):
                    continue
                finding = PRSubagentFinding.model_validate(item)
                finding.score = _clamp(finding.score, 0.0, 1.0)
                finding.confidence = _clamp(finding.confidence, 0.0, 1.0)
                revised_findings.append(finding)

            if not revised_findings:
                logger.warning("No valid revised findings, using initial findings")
                return initial_findings

            logger.info("Self-review complete for PR #%d: %d findings revised", pr.number, len(revised_findings))
            return revised_findings

        except Exception as exc:
            logger.warning("Step 3 (revision) failed, using initial findings: %s", self._format_failure_detail(exc))
            return initial_findings

    def _build_critique_prompt(self, pr: PullRequestSnapshot, findings: list[PRSubagentFinding]) -> str:
        """Build prompt for critiquing initial review findings."""
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
        """Build prompt for revising findings based on critique."""
        review_skill_prompt = self._load_skill_prompt(self.review_skill_file)
        original_json = json.dumps([f.model_dump() for f in original_findings], indent=2)
        critique_json = json.dumps(critique, indent=2)

        diff_section = ""
        if pr.diff_text:
            truncated_diff = pr.diff_text[:80_000]
            if len(pr.diff_text) > 80_000:
                truncated_diff += "\n... (diff truncated)"
            diff_section = f"""

Code diff (patch):
```
{truncated_diff}
```
"""

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
{diff_section}

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


    def analyze_catalog_routing(self, pr: PullRequestSnapshot) -> PRSubagentFinding:
        prompt = self._build_catalog_prompt(pr)
        try:
            return self._run_prompt(prompt, "catalog-router", "catalog routing and prioritization", pr)
        except Exception as exc:
            fallback = super().analyze_catalog_routing(pr)
            detail = self._format_failure_detail(exc)
            fallback.summary = f"(fallback heuristic: {type(exc).__name__}: {detail[:160]}) {fallback.summary}"
            return fallback

    def analyze_catalog_routing_batch(self, prs: list[PullRequestSnapshot]) -> dict[int, PRSubagentFinding]:
        if not prs:
            return {}
        prompt = self._build_catalog_batch_prompt(prs)
        try:
            payload = self._run_raw_prompt(prompt)
            findings = payload.get("findings") if isinstance(payload, dict) else None
            if not isinstance(findings, dict):
                raise ValueError("Missing findings object in batch result")
            results: dict[int, PRSubagentFinding] = {}
            for pr in prs:
                raw_finding = findings.get(str(pr.number))
                if not isinstance(raw_finding, dict):
                    continue
                raw_finding["agent_name"] = "catalog-router"
                raw_finding["focus_area"] = "catalog routing and prioritization"
                finding = PRSubagentFinding.model_validate(raw_finding)
                finding.score = _clamp(finding.score, 0.0, 1.0)
                finding.confidence = _clamp(finding.confidence, 0.0, 1.0)
                results[pr.number] = finding
            return results
        except Exception:
            return super().analyze_catalog_routing_batch(prs)

    def analyze_attention_batch(self, contexts: list[PRAttentionContext]) -> dict[int, PRAttentionDecision]:
        if not contexts:
            return {}
        prompt = _build_attention_batch_prompt(self._load_skill_prompt(self.analysis_skill_file), contexts)
        try:
            payload = self._run_raw_prompt(prompt)
            raw_decisions = payload.get("decisions") if isinstance(payload, dict) else None
            if not isinstance(raw_decisions, dict):
                raise ValueError("Missing decisions object in batch result")
            decisions: dict[int, PRAttentionDecision] = {}
            for ctx in contexts:
                raw = raw_decisions.get(str(ctx.pr_number))
                if not isinstance(raw, dict):
                    continue
                raw["pr_number"] = ctx.pr_number
                decision = PRAttentionDecision.model_validate(raw)
                decision.priority_score = _clamp(decision.priority_score, 0.0, 10.0)
                decision.confidence = _clamp(decision.confidence, 0.0, 1.0)
                decisions[ctx.pr_number] = decision
            return decisions
        except Exception:
            return super().analyze_attention_batch(contexts)

    def _build_catalog_batch_prompt(self, prs: list[PullRequestSnapshot]) -> str:
        skill_prompt = self._load_skill_prompt(self.analysis_skill_file)
        sections: list[str] = []
        for pr in prs:
            diff_section = ""
            if pr.diff_text:
                truncated_diff = pr.diff_text[:20_000]
                if len(pr.diff_text) > 20_000:
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
        return (skill_prompt + "\n\n" if skill_prompt else "") + """You are analyzing multiple pull requests for post-sync report generation and catalog routing.

This is a batch routing task, not a normal PR review. For each PR, decide which reports/catalogs should include it and give a short routing summary.

Return ONLY valid JSON in this shape:
{
  "findings": {
    "<pr_number>": {
      "verdict": "low|medium|high",
      "score": 0.0-1.0,
      "summary": "2 short routing sentences",
      "recommendations": ["short routing action"],
      "tags": ["optional-short-tag"],
      "suggested_catalogs": ["needs-review|aging-prs|security-risk|release-risk|interesting-issues|recently-updated"],
      "confidence": 0.0-1.0
    }
  }
}

Routing guidance:
- `needs-review`: prioritize for human review
- `aging-prs`: stale open PR
- `security-risk`: security-sensitive scope
- `release-risk`: risky breadth or regression potential
- `recently-updated`: meaningful fresh activity today
- `interesting-issues`: generally not applicable for PRs

Pull requests:
""" + "\n\n".join(sections)

    def _run_prompt(self, prompt: str, agent_name: str, focus_area: str, pr: PullRequestSnapshot) -> PRSubagentFinding:
        try:
            data = self._run_raw_prompt(prompt)
            if not isinstance(data, dict):
                raise ValueError("Expected JSON object from model")
            data["agent_name"] = agent_name
            data["focus_area"] = focus_area
            finding = PRSubagentFinding.model_validate(data)
            finding.score = _clamp(finding.score, 0.0, 1.0)
            finding.confidence = _clamp(finding.confidence, 0.0, 1.0)
            return finding
        except Exception:
            raise

    def _run_raw_prompt(self, prompt: str) -> object:
        last_message_path: str | None = None
        try:
            with tempfile.NamedTemporaryFile(prefix="codex_last_", suffix=".txt", delete=False) as tmp:
                last_message_path = tmp.name
            cmd = [
                self.command,
                "exec",
                "--full-auto",
                "--skip-git-repo-check",
                "-c",
                f'model_reasoning_effort="{self.reasoning_effort}"',
                "--output-last-message",
                last_message_path,
            ]
            if self.model:
                cmd.extend(["-m", self.model])
            _log_cli_invocation(self.provider, cmd, prompt)
            cmd.append(prompt)
            proc = subprocess.run(
                cmd,
                check=False,
                capture_output=True,
                text=True,
                timeout=self.timeout_sec,
                cwd=self.repo_dir or None,
                env=_codex_subprocess_env(),
            )
            raw_output = ""
            if last_message_path:
                try:
                    raw_output = Path(last_message_path).read_text(encoding="utf-8").strip()
                except Exception:
                    raw_output = ""
            stdout = getattr(proc, "stdout", "")
            stderr = getattr(proc, "stderr", "")
            returncode = getattr(proc, "returncode", 0)
            source = raw_output or stdout
            if source:
                try:
                    return ClaudeCodeLocalAdapter._extract_json_payload(source)
                except ValueError:
                    if returncode == 0:
                        raise
            if returncode != 0:
                raise subprocess.CalledProcessError(
                    returncode=returncode,
                    cmd=getattr(proc, "args", cmd),
                    output=stdout,
                    stderr=stderr,
                )
            raise ValueError("No valid JSON payload found in model output")
        finally:
            if last_message_path:
                try:
                    Path(last_message_path).unlink(missing_ok=True)
                except Exception:
                    pass
