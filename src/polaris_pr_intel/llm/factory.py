from __future__ import annotations

from pathlib import Path

from polaris_pr_intel.config import Settings
from polaris_pr_intel.llm.adapters import (
    AnthropicAdapter,
    ClaudeCodeLocalAdapter,
    CodexLocalAdapter,
    GeminiAdapter,
    HeuristicLLMAdapter,
    OpenAIAdapter,
)
from polaris_pr_intel.llm.base import LLMAdapter


def build_llm_adapter(settings: Settings) -> LLMAdapter:
    provider = settings.llm_provider.lower()
    shared_repo_dir = (settings.local_review_repo_dir or "").strip()
    if provider == "openai":
        return OpenAIAdapter(api_key=settings.openai_api_key, model=settings.llm_model or "gpt-4o-mini")
    if provider == "gemini":
        return GeminiAdapter(api_key=settings.gemini_api_key, model=settings.llm_model or "gemini-1.5-pro")
    if provider == "anthropic":
        return AnthropicAdapter(api_key=settings.anthropic_api_key, model=settings.llm_model or "claude-3-5-sonnet")
    if provider == "claude_code_local":
        repo_dir = shared_repo_dir
        if not repo_dir:
            raise RuntimeError("LOCAL_REVIEW_REPO_DIR must not be empty when using local CLI providers.")
        if not Path(repo_dir).is_dir():
            raise RuntimeError(
                f"LOCAL_REVIEW_REPO_DIR is invalid: {repo_dir!r}. "
                "Set it to an existing local directory for code review."
            )
        return ClaudeCodeLocalAdapter(
            model=settings.llm_model or "claude-code-local",
            command=settings.claude_code_cmd,
            timeout_sec=settings.claude_code_timeout_sec,
            max_turns=settings.claude_code_max_turns,
            repo_dir=repo_dir,
            review_skill_file=settings.review_skill_file or settings.claude_code_skill_file,
            analysis_skill_file=settings.analysis_skill_file,
        )
    if provider == "codex_local":
        repo_dir = shared_repo_dir
        if not repo_dir:
            raise RuntimeError("LOCAL_REVIEW_REPO_DIR must not be empty when using local CLI providers.")
        if not Path(repo_dir).is_dir():
            raise RuntimeError(
                f"LOCAL_REVIEW_REPO_DIR is invalid: {repo_dir!r}. "
                "Set it to an existing local directory for code review."
            )
        return CodexLocalAdapter(
            model=settings.llm_model or "gpt-5-codex",
            command=settings.codex_cmd,
            timeout_sec=settings.codex_timeout_sec,
            max_turns=settings.codex_max_turns,
            reasoning_effort=settings.codex_reasoning_effort,
            repo_dir=repo_dir,
            review_skill_file=settings.review_skill_file,
            analysis_skill_file=settings.analysis_skill_file,
        )
    return HeuristicLLMAdapter(model=settings.llm_model or "local-heuristic")
