from __future__ import annotations

import logging
from typing import Any

from langgraph.graph import END, StateGraph

from polaris_pr_intel.agents.pr_reviewer import PRSubagentReviewer
from polaris_pr_intel.github.client import GitHubClient
from polaris_pr_intel.graphs.state import PRIntelState
from polaris_pr_intel.store.base import Repository

logger = logging.getLogger(__name__)


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
        diff_error = ""
        if not pr.diff_text and self.gh is not None:
            try:
                pr.diff_text = self.gh.get_pull_request_diff(pr.number)
            except Exception as exc:
                diff_error = str(exc).strip()
                logger.warning("Failed to fetch diff for PR #%d: %s", pr.number, diff_error)
        if self.gh is not None and not pr.diff_text:
            reason = "Unable to load the PR patch from GitHub or local state, so code-specific review could not run."
            if diff_error:
                reason = f"{reason} Diff fetch error: {diff_error[:200]}"
            return {
                "pr": pr,
                "errors": [f"pr-diff-unavailable:{pr.number}"],
                "notifications": ["review-blocked"],
                "pr_review_report": self.reviewer.blocked_report(pr, reason),
            }
        return {"pr": pr}

    def run_subagents(self, state: PRIntelState) -> dict[str, Any]:
        if state.get("pr_review_report") is not None:
            return {}
        pr = state.get("pr")
        if pr is None:
            return {}
        findings = self.reviewer.run(pr)
        return {
            "pr_review_findings": findings,
            "pr_review_session_ids": self.reviewer.current_session_ids(),
            "pr_review_resume_context": self.reviewer.current_resume_context(),
        }

    def aggregate_review(self, state: PRIntelState) -> dict[str, Any]:
        existing_report = state.get("pr_review_report")
        if existing_report is not None:
            return {"pr_review_report": existing_report}
        pr = state.get("pr")
        findings = state.get("pr_review_findings", [])
        session_ids = state.get("pr_review_session_ids", [])
        resume_context = state.get("pr_review_resume_context", {})
        if pr is None:
            return {}
        report = self.reviewer.aggregate(
            pr,
            findings,
            session_ids=session_ids,
            resume_context=resume_context,
        )
        return {"pr_review_report": report}

    def persist_review(self, state: PRIntelState) -> dict[str, Any]:
        report = state.get("pr_review_report")
        if not report:
            return {}
        self.repo.save_pr_review_report(report)
        notifications = list(state.get("notifications", []))
        notifications.append(f"pr-review:{report.pr_number}")
        return {"notifications": notifications}

    def invoke(self, pr_number: int) -> PRIntelState:
        return self.graph.invoke({"pr_number": pr_number, "notifications": []})
