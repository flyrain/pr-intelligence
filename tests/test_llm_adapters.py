from __future__ import annotations

import logging
import subprocess

import pytest

from polaris_pr_intel.config import load_settings
from polaris_pr_intel.llm import build_llm_adapter
from polaris_pr_intel.llm._base_local_cli import BaseLocalCLIAdapter
from polaris_pr_intel.llm._claude_code_local import ClaudeCodeLocalAdapter
from polaris_pr_intel.llm._codex_local import CodexLocalAdapter
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


def test_claude_code_local_adapter_catalog_prompt_mentions_routing() -> None:
    adapter = ClaudeCodeLocalAdapter(command="claude")
    prompt = adapter._build_catalog_prompt(_pr())

    assert "post-sync reporting and catalog routing" in prompt
    assert "This is not a line-by-line PR review." in prompt
    assert '"agent_name": "catalog-router"' in prompt


def test_claude_code_local_adapter_logs_invocation_command(monkeypatch, caplog) -> None:
    adapter = ClaudeCodeLocalAdapter(command="claude", model="claude-local")

    def _fake_run(*args, **kwargs):
        class R:
            stdout = """```json
{"agent_name":"x","focus_area":"x","verdict":"high","score":0.9,"summary":"critical path changed","recommendations":["review auth"],"confidence":0.8}
```"""

        return R()

    monkeypatch.setattr(subprocess, "run", _fake_run)
    caplog.set_level(logging.INFO)

    adapter.analyze_catalog_routing(_pr())

    assert "Invoking claude_code_local LLM command:" in caplog.text
    assert "claude --print --dangerously-skip-permissions --output-format json" in caplog.text
    assert "<prompt>" in caplog.text


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


def test_factory_builds_claude_code_local_adapter(tmp_path, monkeypatch) -> None:
    # Create a fake git repo for testing
    fake_repo = tmp_path / "test-repo"
    fake_repo.mkdir()
    (fake_repo / ".git").mkdir()

    monkeypatch.setenv("PR_INTEL_GITHUB_TOKEN", "token")
    monkeypatch.setenv("LLM_PROVIDER", "claude_code_local")
    monkeypatch.setenv("LLM_MODEL", "claude-local")
    monkeypatch.setenv("CLAUDE_CODE_CMD", "claude")
    monkeypatch.setenv("CLAUDE_CODE_TIMEOUT_SEC", "30")
    monkeypatch.setenv("GIT_REPO_PATH", str(fake_repo))

    settings = load_settings()
    adapter = build_llm_adapter(settings)
    assert adapter.provider == "claude_code_local"
    assert adapter.model == "claude-local"


def test_factory_auto_manages_repo_when_no_explicit_path(monkeypatch, tmp_path) -> None:
    """Test that empty GIT_REPO_PATH triggers auto-management."""
    # Mock the RepositoryManager to avoid actual clone
    monkeypatch.setenv("PR_INTEL_GITHUB_TOKEN", "token")
    monkeypatch.setenv("GITHUB_OWNER", "test-owner")
    monkeypatch.setenv("GITHUB_REPO", "test-repo")
    monkeypatch.setenv("LLM_PROVIDER", "claude_code_local")
    monkeypatch.setenv("GIT_REPO_PATH", "")  # Empty - should trigger auto mode

    # Mock cache dir to avoid actual clone
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    fake_repo = cache_dir / "test-owner-test-repo"
    fake_repo.mkdir()
    (fake_repo / ".git").mkdir()

    monkeypatch.setenv("REPO_CACHE_DIR", str(cache_dir))

    # Mock git commands to avoid actual operations
    def mock_run(*args, **kwargs):
        class MockResult:
            stdout = "main"
            stderr = ""
            returncode = 0
        return MockResult()

    monkeypatch.setattr("subprocess.run", mock_run)

    settings = load_settings()
    adapter = build_llm_adapter(settings)
    assert adapter.provider == "claude_code_local"


def test_factory_fails_for_invalid_explicit_repo_path(monkeypatch) -> None:
    """Test that explicitly provided invalid path fails."""
    monkeypatch.setenv("PR_INTEL_GITHUB_TOKEN", "token")
    monkeypatch.setenv("LLM_PROVIDER", "claude_code_local")
    monkeypatch.setenv("GIT_REPO_PATH", "/path/that/does/not/exist/at/all")
    settings = load_settings()
    with pytest.raises(RuntimeError, match="does not exist"):
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


