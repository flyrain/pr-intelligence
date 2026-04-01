from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from polaris_pr_intel.graphs.daily_report_graph import DailyReportGraph
from polaris_pr_intel.refresh import run_full_refresh
from polaris_pr_intel.time_utils import configured_or_local_timezone

if TYPE_CHECKING:
    from polaris_pr_intel.agents.issue_insight import IssueInsightAgent
    from polaris_pr_intel.agents.review_need import ReviewNeedAgent
    from polaris_pr_intel.ingest import SnapshotIngestor
    from polaris_pr_intel.store.base import Repository

logger = logging.getLogger(__name__)


def _format_refresh_error(exc: Exception) -> str:
    message = str(exc).strip()
    return f"{type(exc).__name__}: {message}" if message else type(exc).__name__


def _local_timezone(timezone_name: str = ""):
    return configured_or_local_timezone(timezone_name)


def _refresh_schedule_summary(interval_minutes: int, start_hour_local: int, end_hour_local: int) -> str:
    return f"every {interval_minutes} minutes from {start_hour_local:02d}:00 to {end_hour_local:02d}:00 local time"


def build_periodic_refresh_triggers(
    timezone_name: str,
    interval_minutes: int,
    start_hour_local: int,
    end_hour_local: int,
) -> list[tuple[str, CronTrigger]]:
    local_tz = _local_timezone(timezone_name)
    total_start_minutes = start_hour_local * 60
    total_end_minutes = end_hour_local * 60
    if interval_minutes <= 0:
        raise ValueError("interval_minutes must be positive")
    if total_end_minutes < total_start_minutes:
        raise ValueError("refresh window end must be at or after the start")

    triggers: list[tuple[str, CronTrigger]] = []
    for total_minutes in range(total_start_minutes, total_end_minutes + 1, interval_minutes):
        hour, minute = divmod(total_minutes, 60)
        triggers.append(
            (
                f"periodic-refresh-{hour:02d}{minute:02d}",
                CronTrigger(hour=hour, minute=minute, timezone=local_tz),
            )
        )
    return triggers


def next_periodic_refresh_at(
    now: datetime,
    timezone_name: str,
    interval_minutes: int,
    start_hour_local: int,
    end_hour_local: int,
) -> datetime | None:
    next_runs = [
        trigger.get_next_fire_time(None, now)
        for _, trigger in build_periodic_refresh_triggers(timezone_name, interval_minutes, start_hour_local, end_hour_local)
    ]
    candidates = [run for run in next_runs if run is not None]
    return min(candidates) if candidates else None


class PeriodicRefreshScheduler:
    def __init__(
        self,
        graph: DailyReportGraph,
        snapshot_ingestor: SnapshotIngestor | None = None,
        repo: Repository | None = None,
        review_need_agent: ReviewNeedAgent | None = None,
        issue_insight_agent: IssueInsightAgent | None = None,
        enable_periodic_refresh: bool = False,
        refresh_timezone: str = "",
        refresh_interval_minutes: int = 30,
        refresh_start_hour_local: int = 8,
        refresh_end_hour_local: int = 23,
    ) -> None:
        self.graph = graph
        self.snapshot_ingestor = snapshot_ingestor
        self.repo = repo
        self.review_need_agent = review_need_agent
        self.issue_insight_agent = issue_insight_agent
        self.enable_periodic_refresh = enable_periodic_refresh
        self.refresh_timezone = refresh_timezone
        self.refresh_interval_minutes = refresh_interval_minutes
        self.refresh_start_hour_local = refresh_start_hour_local
        self.refresh_end_hour_local = refresh_end_hour_local
        self.scheduler = BackgroundScheduler(timezone="UTC")

    def _run_full_refresh(self) -> None:
        """Full refresh: sync GitHub data → recompute scores → run analysis → generate report."""
        if not all([self.snapshot_ingestor, self.repo, self.review_need_agent, self.issue_insight_agent]):
            logger.warning("Periodic refresh skipped: missing dependencies")
            return

        try:
            logger.info(
                "Starting scheduled refresh (%s)",
                _refresh_schedule_summary(
                    self.refresh_interval_minutes,
                    self.refresh_start_hour_local,
                    self.refresh_end_hour_local,
                ),
            )
            self.repo.scheduled_refresh_attempted_at = datetime.now(timezone.utc)

            result = run_full_refresh(
                snapshot_ingestor=self.snapshot_ingestor,
                repo=self.repo,
                review_need_agent=self.review_need_agent,
                issue_insight_agent=self.issue_insight_agent,
                daily_graph=self.graph,
            )
            synced = result["synced"]
            scored = result["scored"]
            logger.info("Synced %d PRs and %d issues", synced.get("prs", 0), synced.get("issues", 0))
            logger.info(
                "Scored %d PRs (%d need review) and %d issues (%d interesting)",
                scored["prs"],
                scored["needs_review"],
                scored["issues"],
                scored["interesting_issues"],
            )
            logger.info("Generated report: %s", result.get("notifications", []))
            self.repo.scheduled_refresh_succeeded_at = datetime.now(timezone.utc)
            self.repo.scheduled_refresh_failed_at = None
            self.repo.scheduled_refresh_last_error = None

        except Exception as exc:
            self.repo.scheduled_refresh_failed_at = datetime.now(timezone.utc)
            self.repo.scheduled_refresh_last_error = _format_refresh_error(exc)
            logger.exception("Scheduled refresh failed: %s", exc)

    def start(self) -> None:
        if self.scheduler.running:
            return

        # Periodic refresh every 30 minutes during the local daytime window.
        if self.enable_periodic_refresh and all([self.snapshot_ingestor, self.repo, self.review_need_agent, self.issue_insight_agent]):
            for job_id, trigger in build_periodic_refresh_triggers(
                self.refresh_timezone,
                self.refresh_interval_minutes,
                self.refresh_start_hour_local,
                self.refresh_end_hour_local,
            ):
                self.scheduler.add_job(
                    self._run_full_refresh,
                    trigger=trigger,
                    id=job_id,
                    replace_existing=True,
                )
            logger.info(
                "Scheduled periodic refresh %s",
                _refresh_schedule_summary(
                    self.refresh_interval_minutes,
                    self.refresh_start_hour_local,
                    self.refresh_end_hour_local,
                ),
            )

        self.scheduler.start()

    def stop(self) -> None:
        if self.scheduler.running:
            self.scheduler.shutdown(wait=False)
