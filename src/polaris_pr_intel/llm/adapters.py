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
    timeout_sec: int = 45

    @staticmethod
    def _extract_json_blob(text: str) -> str:
        cleaned = text.strip()
        if cleaned.startswith("```"):
            lines = cleaned.splitlines()
            # Remove opening/closing fences if present.
            if lines and lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].startswith("```"):
                lines = lines[:-1]
            cleaned = "\n".join(lines).strip()
        return cleaned

    def _build_prompt(self, agent_name: str, focus_area: str, pr: PullRequestSnapshot) -> str:
        return f"""You are a PR review subagent.
Analyze this pull request for the given focus area and return ONLY JSON.

Required JSON schema:
{{
  "agent_name": "{agent_name}",
  "focus_area": "{focus_area}",
  "verdict": "low|medium|high",
  "score": 0.0-1.0,
  "summary": "short summary",
  "recommendations": ["item1", "item2"],
  "confidence": 0.0-1.0
}}

Pull request context:
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
- body:
{pr.body[:4000]}
"""

    def analyze_pr(self, agent_name: str, focus_area: str, pr: PullRequestSnapshot) -> PRSubagentFinding:
        prompt = self._build_prompt(agent_name, focus_area, pr)
        try:
            proc = subprocess.run(
                [self.command, "--print", prompt],
                check=True,
                capture_output=True,
                text=True,
                timeout=self.timeout_sec,
            )
            blob = self._extract_json_blob(proc.stdout)
            data = json.loads(blob)
            data["agent_name"] = agent_name
            data["focus_area"] = focus_area
            finding = PRSubagentFinding.model_validate(data)
            finding.score = _clamp(finding.score, 0.0, 1.0)
            finding.confidence = _clamp(finding.confidence, 0.0, 1.0)
            return finding
        except Exception:
            # Fall back to deterministic behavior when local CLI is unavailable.
            fallback = super().analyze_pr(agent_name, focus_area, pr)
            fallback.summary = f"(fallback heuristic) {fallback.summary}"
            return fallback