def test_codex_local_adapter_catalog_prompt_mentions_routing() -> None:
    adapter = CodexLocalAdapter(command="codex")
    prompt = adapter._build_catalog_prompt(_pr())

    assert "post-sync report generation and catalog routing" in prompt
    assert "Do not perform a normal PR review." in prompt
    assert '"agent_name": "catalog-router"' in prompt


def test_codex_local_adapter_includes_shared_skill_prompt(tmp_path) -> None:
    analysis_skill_file = tmp_path / "analysis-skill.md"
    analysis_skill_file.write_text(
        "---\nname: polaris-attention-analysis\ndescription: x\n---\nUse recent-change routing rules.\n",
        encoding="utf-8",
    )
    adapter = CodexLocalAdapter(command="codex", analysis_skill_file=str(analysis_skill_file))

    prompt = adapter._build_catalog_prompt(_pr())

    assert "Use recent-change routing rules." in prompt


def test_codex_local_adapter_uses_review_skill_only_for_review_prompt(tmp_path) -> None:
    review_skill_file = tmp_path / "review-skill.md"
    review_skill_file.write_text(
        "---\nname: polaris-pr-review\ndescription: x\n---\nReview-specific guidance.\n",
        encoding="utf-8",
    )
    adapter = CodexLocalAdapter(command="codex", review_skill_file=str(review_skill_file))

    prompt = adapter._build_prompt("code-risk", "code risk and complexity", _pr())

    assert "Review-specific guidance." in prompt


def test_claude_code_local_adapter_uses_analysis_skill_only_for_catalog_prompt(tmp_path) -> None:
    analysis_skill_file = tmp_path / "analysis-skill.md"
    analysis_skill_file.write_text(
        "---\nname: polaris-attention-analysis\ndescription: x\n---\nAnalysis-only guidance.\n",
        encoding="utf-8",
    )
    adapter = ClaudeCodeLocalAdapter(command="claude", analysis_skill_file=str(analysis_skill_file))

    prompt = adapter._build_catalog_prompt(_pr())

    assert "Analysis-only guidance." in prompt


def test_codex_local_adapter_logs_invocation_command(monkeypatch, caplog) -> None:
    adapter = CodexLocalAdapter(command="codex", model="gpt-5-codex")

    def _fake_run(*args, **kwargs):
        class R:
            stdout = '{"verdict":"medium","score":0.55,"summary":"moderate risk in changed auth path","recommendations":["add coverage for auth edge cases"],"confidence":0.7}'

        return R()

    monkeypatch.setattr(subprocess, "run", _fake_run)
    caplog.set_level(logging.INFO)

    adapter.analyze_catalog_routing(_pr())

    assert "Invoking codex_local LLM command:" in caplog.text
    assert "codex exec --full-auto --skip-git-repo-check" in caplog.text
    assert "--output-last-message" in caplog.text
    assert 'model_reasoning_effort="medium"' in caplog.text
    assert "<prompt>" in caplog.text


def test_codex_local_adapter_passes_reasoning_effort_override_to_subprocess(monkeypatch) -> None:
    adapter = CodexLocalAdapter(command="codex", model="gpt-5-codex", reasoning_effort="medium")
    captured_cmd: list[str] | None = None

    def _fake_run(cmd, **kwargs):
        nonlocal captured_cmd
        captured_cmd = list(cmd)

        class R:
            stdout = '{"verdict":"medium","score":0.55,"summary":"moderate risk in changed auth path","recommendations":["add coverage for auth edge cases"],"confidence":0.7}'

        return R()

    monkeypatch.setattr(subprocess, "run", _fake_run)

    adapter.analyze_pr("code-risk", "code risk and complexity", _pr())

    assert captured_cmd is not None
    assert "-c" in captured_cmd
    idx = captured_cmd.index("-c")
    assert captured_cmd[idx + 1] == 'model_reasoning_effort="medium"'


def test_codex_local_adapter_formats_timeout_failures(monkeypatch, caplog) -> None:
    adapter = CodexLocalAdapter(command="codex")

    def _fake_run_raw_prompt(prompt: str):
        raise subprocess.TimeoutExpired(cmd="codex", timeout=900)

    monkeypatch.setattr(adapter, "_run_raw_prompt", _fake_run_raw_prompt)
    caplog.set_level(logging.WARNING)

    with pytest.raises(RuntimeError, match="timed out after 900s before producing output"):
        adapter.analyze_pr_with_self_review(_pr())

    assert "Step 1 failed, failing review job" in caplog.text
    assert "CODEX_TIMEOUT_SEC" in caplog.text


