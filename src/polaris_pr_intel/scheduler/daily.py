from __future__ import annotations

from apscheduler.schedulers.background import BackgroundScheduler

from polaris_pr_intel.graphs.daily_report_graph import DailyReportGraph


class DailyScheduler:
    def __init__(self, graph: DailyReportGraph) -> None:
        self.graph = graph
        self.scheduler = BackgroundScheduler(timezone="UTC")

    def start(self) -> None:
        if self.scheduler.running:
            return
        # Daily at 16:00 UTC (09:00 America/Los_Angeles during PST)
        self.scheduler.add_job(self.graph.invoke, "cron", hour=16, minute=0, id="daily-report", replace_existing=True)
        self.scheduler.start()

    def stop(self) -> None:
        if self.scheduler.running:
            self.scheduler.shutdown(wait=False)
