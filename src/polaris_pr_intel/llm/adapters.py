from __future__ import annotations

import json
import logging
import re
import shlex
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from polaris_pr_intel.models import PRSubagentFinding, PullRequestSnapshot

logger = logging.getLogger(__name__)


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


def _log_cli_invocation(provider: str, cmd: list[str], prompt: str) -> None:
    logger.info(
        "Invoking %s LLM command: %s [prompt_chars=%d]",
        provider,
        shlex.join([*cmd, "<prompt>"]),
        len(prompt),
    )


@dataclass
class HeuristicLLMAdapter:
    provider: str = "heuristic"
    model: str = "local-heuristic"

    def analyze_pr(self, agent_name: str, focus_area: str, pr: PullRequestSnapshot) -> PRSubagentFinding:
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

    def analyze_pr_comprehensive(self, pr: PullRequestSnapshot) -> list[PRSubagentFinding]:
        """Comprehensive PR review covering all aspects. Heuristic fallback generates findings for all areas."""
        return [
            self.analyze_pr("code-risk", "code risk and complexity", pr),
            self.analyze_pr("test-impact", "test impact and coverage", pr),
            self.analyze_pr("docs-quality", "documentation and release notes", pr),
            self.analyze_pr("security-signal", "security and permission model", pr),
        ]

    def analyze_catalog_routing(self, pr: PullRequestSnapshot) -> PRSubagentFinding:
        return self.analyze_pr("catalog-router", "catalog routing and prioritization", pr)

    def analyze_catalog_routing_batch(self, prs: list[PullRequestSnapshot]) -> dict[int, PRSubagentFinding]:
        return {pr.number: self.analyze_catalog_routing(pr) for pr in prs}


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
            skill_prompt = self._load_skill_prompt(self.review_skill_file)
            if skill_prompt:
                logger.info("Loaded review skill (%d chars) from %s", len(skill_prompt), self.review_skill_file)
                cmd.extend(["--append-system-prompt", skill_prompt])
            else:
                logger.warning("No review skill loaded (skill_file=%r)", self.review_skill_file)
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
    repo_dir: str = ""
    review_skill_file: str = ""
    analysis_skill_file: str = ""

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
            detail = str(exc)
            if isinstance(exc, subprocess.CalledProcessError):
                stderr = (exc.stderr or "").strip().replace("\n", " ")
                stdout = (exc.stdout or "").strip().replace("\n", " ")
                detail = stderr or stdout or detail
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
            # Fallback to heuristic multi-finding
            return super().analyze_pr_comprehensive(pr)

    def analyze_catalog_routing(self, pr: PullRequestSnapshot) -> PRSubagentFinding:
        prompt = self._build_catalog_prompt(pr)
        try:
            return self._run_prompt(prompt, "catalog-router", "catalog routing and prioritization", pr)
        except Exception as exc:
            fallback = super().analyze_catalog_routing(pr)
            detail = str(exc)
            if isinstance(exc, subprocess.CalledProcessError):
                stderr = (exc.stderr or "").strip().replace("\n", " ")
                stdout = (exc.stdout or "").strip().replace("\n", " ")
                detail = stderr or stdout or detail
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
                "--output-last-message",
                last_message_path,
            ]
            if self.model:
                cmd.extend(["-m", self.model])
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
            raw_output = ""
            if last_message_path:
                try:
                    raw_output = Path(last_message_path).read_text(encoding="utf-8").strip()
                except Exception:
                    raw_output = ""
            return ClaudeCodeLocalAdapter._extract_json_payload(raw_output or proc.stdout)
        finally:
            if last_message_path:
                try:
                    Path(last_message_path).unlink(missing_ok=True)
                except Exception:
                    pass
