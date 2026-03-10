from __future__ import annotations

from polaris_pr_intel.models import DailyReport


class ConsolePublisher:
    def publish_daily_report(self, report: DailyReport) -> None:
        print(report.markdown)
