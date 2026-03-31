from __future__ import annotations

import logging
from datetime import datetime
import os
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from polaris_pr_intel.graphs.daily_report_graph import DailyReportGraph
from polaris_pr_intel.refresh import run_full_refresh

if TYPE_CHECKING:
    from polaris_pr_intel.agents.issue_insight import IssueInsightAgent
    from polaris_pr_intel.agents.review_need import ReviewNeedAgent
    from polaris_pr_intel.ingest import SnapshotIngestor
    from polaris_pr_intel.store.base import Repository

logger = logging.getLogger(__name__)


def _local_timezone(timezone_name: str = ""):
    if timezone_name:
        try:
            return ZoneInfo(timezone_name)
        except ZoneInfoNotFoundError:
            logger.warning("Invalid refresh timezone %r, falling back to system local timezone", timezone_name)

    local_tz = datetime.now().astimezone().tzinfo
    zone_key = getattr(local_tz, "key", "")
    if zone_key:
        try:
            return ZoneInfo(zone_key)
        except ZoneInfoNotFoundError:
            logger.warning("Local timezone %r is not available via zoneinfo, using system tzinfo as-is", zone_key)

    env_tz = os.getenv("TZ", "").strip()
    if env_tz:
        try:
            return ZoneInfo(env_tz)
        except ZoneInfoNotFoundError:
            logger.warning("TZ=%r is not available via zoneinfo, using system tzinfo as-is", env_tz)

    return local_tz


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

        except Exception as exc:
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
