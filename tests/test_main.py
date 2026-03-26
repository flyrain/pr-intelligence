from __future__ import annotations

import logging
import pytest

from polaris_pr_intel.config import Settings
from polaris_pr_intel.main import _configure_logging, build_runtime
from polaris_pr_intel.store.repository import InMemoryRepository
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
    monkeypatch.setattr("polaris_pr_intel.main.DailyScheduler", _DummyScheduler)

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


def test_periodic_refresh_defaults_match_daytime_half_hour_schedule(monkeypatch) -> None:
    monkeypatch.setenv("PR_INTEL_GITHUB_TOKEN", "token")
    monkeypatch.delenv("REFRESH_INTERVAL_MINUTES", raising=False)
    monkeypatch.delenv("REFRESH_START_HOUR_LOCAL", raising=False)
    monkeypatch.delenv("REFRESH_END_HOUR_LOCAL", raising=False)

    settings = load_settings()

    assert settings.refresh_interval_minutes == 30
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