def test_codex_local_adapter_surfaces_sandboxed_codex_failure(monkeypatch) -> None:
    adapter = CodexLocalAdapter(command="codex")

    def _fake_run(*args, **kwargs):
        raise subprocess.CalledProcessError(
            returncode=101,
            cmd="codex",
            stderr="Could not create otel exporter: panicked during initialization",
        )

    monkeypatch.setattr(subprocess, "run", _fake_run)

    finding = adapter.analyze_pr("code-risk", "code risk and complexity", _pr())

    assert "sandboxed Codex Desktop/session environment" in finding.summary


def test_codex_local_adapter_strips_parent_codex_env(monkeypatch) -> None:
    adapter = CodexLocalAdapter(command="codex", model="gpt-5-codex")
    monkeypatch.setenv("CODEX_SANDBOX", "seatbelt")
    monkeypatch.setenv("CODEX_THREAD_ID", "thread-123")
    monkeypatch.setenv("CODEX_HOME", "/tmp/codex-home")
    monkeypatch.setenv("PATH", "/usr/bin")
    captured_env: dict[str, str] | None = None

    def _fake_run(*args, **kwargs):
        nonlocal captured_env
        assert "env" in kwargs
        captured_env = dict(kwargs["env"])

        class R:
            stdout = '{"verdict":"medium","score":0.55,"summary":"moderate risk in changed auth path","recommendations":["add coverage for auth edge cases"],"confidence":0.7}'

        return R()

    monkeypatch.setattr(subprocess, "run", _fake_run)

    adapter.analyze_pr("code-risk", "code risk and complexity", _pr())

    assert captured_env is not None
    assert "CODEX_SANDBOX" not in captured_env
    assert "CODEX_THREAD_ID" not in captured_env
    assert captured_env["CODEX_HOME"] == "/tmp/codex-home"
    assert all(not key.startswith("CODEX_") for key in captured_env if key != "CODEX_HOME")


def test_codex_local_adapter_comprehensive_failure_raises_without_retry_per_aspect(monkeypatch) -> None:
    adapter = CodexLocalAdapter(command="codex")
    calls = 0

    def _fake_run_raw_prompt(prompt: str):
        nonlocal calls
        calls += 1
        raise subprocess.CalledProcessError(returncode=1, cmd="codex", stderr="stream disconnected before completion")

    monkeypatch.setattr(adapter, "_run_raw_prompt", _fake_run_raw_prompt)

    with pytest.raises(RuntimeError, match="could not reach the Codex backend"):
        adapter.analyze_pr_comprehensive(_pr())

    assert calls == 1


def test_codex_local_adapter_self_review_fails_job_if_initial_review_fails(monkeypatch, caplog) -> None:
    adapter = CodexLocalAdapter(command="codex")
    prompts: list[str] = []

    def _fake_run_raw_prompt(prompt: str):
        prompts.append(prompt)
        raise subprocess.CalledProcessError(returncode=1, cmd="codex", stderr="stream disconnected before completion")

    monkeypatch.setattr(adapter, "_run_raw_prompt", _fake_run_raw_prompt)
    caplog.set_level(logging.WARNING)

    with pytest.raises(RuntimeError, match="could not reach the Codex backend"):
        adapter.analyze_pr_with_self_review(_pr())

    assert len(prompts) == 1
    assert "Step 1 failed, failing review job" in caplog.text
    assert "could not reach the Codex backend" in caplog.text


def test_codex_local_adapter_uses_last_message_output_even_on_nonzero_exit(monkeypatch) -> None:
    adapter = CodexLocalAdapter(command="codex")

    def _fake_run(args, **kwargs):
        last_message_path = args[args.index("--output-last-message") + 1]
        with open(last_message_path, "w", encoding="utf-8") as fh:
            fh.write('{"verdict":"medium","score":0.55,"summary":"moderate risk in changed auth path","recommendations":["add coverage for auth edge cases"],"confidence":0.7}')

        result = type("Result", (), {})()
        result.returncode = 1
        result.stdout = "OpenAI Codex v0.114.0"
        result.stderr = "stream disconnected before completion"
        result.args = args
        return result

    monkeypatch.setattr(subprocess, "run", _fake_run)

    finding = adapter.analyze_pr("code-risk", "code risk and complexity", _pr())

    assert finding.verdict == "medium"
    assert finding.summary == "moderate risk in changed auth path"


