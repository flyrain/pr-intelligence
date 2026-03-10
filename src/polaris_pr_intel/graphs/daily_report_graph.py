from __future__ import annotations

from typing import Any

from langgraph.graph import END, StateGraph

from polaris_pr_intel.agents.daily_reporter import DailyReporterAgent
from polaris_pr_intel.graphs.state import PRIntelState
from polaris_pr_intel.publish.console import ConsolePublisher
from polaris_pr_intel.store.base import Repository


class DailyReportGraph:
    def __init__(self, repo: Repository) -> None:
        self.repo = repo
        self.reporter = DailyReporterAgent()
        self.publisher = ConsolePublisher()
        self.graph = self._build()

    def _build(self):
        g = StateGraph(PRIntelState)
        g.add_node("generate_report", self.generate_report)
        g.add_node("publish_report", self.publish_report)

        g.set_entry_point("generate_report")
        g.add_edge("generate_report", "publish_report")
        g.add_edge("publish_report", END)
        return g.compile()

    def generate_report(self, state: PRIntelState) -> dict[str, Any]:
        report = self.reporter.run(self.repo)
        self.repo.save_daily_report(report)
        return {"daily_report": report}

    def publish_report(self, state: PRIntelState) -> dict[str, Any]:
        report = state["daily_report"]
        self.publisher.publish_daily_report(report)
        return {"notifications": [f"daily-report:{report.date}"]}

    def invoke(self) -> PRIntelState:
        # LangGraph requires at least one state key in the initial input.
        return self.graph.invoke({"notifications": []})
