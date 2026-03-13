from __future__ import annotations

import logging

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
    monkeypatch.setattr("polaris_pr_intel.main.GitHubClient", _DummyGitHubClient)
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
    monkeypatch.setenv("GITHUB_TOKEN", "token")
    monkeypatch.delenv("REVIEW_SKILL_FILE", raising=False)
    monkeypatch.delenv("ANALYSIS_SKILL_FILE", raising=False)

    settings = load_settings()

    assert settings.review_skill_file != settings.analysis_skill_file
    assert settings.review_skill_file.endswith("skills/polaris-pr-review/skill.md")
    assert settings.analysis_skill_file.endswith("skills/polaris-report-analysis/skill.md")
