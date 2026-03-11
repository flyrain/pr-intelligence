from __future__ import annotations

import subprocess

import pytest

from polaris_pr_intel.config import load_settings
from polaris_pr_intel.llm.adapters import ClaudeCodeLocalAdapter, CodexLocalAdapter
from polaris_pr_intel.llm.factory import build_llm_adapter
from polaris_pr_intel.models import PullRequestSnapshot


def _pr() -> PullRequestSnapshot:
    return PullRequestSnapshot.model_validate(
        {
            "number": 77,
            "title": "Improve security checks",
            "body": "Touches authorization logic.",
            "state": "open",
            "draft": False,
            "author": "alice",
            "labels": ["security"],
            "requested_reviewers": ["bob"],
            "comments": 0,
            "review_comments": 0,
            "commits": 4,
            "changed_files": 9,
            "additions": 120,
            "deletions": 20,
            "html_url": "https://example.com/pr/77",
            "updated_at": "2026-03-10T00:00:00Z",
        }
    )


def test_claude_code_local_adapter_parses_json(monkeypatch) -> None:
    adapter = ClaudeCodeLocalAdapter(command="claude")

    def _fake_run(*args, **kwargs):
        class R:
            stdout = """```json
{"agent_name":"x","focus_area":"x","verdict":"high","score":0.9,"summary":"critical path changed","recommendations":["review auth"],"confidence":0.8}
```"""

        return R()

    monkeypatch.setattr(subprocess, "run", _fake_run)
    finding = adapter.analyze_pr("security-signal", "security and permission model", _pr())

    assert finding.agent_name == "security-signal"
    assert finding.focus_area == "security and permission model"
    assert finding.verdict == "high"
    assert finding.score == 0.9


def test_claude_code_local_adapter_falls_back_on_failure(monkeypatch) -> None:
    adapter = ClaudeCodeLocalAdapter(command="claude")

    def _fake_run(*args, **kwargs):
        raise subprocess.CalledProcessError(returncode=1, cmd="claude")

    monkeypatch.setattr(subprocess, "run", _fake_run)
    finding = adapter.analyze_pr("security-signal", "security and permission model", _pr())
    assert finding.summary.startswith("(fallback heuristic:")


def test_claude_code_local_adapter_raises_on_auth_failure(monkeypatch) -> None:
    adapter = ClaudeCodeLocalAdapter(command="claude")

    def _fake_run(*args, **kwargs):
        raise subprocess.CalledProcessError(
            returncode=1,
            cmd="claude",
            stderr='{"result":"Failed to authenticate. API Error: 401"}',
        )

    monkeypatch.setattr(subprocess, "run", _fake_run)
    with pytest.raises(RuntimeError, match="authentication failed"):
        adapter.analyze_pr("security-signal", "security and permission model", _pr())


def test_factory_builds_claude_code_local_adapter(monkeypatch) -> None:
    monkeypatch.setenv("GITHUB_TOKEN", "token")
    monkeypatch.setenv("LLM_PROVIDER", "claude_code_local")
    monkeypatch.setenv("LLM_MODEL", "claude-local")
    monkeypatch.setenv("CLAUDE_CODE_CMD", "claude")
    monkeypatch.setenv("CLAUDE_CODE_TIMEOUT_SEC", "30")
    monkeypatch.setenv("LOCAL_REVIEW_REPO_DIR", "/tmp")

    settings = load_settings()
    adapter = build_llm_adapter(settings)
    assert adapter.provider == "claude_code_local"
    assert adapter.model == "claude-local"


def test_factory_fails_for_empty_repo_dir(monkeypatch) -> None:
    monkeypatch.setenv("GITHUB_TOKEN", "token")
    monkeypatch.setenv("LLM_PROVIDER", "claude_code_local")
    monkeypatch.setenv("LOCAL_REVIEW_REPO_DIR", "   ")
    settings = load_settings()
    with pytest.raises(RuntimeError, match="LOCAL_REVIEW_REPO_DIR must not be empty"):
        build_llm_adapter(settings)


def test_factory_fails_for_invalid_repo_dir(monkeypatch) -> None:
    monkeypatch.setenv("GITHUB_TOKEN", "token")
    monkeypatch.setenv("LLM_PROVIDER", "claude_code_local")
    monkeypatch.setenv("LOCAL_REVIEW_REPO_DIR", "/path/that/does/not/exist")
    settings = load_settings()
    with pytest.raises(RuntimeError, match="LOCAL_REVIEW_REPO_DIR is invalid"):
        build_llm_adapter(settings)


def test_codex_local_adapter_parses_json(monkeypatch) -> None:
    adapter = CodexLocalAdapter(command="codex")

    def _fake_run(*args, **kwargs):
        class R:
            stdout = '{"verdict":"medium","score":0.55,"summary":"moderate risk in changed auth path","recommendations":["add coverage for auth edge cases"],"confidence":0.7}'

        return R()

    monkeypatch.setattr(subprocess, "run", _fake_run)
    finding = adapter.analyze_pr("code-risk", "code risk and complexity", _pr())
    assert finding.agent_name == "code-risk"
    assert finding.verdict == "medium"
    assert finding.score == 0.55


def test_factory_builds_codex_local_adapter(monkeypatch) -> None:
    monkeypatch.setenv("GITHUB_TOKEN", "token")
    monkeypatch.setenv("LLM_PROVIDER", "codex_local")
    monkeypatch.setenv("LLM_MODEL", "gpt-5-codex")
    monkeypatch.setenv("CODEX_CMD", "codex")
    monkeypatch.setenv("CODEX_TIMEOUT_SEC", "40")
    monkeypatch.setenv("CODEX_MAX_TURNS", "10")
    monkeypatch.setenv("LOCAL_REVIEW_REPO_DIR", "/tmp")

    settings = load_settings()
    adapter = build_llm_adapter(settings)
    assert adapter.provider == "codex_local"
    assert adapter.model == "gpt-5-codex"


def test_factory_fails_for_invalid_codex_repo_dir(monkeypatch) -> None:
    monkeypatch.setenv("GITHUB_TOKEN", "token")
    monkeypatch.setenv("LLM_PROVIDER", "codex_local")
    monkeypatch.setenv("LOCAL_REVIEW_REPO_DIR", "/path/that/does/not/exist")
    settings = load_settings()
    with pytest.raises(RuntimeError, match="LOCAL_REVIEW_REPO_DIR is invalid"):
        build_llm_adapter(settings)


def test_factory_backward_compat_old_repo_dir_vars(monkeypatch) -> None:
    monkeypatch.setenv("GITHUB_TOKEN", "token")
    monkeypatch.setenv("LLM_PROVIDER", "codex_local")
    monkeypatch.delenv("LOCAL_REVIEW_REPO_DIR", raising=False)
    monkeypatch.setenv("CODEX_REPO_DIR", "/tmp")
    settings = load_settings()
    adapter = build_llm_adapter(settings)
    assert adapter.provider == "codex_local"
