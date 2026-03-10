from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass

from polaris_pr_intel.models import PRSubagentFinding, PullRequestSnapshot


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


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
            confidence=0.65,
        )


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

    @staticmethod
    def _extract_json_from_result(text: str) -> dict:
        """Extract JSON finding from Claude's output.

        With --output-format json, Claude returns a JSON object with a "result"
        field containing the final text response.  We look for a JSON object
        matching our schema inside that text.
        """
        # First try: the whole output is the --output-format json envelope.
        try:
            envelope = json.loads(text)
            if isinstance(envelope, dict) and "result" in envelope:
                text = envelope["result"]
        except (json.JSONDecodeError, TypeError):
            pass

        # Try to parse the (possibly extracted) text directly.
        try:
            obj = json.loads(text.strip())
            if isinstance(obj, dict) and "verdict" in obj:
                return obj
        except (json.JSONDecodeError, TypeError):
            pass

        # Look for a JSON code block in the response.
        import re
        for match in re.finditer(r"```(?:json)?\s*\n(.*?)\n```", text, re.DOTALL):
            try:
                obj = json.loads(match.group(1).strip())
                if isinstance(obj, dict) and "verdict" in obj:
                    return obj
            except (json.JSONDecodeError, TypeError):
                continue

        # Last resort: find first { ... } blob that looks right.
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
                            obj = json.loads(text[brace_start : i + 1])
                            if isinstance(obj, dict) and "verdict" in obj:
                                return obj
                        except (json.JSONDecodeError, TypeError):
                            pass
                        break

        raise ValueError("No valid finding JSON found in Claude output")

    def _build_prompt(self, agent_name: str, focus_area: str, pr: PullRequestSnapshot) -> str:
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
        return f"""You are an expert code reviewer acting as a specialized PR review subagent.
Your focus area is: {focus_area}

Analyze the pull request below. You have access to tools — use them to read source files
for additional context when the diff alone is not enough to assess the change. For example,
read surrounding code to understand how changed functions are called, check test coverage,
or verify that security patterns are applied consistently.

After your analysis, respond with ONLY valid JSON matching this schema:
{{
  "agent_name": "{agent_name}",
  "focus_area": "{focus_area}",
  "verdict": "low|medium|high",
  "score": 0.0-1.0,
  "summary": "2-3 sentence analysis with specific findings from the code",
  "recommendations": ["specific actionable item referencing code"],
  "confidence": 0.0-1.0
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
            cmd = [
                self.command,
                "--dangerously-skip-permissions",
                "--output-format", "json",
                "--max-turns", str(self.max_turns),
                "--allowedTools", "Read,Grep,Glob,Bash",
                "-p", prompt,
            ]
            proc = subprocess.run(
                cmd,
                check=True,
                capture_output=True,
                text=True,
                timeout=self.timeout_sec,
                cwd=self.repo_dir or None,
            )
            data = self._extract_json_from_result(proc.stdout)
            data["agent_name"] = agent_name
            data["focus_area"] = focus_area
            finding = PRSubagentFinding.model_validate(data)
            finding.score = _clamp(finding.score, 0.0, 1.0)
            finding.confidence = _clamp(finding.confidence, 0.0, 1.0)
            return finding
        except Exception:
            fallback = super().analyze_pr(agent_name, focus_area, pr)
            fallback.summary = f"(fallback heuristic) {fallback.summary}"
            return fallback