def test_factory_builds_codex_local_adapter(tmp_path, monkeypatch) -> None:
    # Create a fake git repo for testing
    fake_repo = tmp_path / "test-repo"
    fake_repo.mkdir()
    (fake_repo / ".git").mkdir()

    monkeypatch.setenv("PR_INTEL_GITHUB_TOKEN", "token")
    monkeypatch.setenv("LLM_PROVIDER", "codex_local")
    monkeypatch.setenv("LLM_MODEL", "gpt-5-codex")
    monkeypatch.setenv("CODEX_CMD", "codex")
    monkeypatch.setenv("CODEX_TIMEOUT_SEC", "40")
    monkeypatch.setenv("CODEX_MAX_TURNS", "10")
    monkeypatch.setenv("CODEX_REASONING_EFFORT", "medium")
    monkeypatch.setenv("GIT_REPO_PATH", str(fake_repo))

    settings = load_settings()
    adapter = build_llm_adapter(settings)
    assert adapter.provider == "codex_local"
    assert adapter.model == "gpt-5-codex"
    assert adapter.reasoning_effort == "medium"


def test_codex_settings_defaults_favor_medium_effort_and_longer_timeout(monkeypatch) -> None:
    monkeypatch.setenv("PR_INTEL_GITHUB_TOKEN", "token")
    monkeypatch.delenv("CODEX_TIMEOUT_SEC", raising=False)
    monkeypatch.delenv("CODEX_REASONING_EFFORT", raising=False)

    settings = load_settings()

    assert settings.codex_timeout_sec == 900
    assert settings.codex_reasoning_effort == "medium"


def test_codex_settings_allow_model_specific_reasoning_effort_values(monkeypatch) -> None:
    monkeypatch.setenv("PR_INTEL_GITHUB_TOKEN", "token")
    monkeypatch.setenv("CODEX_REASONING_EFFORT", "xhigh")

    settings = load_settings()

    assert settings.codex_reasoning_effort == "xhigh"


def test_factory_defaults_codex_local_to_gpt_5_4(tmp_path, monkeypatch) -> None:
    # Create a fake git repo for testing
    fake_repo = tmp_path / "test-repo"
    fake_repo.mkdir()
    (fake_repo / ".git").mkdir()

    monkeypatch.setenv("PR_INTEL_GITHUB_TOKEN", "token")
    monkeypatch.setenv("LLM_PROVIDER", "codex_local")
    monkeypatch.delenv("LLM_MODEL", raising=False)
    monkeypatch.setenv("GIT_REPO_PATH", str(fake_repo))

    settings = load_settings()
    adapter = build_llm_adapter(settings)

    assert adapter.provider == "codex_local"
    assert adapter.model == "gpt-5.4"


def test_factory_fails_for_invalid_codex_repo_dir(monkeypatch) -> None:
    """Test that explicitly provided invalid path fails for codex too."""
    monkeypatch.setenv("PR_INTEL_GITHUB_TOKEN", "token")
    monkeypatch.setenv("LLM_PROVIDER", "codex_local")
    monkeypatch.setenv("GIT_REPO_PATH", "/path/that/does/not/exist/at/all")
    settings = load_settings()
    with pytest.raises(RuntimeError, match="does not exist"):
        build_llm_adapter(settings)


@pytest.mark.parametrize("provider", ["openai", "anthropic", "gemini", "bogus"])
def test_factory_rejects_unsupported_provider(monkeypatch, provider: str) -> None:
    monkeypatch.setenv("PR_INTEL_GITHUB_TOKEN", "token")
    monkeypatch.setenv("LLM_PROVIDER", provider)

    settings = load_settings()

    with pytest.raises(RuntimeError, match="Unsupported LLM_PROVIDER="):
        build_llm_adapter(settings)


