from __future__ import annotations

import os
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from polaris_pr_intel.llm._base_local_cli import BaseLocalCLIAdapter
from polaris_pr_intel.llm._claude_code_local import ClaudeCodeLocalAdapter
from polaris_pr_intel.models import PRSubagentFinding, PullRequestSnapshot


def _codex_subprocess_env() -> dict[str, str]:
    env = os.environ.copy()
    for key in list(env):
        if key.startswith("CODEX_") and key != "CODEX_HOME":
            env.pop(key, None)
    return env


@dataclass
class CodexLocalAdapter(BaseLocalCLIAdapter):
    provider: str = "codex_local"
    model: str = "gpt-5.4"
    command: str = "codex"
    timeout_sec: int = 900
    max_turns: int = 15
    reasoning_effort: str = "medium"
    repo_dir: str = ""
    fail_review_job_on_generation_error: bool = True

    def _wrap_skill_prompt(self, skill_body: str) -> str:
        return (
            "Use the following project skill as guidance. Follow its triage and review rules, "
            "but obey the task-specific JSON format and scope in the user prompt.\n\n" + skill_body
        )

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

    def _format_review_failure(self, exc: Exception) -> str:
        return self._format_failure_detail(exc)

    def _format_followup_failure(self, exc: Exception) -> str:
        return self._format_failure_detail(exc)

    def _build_prompt(self, agent_name: str, focus_area: str, pr: PullRequestSnapshot) -> str:
        return self._build_pr_prompt_document(
            skill_prompt=self._load_skill_prompt(self.review_skill_file),
            instructions=f"""You are an expert code reviewer acting as a specialized PR review subagent.
Your focus area is: {focus_area}

Analyze the pull request below with concrete, code-specific findings.
You may inspect repository files for extra context if needed.

Follow the "Review Style" and "Automated Review Output Format" guidance from the skill above.

Return ONLY valid JSON for agent_name="{agent_name}" and focus_area="{focus_area}".""",
            pr=pr,
            diff_limit=80_000,
        )

    def _build_catalog_prompt(self, pr: PullRequestSnapshot) -> str:
        return self._build_pr_prompt_document(
            skill_prompt=self._load_skill_prompt(self.analysis_skill_file),
            instructions="""You are analyzing a pull request for post-sync report generation and catalog routing.

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
""",
            pr=pr,
            diff_limit=60_000,
        )

    def _build_comprehensive_prompt(self, pr: PullRequestSnapshot) -> str:
        return self._build_pr_prompt_document(
            skill_prompt=self._load_skill_prompt(self.review_skill_file),
            instructions="""You are conducting a comprehensive PR code review.

Analyze the pull request below according to the review aspects defined in the skill above. You may inspect repository files for extra context if needed.

Follow the "Review Style" and "Automated Review Output Format" guidance from the skill above.

Return ONLY valid JSON:
{{
  "findings": [
    {{ <finding object per skill format> }}
  ]
}}""",
            pr=pr,
            diff_limit=80_000,
        )

    def analyze_pr(self, agent_name: str, focus_area: str, pr: PullRequestSnapshot) -> PRSubagentFinding:
        try:
            return self._run_prompt(self._build_prompt(agent_name, focus_area, pr), agent_name, focus_area, pr)
        except Exception as exc:
            fallback = super().analyze_pr(agent_name, focus_area, pr)
            detail = self._format_failure_detail(exc)
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
            fallback = super().analyze_catalog_routing(pr)
            detail = self._format_failure_detail(exc)
            fallback.summary = f"(fallback heuristic: {type(exc).__name__}: {detail[:160]}) {fallback.summary}"
            return fallback

    def _build_catalog_batch_prompt(self, prs: list[PullRequestSnapshot]) -> str:
        return self._build_catalog_batch_prompt_document(
            skill_prompt=self._load_skill_prompt(self.analysis_skill_file),
            instructions="""You are analyzing multiple pull requests for post-sync report generation and catalog routing.

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
""",
            prs=prs,
            diff_limit=20_000,
        )

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
            self._log_cli_invocation(cmd, prompt)
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
