from __future__ import annotations

import subprocess

from polaris_pr_intel.config import load_settings
from polaris_pr_intel.llm.adapters import ClaudeCodeLocalAdapter
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
    assert finding.summary.startswith("(fallback heuristic)")


def test_factory_builds_claude_code_local_adapter(monkeypatch) -> None:
    monkeypatch.setenv("GITHUB_TOKEN", "token")
    monkeypatch.setenv("LLM_PROVIDER", "claude_code_local")
    monkeypatch.setenv("LLM_MODEL", "claude-local")
    monkeypatch.setenv("CLAUDE_CODE_CMD", "claude")
    monkeypatch.setenv("CLAUDE_CODE_TIMEOUT_SEC", "30")

    settings = load_settings()
    adapter = build_llm_adapter(settings)
    assert adapter.provider == "claude_code_local"
    assert adapter.model == "claude-local"