def test_factory_backward_compat_old_repo_dir_vars(tmp_path, monkeypatch) -> None:
    """Test that old LOCAL_REVIEW_REPO_DIR and CODEX_REPO_DIR still work."""
    # Create a fake git repo for testing
    fake_repo = tmp_path / "test-repo"
    fake_repo.mkdir()
    (fake_repo / ".git").mkdir()

    monkeypatch.setenv("PR_INTEL_GITHUB_TOKEN", "token")
    monkeypatch.setenv("LLM_PROVIDER", "codex_local")
    monkeypatch.delenv("LOCAL_REVIEW_REPO_DIR", raising=False)
    monkeypatch.delenv("GIT_REPO_PATH", raising=False)
    monkeypatch.setenv("CODEX_REPO_DIR", str(fake_repo))  # Old variable
    settings = load_settings()
    adapter = build_llm_adapter(settings)
    assert adapter.provider == "codex_local"


def test_claude_code_local_adapter_review_prompt_uses_review_skill_only(tmp_path) -> None:
    """Verify that PR review prompts include review skill and NOT analysis skill."""
    review_skill_file = tmp_path / "review-skill.md"
    review_skill_file.write_text(
        "---\nname: polaris-pr-review\ndescription: x\n---\n# Apache Polaris PR Review Skill\n\nCheck code correctness.",
        encoding="utf-8",
    )
    analysis_skill_file = tmp_path / "analysis-skill.md"
    analysis_skill_file.write_text(
        "---\nname: polaris-attention-analysis\ndescription: x\n---\n# Apache Polaris Attention Analysis Skill\n\nRank PRs for attention.",
        encoding="utf-8",
    )
    adapter = ClaudeCodeLocalAdapter(
        command="claude",
        review_skill_file=str(review_skill_file),
        analysis_skill_file=str(analysis_skill_file),
    )

    # Test single-aspect review prompt
    review_prompt = adapter._build_prompt("code-risk", "code risk and complexity", _pr())
    assert "Apache Polaris PR Review Skill" in review_prompt
    assert "Check code correctness." in review_prompt
    assert "Apache Polaris Attention Analysis Skill" not in review_prompt
    assert "Rank PRs for attention." not in review_prompt

    # Test comprehensive review prompt
    comprehensive_prompt = adapter._build_comprehensive_prompt(_pr())
    assert "Apache Polaris PR Review Skill" in comprehensive_prompt
    assert "Check code correctness." in comprehensive_prompt
    assert "Apache Polaris Attention Analysis Skill" not in comprehensive_prompt
    assert "Rank PRs for attention." not in comprehensive_prompt


def test_claude_code_local_adapter_catalog_prompt_uses_analysis_skill_only(tmp_path) -> None:
    """Verify that catalog routing prompts include analysis skill and NOT review skill."""
    review_skill_file = tmp_path / "review-skill.md"
    review_skill_file.write_text(
        "---\nname: polaris-pr-review\ndescription: x\n---\n# Apache Polaris PR Review Skill\n\nCheck code correctness.",
        encoding="utf-8",
    )
    analysis_skill_file = tmp_path / "analysis-skill.md"
    analysis_skill_file.write_text(
        "---\nname: polaris-attention-analysis\ndescription: x\n---\n# Apache Polaris Attention Analysis Skill\n\nRank PRs for attention.",
        encoding="utf-8",
    )
    adapter = ClaudeCodeLocalAdapter(
        command="claude",
        review_skill_file=str(review_skill_file),
        analysis_skill_file=str(analysis_skill_file),
    )

    catalog_prompt = adapter._build_catalog_prompt(_pr())
    assert "Apache Polaris Attention Analysis Skill" in catalog_prompt
    assert "Rank PRs for attention." in catalog_prompt
    assert "Apache Polaris PR Review Skill" not in catalog_prompt
    assert "Check code correctness." not in catalog_prompt


