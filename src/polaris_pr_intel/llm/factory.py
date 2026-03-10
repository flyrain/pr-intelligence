from __future__ import annotations

from polaris_pr_intel.config import Settings
from polaris_pr_intel.llm.adapters import AnthropicAdapter, ClaudeCodeLocalAdapter, GeminiAdapter, HeuristicLLMAdapter, OpenAIAdapter
from polaris_pr_intel.llm.base import LLMAdapter


def build_llm_adapter(settings: Settings) -> LLMAdapter:
    provider = settings.llm_provider.lower()
    if provider == "openai":
        return OpenAIAdapter(api_key=settings.openai_api_key, model=settings.llm_model or "gpt-4o-mini")
    if provider == "gemini":
        return GeminiAdapter(api_key=settings.gemini_api_key, model=settings.llm_model or "gemini-1.5-pro")
    if provider == "anthropic":
        return AnthropicAdapter(api_key=settings.anthropic_api_key, model=settings.llm_model or "claude-3-5-sonnet")
    if provider == "claude_code_local":
        if not settings.claude_code_repo_dir:
            raise RuntimeError(
                "CLAUDE_CODE_REPO_DIR is required when LLM_PROVIDER=claude_code_local. "
                "Set it to the local checkout of the repository to review."
            )
        return ClaudeCodeLocalAdapter(
            model=settings.llm_model or "claude-code-local",
            command=settings.claude_code_cmd,
            timeout_sec=settings.claude_code_timeout_sec,
            max_turns=settings.claude_code_max_turns,
            repo_dir=settings.claude_code_repo_dir,
        )
    return HeuristicLLMAdapter(model=settings.llm_model or "local-heuristic")
