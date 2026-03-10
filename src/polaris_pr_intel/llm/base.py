from __future__ import annotations

from typing import Protocol

from polaris_pr_intel.models import PRSubagentFinding, PullRequestSnapshot


class LLMAdapter(Protocol):
    provider: str
    model: str

    def analyze_pr(self, agent_name: str, focus_area: str, pr: PullRequestSnapshot) -> PRSubagentFinding: ...
