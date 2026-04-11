from __future__ import annotations

from typing import Any

from langgraph.graph import END, StateGraph

from polaris_pr_intel.agents.derived_analysis import DerivedAnalysisAgent
from polaris_pr_intel.config import Settings
from polaris_pr_intel.llm.llm_adapter import LLMAdapter
from polaris_pr_intel.graphs.state import PRIntelState
from polaris_pr_intel.publish.console import ConsolePublisher
from polaris_pr_intel.store.base import Repository


class DailyReportGraph:
    def __init__(self, repo: Repository, llm: LLMAdapter, settings: Settings) -> None:
        self.repo = repo
        self.analysis = DerivedAnalysisAgent(repo, llm=llm, settings=settings)
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
        analysis_run = self.analysis.run()
        report_markdown = self.analysis.render_markdown(analysis_run)
        self.repo.save_analysis_run(analysis_run)
        return {"analysis_run": analysis_run, "report_markdown": report_markdown}

    def publish_report(self, state: PRIntelState) -> dict[str, Any]:
        report_markdown = state["report_markdown"]
        report_date = state["analysis_run"].created_at.strftime("%Y-%m-%d")
        self.publisher.publish_daily_report(report_markdown)
        return {"notifications": [f"daily-report:{report_date}"]}

    def invoke(self) -> PRIntelState:
        # LangGraph requires at least one state key in the initial input.
        return self.graph.invoke({"notifications": []})
