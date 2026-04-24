from __future__ import annotations

from datetime import datetime, timezone
import logging
from zoneinfo import ZoneInfo
import pytest

from polaris_pr_intel.config import Settings
from polaris_pr_intel.main import _configure_logging, build_runtime
from polaris_pr_intel.scheduler.periodic import PeriodicRefreshScheduler
from polaris_pr_intel.store.repository import InMemoryRepository
from polaris_pr_intel.time_utils import activity_timezone
from polaris_pr_intel.config import load_settings


class _DummyLLM:
    provider = "codex_local"
    model = "gpt-5-codex"


class _DummyGitHubClient:
    def __init__(self, *args, **kwargs) -> None:
        pass

    def close(self) -> None:
        pass


class _DummyScheduler:
    def __init__(self, *args, **kwargs) -> None:
        pass

    def start(self) -> None:
        pass

    def stop(self) -> None:
        pass


def test_build_runtime_logs_configured_llm(monkeypatch, caplog) -> None:
    monkeypatch.setattr(
        "polaris_pr_intel.main.load_settings",
        lambda: Settings(github_token="token", store_backend="memory", llm_provider="codex_local", llm_model="gpt-5-codex"),
    )
    monkeypatch.setattr("polaris_pr_intel.main._build_repository", lambda *args, **kwargs: InMemoryRepository())
    monkeypatch.setattr("polaris_pr_intel.main.build_llm_adapter", lambda settings: _DummyLLM())
    monkeypatch.setattr("polaris_pr_intel.main.GitHubClientWrapper", _DummyGitHubClient)
    monkeypatch.setattr("polaris_pr_intel.main.PeriodicRefreshScheduler", _DummyScheduler)

    caplog.set_level(logging.INFO)
    build_runtime()

    assert "Configured LLM provider: codex_local / gpt-5-codex" in caplog.text


def test_configure_logging_installs_handler_when_missing(monkeypatch) -> None:
    root = logging.getLogger()
    original_handlers = list(root.handlers)
    original_level = root.level
    try:
        root.handlers = []
        root.setLevel(logging.WARNING)
        _configure_logging()
        assert root.handlers
    finally:
        root.handlers = original_handlers
        root.setLevel(original_level)


def test_default_review_and_analysis_skill_paths_are_distinct(monkeypatch) -> None:
    monkeypatch.setenv("PR_INTEL_GITHUB_TOKEN", "token")
    monkeypatch.delenv("REVIEW_SKILL_FILE", raising=False)
    monkeypatch.delenv("ANALYSIS_SKILL_FILE", raising=False)

    settings = load_settings()

    assert settings.review_skill_file != settings.analysis_skill_file
    assert settings.review_skill_file.endswith("skills/polaris-pr-review/skill.md")
    assert settings.analysis_skill_file.endswith("skills/polaris-attention-analysis/skill.md")


def test_self_review_defaults_enabled(monkeypatch) -> None:
    monkeypatch.setenv("PR_INTEL_GITHUB_TOKEN", "token")
    monkeypatch.delenv("ENABLE_SELF_REVIEW", raising=False)

    settings = load_settings()

    assert settings.enable_self_review is True


def test_periodic_refresh_defaults_match_daytime_hourly_schedule(monkeypatch) -> None:
    monkeypatch.setenv("PR_INTEL_GITHUB_TOKEN", "token")
    monkeypatch.delenv("REFRESH_INTERVAL_MINUTES", raising=False)
    monkeypatch.delenv("REFRESH_START_HOUR_LOCAL", raising=False)
    monkeypatch.delenv("REFRESH_END_HOUR_LOCAL", raising=False)

    settings = load_settings()

    assert settings.refresh_interval_minutes == 60
    assert settings.refresh_start_hour_local == 8
    assert settings.refresh_end_hour_local == 23


def test_refresh_window_hours_must_be_valid(monkeypatch) -> None:
    monkeypatch.setenv("PR_INTEL_GITHUB_TOKEN", "token")
    monkeypatch.setenv("REFRESH_START_HOUR_LOCAL", "24")

    with pytest.raises(RuntimeError, match="REFRESH_START_HOUR_LOCAL must be between 0 and 23"):
        load_settings()


