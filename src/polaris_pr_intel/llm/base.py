from __future__ import annotations

from typing import Protocol

from polaris_pr_intel.models import PRSubagentFinding, PullRequestSnapshot


class LLMAdapter(Protocol):
    provider: str
    model: str

    def analyze_pr(self, agent_name: str, focus_area: str, pr: PullRequestSnapshot) -> PRSubagentFinding: ...
    def analyze_pr_comprehensive(self, pr: PullRequestSnapshot) -> list[PRSubagentFinding]: ...
    def analyze_catalog_routing(self, pr: PullRequestSnapshot) -> PRSubagentFinding: ...
    def analyze_catalog_routing_batch(self, prs: list[PullRequestSnapshot]) -> dict[int, PRSubagentFinding]: ...
