from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass

from polaris_pr_intel.llm._base_local_cli import BaseLocalCLIAdapter
from polaris_pr_intel.models import PRSubagentFinding, PullRequestSnapshot


@dataclass
class ClaudeCodeLocalAdapter(BaseLocalCLIAdapter):
    provider: str = "claude_code_local"
    model: str = "opus"
    command: str = "claude"
    timeout_sec: int = 300
    max_turns: int = 15
    repo_dir: str = ""

    @staticmethod
    def _detail_from_exception(exc: Exception) -> str:
        detail = str(exc)
        if isinstance(exc, subprocess.CalledProcessError):
            stderr = (exc.stderr or "").strip().replace("\n", " ")
            stdout = (exc.stdout or "").strip().replace("\n", " ")
            detail = stderr or stdout or detail
        return detail

    @classmethod
    def _is_auth_failure(cls, exc: Exception) -> bool:
        detail = cls._detail_from_exception(exc)
        return "Failed to authenticate" in detail or "API Error: 401" in detail or "Not logged in" in detail

    def _format_review_failure(self, exc: Exception) -> str:
        return self._detail_from_exception(exc)

    def _should_fail_review_job(self, exc: Exception) -> bool:
        return self._is_auth_failure(exc) or super()._should_fail_review_job(exc)

    def _review_failure(self, exc: Exception) -> RuntimeError:
        if self._is_auth_failure(exc):
            return RuntimeError(
                "claude-code-local authentication failed. Run `claude auth login` (or `claude setup-token`) and retry."
            )
        return RuntimeError(f"claude-code-local review failed: {self._detail_from_exception(exc)}")

    def _maybe_raise_non_review_failure(self, exc: Exception) -> None:
        if self._is_auth_failure(exc):
            raise self._review_failure(exc) from exc

    def _wrap_skill_prompt(self, skill_body: str) -> str:
        return (
            "Use the following as your review checklist. Apply these checks to the PR. "
            "Ignore any output format instructions in the skill — use the JSON format from the user prompt. "
            "Keep all text short and casual — write like a teammate, not a report.\n\n"
            + skill_body
        )

    @staticmethod
    def _extract_json_payload(text: str) -> object:
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

    def _build_prompt(self, agent_name: str, focus_area: str, pr: PullRequestSnapshot) -> str:
        return self._build_pr_prompt_document(
            skill_prompt=self._load_skill_prompt(self.review_skill_file),
            instructions=f"""You are a code reviewer focused on: {focus_area}

Review the PR below. Use tools to read source files when the diff isn't enough.

Follow the "Review Style" and "Automated Review Output Format" guidance from the skill above.

Respond with ONLY valid JSON for agent_name="{agent_name}" and focus_area="{focus_area}".""",
            pr=pr,
            diff_limit=80_000,
        )

    def _build_comprehensive_prompt(self, pr: PullRequestSnapshot) -> str:
        return self._build_pr_prompt_document(
            skill_prompt=self._load_skill_prompt(self.review_skill_file),
            instructions="""You are conducting a comprehensive code review covering multiple aspects.

Review the PR below according to the review aspects defined in the skill above. Use tools to read source files when the diff isn't enough.

Follow the "Review Style" and "Automated Review Output Format" guidance from the skill above.

Respond with ONLY valid JSON:
{{
  "findings": [
    {{ <finding object per skill format> }}
  ]
}}""",
            pr=pr,
            diff_limit=80_000,
        )

    def _build_catalog_prompt(self, pr: PullRequestSnapshot) -> str:
        return self._build_pr_prompt_document(
            skill_prompt=self._load_skill_prompt(self.analysis_skill_file),
            instructions="""You are classifying a pull request for post-sync reporting and catalog routing.

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
""",
            pr=pr,
            diff_limit=60_000,
        )

    def analyze_pr(self, agent_name: str, focus_area: str, pr: PullRequestSnapshot) -> PRSubagentFinding:
        try:
            return self._run_prompt(self._build_prompt(agent_name, focus_area, pr), agent_name, focus_area, pr)
        except Exception as exc:
            if self._is_auth_failure(exc):
                raise self._review_failure(exc) from exc
            fallback = super().analyze_pr(agent_name, focus_area, pr)
            detail = self._detail_from_exception(exc)
            fallback.summary = f"(fallback heuristic: {type(exc).__name__}: {detail[:160]}) {fallback.summary}"
            return fallback

    def analyze_catalog_routing(self, pr: PullRequestSnapshot) -> PRSubagentFinding:
        try:
            return self._run_prompt(
                self._build_catalog_prompt(pr),
                "catalog-router",
                "catalog routing and prioritization",
                pr,
            )
        except Exception as exc:
            if self._is_auth_failure(exc):
                raise self._review_failure(exc) from exc
            fallback = super().analyze_catalog_routing(pr)
            detail = self._detail_from_exception(exc)
            fallback.summary = f"(fallback heuristic: {type(exc).__name__}: {detail[:160]}) {fallback.summary}"
            return fallback

    def _build_catalog_batch_prompt(self, prs: list[PullRequestSnapshot]) -> str:
        return self._build_catalog_batch_prompt_document(
            skill_prompt=self._load_skill_prompt(self.analysis_skill_file),
            instructions="""You are classifying multiple pull requests for post-sync reporting and catalog routing.

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
""",
            prs=prs,
            diff_limit=20_000,
        )

    def _run_raw_prompt(self, prompt: str) -> object:
        cmd = [
            self.command,
            "--print",
            "--dangerously-skip-permissions",
            "--output-format",
            "json",
            "--allowedTools",
            "Read,Grep,Glob,Bash",
            "--setting-sources",
            "user,project,local",
        ]
        if self.model and self.model not in {"local-heuristic"}:
            cmd.extend(["--model", self.model])
        self._log_cli_invocation(cmd, prompt)
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
