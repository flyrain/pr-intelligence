from __future__ import annotations

import logging
import os
from datetime import datetime, timezone, tzinfo
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from polaris_pr_intel.config import Settings

logger = logging.getLogger(__name__)


def configured_or_local_timezone(timezone_name: str = "") -> tzinfo:
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

    return local_tz or timezone.utc


def activity_timezone(settings: Settings | None = None) -> tzinfo:
    timezone_name = (settings.refresh_timezone if settings else "").strip()
    if timezone_name:
        return configured_or_local_timezone(timezone_name)
    return timezone.utc


def activity_timezone_label(settings: Settings | None = None) -> str:
    tz = activity_timezone(settings)
    if tz is timezone.utc:
        return "UTC"
    return getattr(tz, "key", "") or str(tz)


def is_same_activity_day(value: datetime, *, now: datetime | None = None, settings: Settings | None = None) -> bool:
    tz = activity_timezone(settings)
    current = now.astimezone(tz) if now is not None else datetime.now(tz)
    dt = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    return dt.astimezone(tz).date() == current.date()


def format_activity_time(value: datetime, *, settings: Settings | None = None, include_date: bool = False) -> str:
    tz = activity_timezone(settings)
    dt = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    local_dt = dt.astimezone(tz)
    return local_dt.strftime("%Y-%m-%d %H:%M" if include_date else "%H:%M")
