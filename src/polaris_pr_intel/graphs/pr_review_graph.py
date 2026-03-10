from __future__ import annotations

from typing import Any

from langgraph.graph import END, StateGraph

from polaris_pr_intel.agents.pr_reviewer import PRSubagentReviewer
from polaris_pr_intel.github.client import GitHubClient
from polaris_pr_intel.graphs.state import PRIntelState
from polaris_pr_intel.store.base import Repository


class PRReviewGraph:
    def __init__(self, repo: Repository, reviewer: PRSubagentReviewer, gh: GitHubClient | None = None) -> None:
        self.repo = repo
        self.reviewer = reviewer
        self.gh = gh
        self.graph = self._build()

    def _build(self):
        g = StateGraph(PRIntelState)
        g.add_node("load_pr", self.load_pr)
        g.add_node("run_subagents", self.run_subagents)
        g.add_node("aggregate_review", self.aggregate_review)
        g.add_node("persist_review", self.persist_review)

        g.set_entry_point("load_pr")
        g.add_edge("load_pr", "run_subagents")
        g.add_edge("run_subagents", "aggregate_review")
        g.add_edge("aggregate_review", "persist_review")
        g.add_edge("persist_review", END)
        return g.compile()

    def load_pr(self, state: PRIntelState) -> dict[str, Any]:
        pr_number = state.get("pr_number")
        if pr_number is None:
            return {"errors": ["missing-pr-number"], "notifications": ["review-skipped"]}
        pr = self.repo.prs.get(int(pr_number))
        if pr is None:
            return {"errors": [f"pr-not-found:{pr_number}"], "notifications": ["review-skipped"]}
        # Fetch the actual diff so subagents can review real code.
        if not pr.diff_text and self.gh is not None:
            try:
                pr.diff_text = self.gh.get_pull_request_diff(pr.number)
            except Exception:
                pass  # proceed with metadata-only review
        return {"pr": pr}

    def run_subagents(self, state: PRIntelState) -> dict[str, Any]:
        pr = state.get("pr")
        if pr is None:
            return {}
        findings = self.reviewer.run(pr)
        return {"pr_review_findings": findings}

    def aggregate_review(self, state: PRIntelState) -> dict[str, Any]:
        pr = state.get("pr")
        findings = state.get("pr_review_findings", [])
        if pr is None:
            return {}
        report = self.reviewer.aggregate(pr, findings)
        return {"pr_review_report": report}

    def persist_review(self, state: PRIntelState) -> dict[str, Any]:
        report = state.get("pr_review_report")
        if not report:
            return {}
        self.repo.save_pr_review_report(report)
        return {"notifications": [f"pr-review:{report.pr_number}"]}

    def invoke(self, pr_number: int) -> PRIntelState:
        return self.graph.invoke({"pr_number": pr_number, "notifications": []})
