from __future__ import annotations

import logging
from functools import wraps
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

from polaris_pr_intel.config import Settings
from polaris_pr_intel.models import PRAttentionContext, PRAttentionDecision, PRSubagentFinding, PullRequestSnapshot

if TYPE_CHECKING:
    from polaris_pr_intel.git.repo_manager import RepositoryManager
    from polaris_pr_intel.git.worktree_manager import WorktreeManager

logger = logging.getLogger(__name__)


class LLMAdapter(Protocol):
    provider: str
    model: str

    def analyze_pr(self, agent_name: str, focus_area: str, pr: PullRequestSnapshot) -> PRSubagentFinding: ...
    def analyze_pr_comprehensive(self, pr: PullRequestSnapshot) -> list[PRSubagentFinding]: ...
    def analyze_pr_with_self_review(self, pr: PullRequestSnapshot) -> list[PRSubagentFinding]: ...
    def analyze_catalog_routing(self, pr: PullRequestSnapshot) -> PRSubagentFinding: ...
    def analyze_catalog_routing_batch(self, prs: list[PullRequestSnapshot]) -> dict[int, PRSubagentFinding]: ...
    def analyze_attention_batch(self, contexts: list[PRAttentionContext]) -> dict[int, PRAttentionDecision]: ...


SUPPORTED_LLM_PROVIDERS = ("heuristic", "claude_code_local", "codex_local")


def _wrap_method_with_worktree(method, worktree_manager: WorktreeManager, repo_manager: RepositoryManager):
    """Wrap an adapter method to run in a temporary worktree for each PR."""

    @wraps(method)
    def wrapper(pr: PullRequestSnapshot):
        # Fetch PR branch from GitHub
        try:
            branch = repo_manager.fetch_pr_branch(pr.number)
        except Exception as e:
            logger.error("Cannot fetch PR #%d branch: %s", pr.number, e)
            raise RuntimeError(
                f"PR #{pr.number} review blocked: cannot fetch branch from GitHub. "
                f"Would review wrong code (HEAD instead of PR). Error: {e}"
            ) from e

        # Create worktree for this PR
        worktree_ctx = worktree_manager.create_worktree_for_pr(pr.number, branch)

        try:
            logger.info("Using worktree at %s for PR #%d", worktree_ctx.path, pr.number)
            # Swap repo_dir temporarily
            original_repo_dir = method.__self__.repo_dir
            set_resume_context = getattr(method.__self__, "set_review_resume_context", None)
            if callable(set_resume_context):
                if getattr(method.__self__, "keep_worktree_for_resume", False):
                    resume_cwd = str(worktree_ctx.path)
                else:
                    resume_cwd = str(repo_manager.get_base_repo())
                set_resume_context(cwd=resume_cwd, branch=branch)
            method.__self__.repo_dir = str(worktree_ctx.path)
            return method(pr)
        finally:
            method.__self__.repo_dir = original_repo_dir
            if worktree_manager.auto_cleanup:
                logger.info("Cleaning up worktree for PR #%d", pr.number)
                worktree_manager.remove_worktree(pr.number)

    return wrapper


def build_llm_adapter(settings: Settings) -> LLMAdapter:
    provider = settings.llm_provider.lower()

    if provider == "heuristic":
        from polaris_pr_intel.llm._heuristic import HeuristicLLMAdapter

        return HeuristicLLMAdapter(model=settings.llm_model or "local-heuristic")

    if provider == "claude_code_local":
        from polaris_pr_intel.git.repo_manager import RepositoryManager
        from polaris_pr_intel.git.worktree_manager import WorktreeManager
        from polaris_pr_intel.llm._claude_code_local import ClaudeCodeLocalAdapter

        # Get base repository using smart auto-detection
        repo_manager = RepositoryManager(
            owner=settings.github_owner,
            repo=settings.github_repo,
            token=settings.github_token,
            explicit_path=settings.git_repo_path or None,
            cache_dir=settings.repo_cache_dir or None,
        )

        base_repo = repo_manager.get_base_repo()

        # Create base adapter
        adapter_kwargs = {
            "command": settings.claude_code_cmd,
            "timeout_sec": settings.claude_code_timeout_sec,
            "max_turns": settings.claude_code_max_turns,
            "repo_dir": str(base_repo),
            "review_skill_file": settings.review_skill_file or settings.claude_code_skill_file,
            "analysis_skill_file": settings.analysis_skill_file,
        }
        if settings.llm_model:
            adapter_kwargs["model"] = settings.llm_model
        adapter = ClaudeCodeLocalAdapter(**adapter_kwargs)

        # Wrap with worktree manager if enabled (default)
        if settings.use_worktrees:
            worktree_base = settings.worktree_base_dir or str(base_repo / ".worktrees")
            worktree_manager = WorktreeManager(
                base_repo_path=base_repo,
                worktree_base_dir=worktree_base,
                auto_cleanup=not adapter.keep_worktree_for_resume,
            )
            # Wrap the PR review methods to run in temporary worktrees
            adapter.analyze_pr_comprehensive = _wrap_method_with_worktree(
                adapter.analyze_pr_comprehensive, worktree_manager, repo_manager
            )
            adapter.analyze_pr_with_self_review = _wrap_method_with_worktree(
                adapter.analyze_pr_with_self_review, worktree_manager, repo_manager
            )

        return adapter

    if provider == "codex_local":
        from polaris_pr_intel.git.repo_manager import RepositoryManager
        from polaris_pr_intel.git.worktree_manager import WorktreeManager
        from polaris_pr_intel.llm._codex_local import CodexLocalAdapter

        # Get base repository using smart auto-detection
        repo_manager = RepositoryManager(
            owner=settings.github_owner,
            repo=settings.github_repo,
            token=settings.github_token,
            explicit_path=settings.git_repo_path or None,
            cache_dir=settings.repo_cache_dir or None,
        )

        base_repo = repo_manager.get_base_repo()

        # Create base adapter
        adapter_kwargs = {
            "command": settings.codex_cmd,
            "timeout_sec": settings.codex_timeout_sec,
            "max_turns": settings.codex_max_turns,
            "reasoning_effort": settings.codex_reasoning_effort,
            "repo_dir": str(base_repo),
            "review_skill_file": settings.review_skill_file,
            "analysis_skill_file": settings.analysis_skill_file,
        }
        if settings.llm_model:
            adapter_kwargs["model"] = settings.llm_model
        adapter = CodexLocalAdapter(**adapter_kwargs)

        # Wrap with worktree manager if enabled (default)
        if settings.use_worktrees:
            worktree_base = settings.worktree_base_dir or str(base_repo / ".worktrees")
            worktree_manager = WorktreeManager(
                base_repo_path=base_repo,
                worktree_base_dir=worktree_base,
                auto_cleanup=not adapter.keep_worktree_for_resume,
            )
            # Wrap the PR review methods to run in temporary worktrees
            adapter.analyze_pr_comprehensive = _wrap_method_with_worktree(
                adapter.analyze_pr_comprehensive, worktree_manager, repo_manager
            )
            adapter.analyze_pr_with_self_review = _wrap_method_with_worktree(
                adapter.analyze_pr_with_self_review, worktree_manager, repo_manager
            )

        return adapter

    supported = ", ".join(SUPPORTED_LLM_PROVIDERS)
    raise RuntimeError(f"Unsupported LLM_PROVIDER={provider!r}. Supported values: {supported}")
