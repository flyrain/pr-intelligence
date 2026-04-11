from __future__ import annotations

from pathlib import Path
from typing import Protocol

from polaris_pr_intel.config import Settings
from polaris_pr_intel.models import PRAttentionContext, PRAttentionDecision, PRSubagentFinding, PullRequestSnapshot


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


def build_llm_adapter(settings: Settings) -> LLMAdapter:
    provider = settings.llm_provider.lower()
    shared_repo_dir = (settings.local_review_repo_dir or "").strip()
    if provider == "heuristic":
        from polaris_pr_intel.llm._heuristic import HeuristicLLMAdapter

        return HeuristicLLMAdapter(model=settings.llm_model or "local-heuristic")
    if provider == "claude_code_local":
        from polaris_pr_intel.llm._claude_code_local import ClaudeCodeLocalAdapter

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
        from polaris_pr_intel.llm._codex_local import CodexLocalAdapter

        repo_dir = shared_repo_dir
        if not repo_dir:
            raise RuntimeError("LOCAL_REVIEW_REPO_DIR must not be empty when using local CLI providers.")
        if not Path(repo_dir).is_dir():
            raise RuntimeError(
                f"LOCAL_REVIEW_REPO_DIR is invalid: {repo_dir!r}. "
                "Set it to an existing local directory for code review."
            )
        return CodexLocalAdapter(
            model=settings.llm_model or "gpt-5.4",
            command=settings.codex_cmd,
            timeout_sec=settings.codex_timeout_sec,
            max_turns=settings.codex_max_turns,
            reasoning_effort=settings.codex_reasoning_effort,
            repo_dir=repo_dir,
            review_skill_file=settings.review_skill_file,
            analysis_skill_file=settings.analysis_skill_file,
        )
    supported = ", ".join(SUPPORTED_LLM_PROVIDERS)
    raise RuntimeError(f"Unsupported LLM_PROVIDER={provider!r}. Supported values: {supported}")