def test_claude_code_local_adapter_attention_batch_uses_analysis_skill_only(tmp_path) -> None:
    """Verify that attention batch prompts include analysis skill and NOT review skill."""
    review_skill_file = tmp_path / "review-skill.md"
    review_skill_file.write_text(
        "---\nname: polaris-pr-review\ndescription: x\n---\n# Apache Polaris PR Review Skill\n\nCheck code correctness.",
        encoding="utf-8",
    )
    analysis_skill_file = tmp_path / "analysis-skill.md"
    analysis_skill_file.write_text(
        "---\nname: polaris-attention-analysis\ndescription: x\n---\n# Apache Polaris Attention Analysis Skill\n\nRank PRs for attention.",
        encoding="utf-8",
    )
    adapter = ClaudeCodeLocalAdapter(
        command="claude",
        review_skill_file=str(review_skill_file),
        analysis_skill_file=str(analysis_skill_file),
    )

    # Create attention contexts for batch analysis
    from polaris_pr_intel.models import PRAttentionContext
    from datetime import datetime, timezone

    contexts = [
        PRAttentionContext(
            pr_number=77,
            title="Test PR",
            author="alice",
            state="open",
            draft=False,
            labels=["security"],
            requested_reviewers=["bob"],
            updated_at=datetime.now(timezone.utc),
            age_hours=24.0,
            inactive_days=1.0,
            comments_total=0,
            review_comments_total=0,
            comments_24h=0,
            comments_7d=0,
            reviews_24h=0,
            reviews_7d=0,
            commits=4,
            changed_files=9,
            additions=120,
            deletions=20,
            diff_size=140,
            has_prior_review_activity=False,
            has_prior_deep_review=False,
            rule_reasons=[],
            body="Test body",
            html_url="https://example.com/pr/77",
        )
    ]

    skill_prompt = adapter._load_skill_prompt(adapter.analysis_skill_file)
    batch_prompt = BaseLocalCLIAdapter._build_attention_batch_prompt(skill_prompt, contexts)

    assert "Apache Polaris Attention Analysis Skill" in batch_prompt
    assert "Rank PRs for attention." in batch_prompt
    assert "Apache Polaris PR Review Skill" not in batch_prompt
    assert "Check code correctness." not in batch_prompt


def test_codex_local_adapter_review_prompt_uses_review_skill_only(tmp_path) -> None:
    """Verify that PR review prompts include review skill and NOT analysis skill for Codex."""
    review_skill_file = tmp_path / "review-skill.md"
    review_skill_file.write_text(
        "---\nname: polaris-pr-review\ndescription: x\n---\n# Apache Polaris PR Review Skill\n\nCheck code correctness.",
        encoding="utf-8",
    )
    analysis_skill_file = tmp_path / "analysis-skill.md"
    analysis_skill_file.write_text(
        "---\nname: polaris-attention-analysis\ndescription: x\n---\n# Apache Polaris Attention Analysis Skill\n\nRank PRs for attention.",
        encoding="utf-8",
    )
    adapter = CodexLocalAdapter(
        command="codex",
        review_skill_file=str(review_skill_file),
        analysis_skill_file=str(analysis_skill_file),
    )

    # Test single-aspect review prompt
    review_prompt = adapter._build_prompt("code-risk", "code risk and complexity", _pr())
    assert "Use the following project skill as guidance." in review_prompt
    assert "Apache Polaris PR Review Skill" in review_prompt
    assert "Check code correctness." in review_prompt
    assert "Apache Polaris Attention Analysis Skill" not in review_prompt
    assert "Rank PRs for attention." not in review_prompt

    # Test comprehensive review prompt
    comprehensive_prompt = adapter._build_comprehensive_prompt(_pr())
    assert "Apache Polaris PR Review Skill" in comprehensive_prompt
    assert "Check code correctness." in comprehensive_prompt
    assert "Apache Polaris Attention Analysis Skill" not in comprehensive_prompt
    assert "Rank PRs for attention." not in comprehensive_prompt


def test_codex_local_adapter_catalog_prompt_uses_analysis_skill_only(tmp_path) -> None:
    """Verify that catalog routing prompts include analysis skill and NOT review skill for Codex."""
    review_skill_file = tmp_path / "review-skill.md"
    review_skill_file.write_text(
        "---\nname: polaris-pr-review\ndescription: x\n---\n# Apache Polaris PR Review Skill\n\nCheck code correctness.",
        encoding="utf-8",
    )
    analysis_skill_file = tmp_path / "analysis-skill.md"
    analysis_skill_file.write_text(
        "---\nname: polaris-attention-analysis\ndescription: x\n---\n# Apache Polaris Attention Analysis Skill\n\nRank PRs for attention.",
        encoding="utf-8",
    )
    adapter = CodexLocalAdapter(
        command="codex",
        review_skill_file=str(review_skill_file),
        analysis_skill_file=str(analysis_skill_file),
    )

    catalog_prompt = adapter._build_catalog_prompt(_pr())
    assert "Apache Polaris Attention Analysis Skill" in catalog_prompt
    assert "Rank PRs for attention." in catalog_prompt
    assert "Apache Polaris PR Review Skill" not in catalog_prompt
    assert "Check code correctness." not in catalog_prompt
