from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from apscheduler.schedulers.background import BackgroundScheduler

from polaris_pr_intel.graphs.daily_report_graph import DailyReportGraph

if TYPE_CHECKING:
    from polaris_pr_intel.agents.issue_insight import IssueInsightAgent
    from polaris_pr_intel.agents.review_need import ReviewNeedAgent
    from polaris_pr_intel.ingest import SnapshotIngestor
    from polaris_pr_intel.store.base import Repository

logger = logging.getLogger(__name__)


class DailyScheduler:
    def __init__(
        self,
        graph: DailyReportGraph,
        snapshot_ingestor: SnapshotIngestor | None = None,
        repo: Repository | None = None,
        review_need_agent: ReviewNeedAgent | None = None,
        issue_insight_agent: IssueInsightAgent | None = None,
        enable_periodic_refresh: bool = False,
        refresh_interval_hours: int = 2,
    ) -> None:
        self.graph = graph
        self.snapshot_ingestor = snapshot_ingestor
        self.repo = repo
        self.review_need_agent = review_need_agent
        self.issue_insight_agent = issue_insight_agent
        self.enable_periodic_refresh = enable_periodic_refresh
        self.refresh_interval_hours = refresh_interval_hours
        self.scheduler = BackgroundScheduler(timezone="UTC")

    def _run_full_refresh(self) -> None:
        """Full refresh: sync GitHub data → recompute scores → run analysis → generate report."""
        if not all([self.snapshot_ingestor, self.repo, self.review_need_agent, self.issue_insight_agent]):
            logger.warning("Periodic refresh skipped: missing dependencies")
            return

        try:
            logger.info("Starting scheduled refresh (every %d hours)", self.refresh_interval_hours)

            # Step 1: Sync GitHub data
            synced = self.snapshot_ingestor.sync_recent(per_page=100, max_pages=20, since=None, prune_missing_open_prs=True)
            logger.info("Synced %d PRs and %d issues", synced.get("prs", 0), synced.get("issues", 0))

            # Step 2: Recompute scores
            prs_scored = 0
            issues_scored = 0
            needs_review = 0
            interesting_issues = 0

            for pr in self.repo.prs.values():
                if pr.state != "open":
                    continue
                signal = self.review_need_agent.run(pr)
                self.repo.save_review_signal(signal)
                prs_scored += 1
                if signal.needs_review:
                    needs_review += 1

            for issue in self.repo.issues.values():
                if issue.state != "open":
                    continue
                signal = self.issue_insight_agent.run(issue)
                self.repo.save_issue_signal(signal)
                issues_scored += 1
                if signal.interesting:
                    interesting_issues += 1

            logger.info("Scored %d PRs (%d need review) and %d issues (%d interesting)",
                       prs_scored, needs_review, issues_scored, interesting_issues)

            # Step 3: Run analysis & generate report
            out = self.graph.invoke()
            logger.info("Generated report: %s", out.get("notifications", []))

        except Exception as exc:
            logger.exception("Scheduled refresh failed: %s", exc)

    def start(self) -> None:
        if self.scheduler.running:
            return

        # Daily report at 16:00 UTC (09:00 America/Los_Angeles during PST)
        self.scheduler.add_job(self.graph.invoke, "cron", hour=16, minute=0, id="daily-report", replace_existing=True)
        logger.info("Scheduled daily report at 16:00 UTC")

        # Periodic refresh every N hours
        if self.enable_periodic_refresh and all([self.snapshot_ingestor, self.repo, self.review_need_agent, self.issue_insight_agent]):
            self.scheduler.add_job(
                self._run_full_refresh,
                "interval",
                hours=self.refresh_interval_hours,
                id="periodic-refresh",
                replace_existing=True
            )
            logger.info("Scheduled periodic refresh every %d hours", self.refresh_interval_hours)

        self.scheduler.start()

    def stop(self) -> None:
        if self.scheduler.running:
            self.scheduler.shutdown(wait=False)
