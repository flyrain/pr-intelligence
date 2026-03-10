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
        return ClaudeCodeLocalAdapter(
            model=settings.llm_model or "claude-code-local",
            command=settings.claude_code_cmd,
            timeout_sec=settings.claude_code_timeout_sec,
        )
    return HeuristicLLMAdapter(model=settings.llm_model or "local-heuristic")