def test_load_settings_accepts_legacy_github_token(monkeypatch) -> None:
    monkeypatch.delenv("PR_INTEL_GITHUB_TOKEN", raising=False)
    monkeypatch.setenv("GITHUB_TOKEN", "legacy-token")

    settings = load_settings()

    assert settings.github_token == "legacy-token"


def test_load_settings_prefers_project_specific_token(monkeypatch) -> None:
    monkeypatch.setenv("PR_INTEL_GITHUB_TOKEN", "project-token")
    monkeypatch.setenv("GITHUB_TOKEN", "legacy-token")

    settings = load_settings()

    assert settings.github_token == "project-token"


def test_scheduler_registers_only_periodic_refresh_jobs() -> None:
    scheduler = PeriodicRefreshScheduler(
        graph=object(),
        snapshot_ingestor=object(),
        repo=object(),
        review_need_agent=object(),
        issue_insight_agent=object(),
        enable_periodic_refresh=True,
        refresh_interval_minutes=60,
        refresh_start_hour_local=8,
        refresh_end_hour_local=10,
    )
    recorded: list[tuple] = []

    class _SchedulerBackend:
        running = False

        def add_job(self, func, *args, **kwargs) -> None:
            recorded.append((func, args, kwargs))

        def start(self) -> None:
            pass

    scheduler.scheduler = _SchedulerBackend()

    scheduler.start()

    job_ids = [job[2]["id"] for job in recorded]
    assert job_ids == ["periodic-refresh-0800", "periodic-refresh-0900", "periodic-refresh-1000"]


def test_activity_timezone_uses_local_fallback_for_invalid_refresh_timezone(monkeypatch) -> None:
    fallback_tz = ZoneInfo("America/Los_Angeles")
    monkeypatch.setattr("polaris_pr_intel.time_utils.configured_or_local_timezone", lambda timezone_name="": fallback_tz)

    tz = activity_timezone(Settings(github_token="token", refresh_timezone="Mars/Phobos"))

    assert tz == fallback_tz


def test_scheduler_records_refresh_attempt_and_success(monkeypatch) -> None:
    repo = InMemoryRepository()
    repo.scheduled_refresh_failed_at = datetime(2026, 4, 1, 4, 0, 0, tzinfo=timezone.utc)
    repo.scheduled_refresh_last_error = "RuntimeError: stale error"
    scheduler = PeriodicRefreshScheduler(
        graph=object(),
        snapshot_ingestor=object(),
        repo=repo,
        review_need_agent=object(),
        issue_insight_agent=object(),
        enable_periodic_refresh=False,
    )

    monkeypatch.setattr(
        "polaris_pr_intel.scheduler.periodic.run_full_refresh",
        lambda **kwargs: {
            "ok": True,
            "synced": {"prs": 1, "issues": 2},
            "scored": {"prs": 1, "issues": 2, "needs_review": 1, "interesting_issues": 1},
            "analysis_run": None,
            "notifications": ["daily-report:2026-04-01"],
            "report_markdown": "",
        },
    )

    scheduler._run_full_refresh()

    assert repo.scheduled_refresh_attempted_at is not None
    assert repo.scheduled_refresh_succeeded_at is not None
    assert repo.scheduled_refresh_failed_at is None
    assert repo.scheduled_refresh_last_error is None
    assert repo.scheduled_refresh_succeeded_at >= repo.scheduled_refresh_attempted_at


def test_scheduler_records_refresh_failure(monkeypatch) -> None:
    repo = InMemoryRepository()
    scheduler = PeriodicRefreshScheduler(
        graph=object(),
        snapshot_ingestor=object(),
        repo=repo,
        review_need_agent=object(),
        issue_insight_agent=object(),
        enable_periodic_refresh=False,
    )
    repo.scheduled_refresh_succeeded_at = datetime(2026, 4, 1, 5, 0, 0, tzinfo=timezone.utc)

    def _fail(**kwargs):
        raise RuntimeError("github timeout")

    monkeypatch.setattr("polaris_pr_intel.scheduler.periodic.run_full_refresh", _fail)

    scheduler._run_full_refresh()

    assert repo.scheduled_refresh_attempted_at is not None
    assert repo.scheduled_refresh_failed_at is not None
    assert repo.scheduled_refresh_last_error == "RuntimeError: github timeout"
    assert repo.scheduled_refresh_succeeded_at == datetime(2026, 4, 1, 5, 0, 0, tzinfo=timezone.utc)
