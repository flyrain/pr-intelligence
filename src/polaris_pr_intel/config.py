from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    github_token: str
    github_owner: str = "apache"
    github_repo: str = "polaris"
    github_webhook_secret: str = ""
    review_needed_threshold: float = 2.0
    issue_interesting_threshold: float = 2.0
    review_stale_24h_points: float = 1.5
    review_stale_72h_points: float = 1.5
    review_inactive_days: int = 7
    review_inactive_penalty_points: float = 2.0
    review_activity_hot_comments_24h_threshold: int = 5
    review_activity_hot_points: float = 1.5
    review_activity_warm_comments_24h_threshold: int = 2
    review_activity_warm_points: float = 0.75
    review_requested_points: float = 2.0
    review_large_diff_points: float = 1.5
    review_medium_diff_points: float = 1.0
    review_many_files_points: float = 1.0
    review_target_login: str = ""
    store_backend: str = "sqlite"
    sqlite_path: str = ".data/polaris_pr_intel.db"
    llm_provider: str = "claude_code_local"
    llm_model: str = ""
    openai_api_key: str = ""
    gemini_api_key: str = ""
    anthropic_api_key: str = ""
    claude_code_cmd: str = "claude"
    claude_code_timeout_sec: int = 300
    claude_code_max_turns: int = 15
    review_skill_file: str = ""
    analysis_skill_file: str = ""
    claude_code_skill_file: str = ""
    local_review_repo_dir: str = ""
    codex_cmd: str = "codex"
    codex_timeout_sec: int = 300
    codex_max_turns: int = 15
    codex_reasoning_effort: str = "high"
    analysis_top_slice_limit: int = 10
    enable_periodic_refresh: bool = True
    refresh_timezone: str = ""
    refresh_interval_minutes: int = 30
    refresh_start_hour_local: int = 8
    refresh_end_hour_local: int = 23
    enable_self_review: bool = True


def _float_env(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return float(raw)
    except ValueError as exc:
        raise RuntimeError(f"{name} must be a float") from exc


def _int_env(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise RuntimeError(f"{name} must be an int") from exc


def _hour_env(name: str, default: int) -> int:
    value = _int_env(name, default)
    if not 0 <= value <= 23:
        raise RuntimeError(f"{name} must be between 0 and 23")
    return value


def _codex_reasoning_effort_env(name: str, default: str) -> str:
    value = os.getenv(name, default).strip().lower() or default
    if value not in {"low", "medium", "high"}:
        raise RuntimeError(f"{name} must be one of: low, medium, high")
    return value


def load_settings() -> Settings:
    token = os.getenv("PR_INTEL_GITHUB_TOKEN", "").strip() or os.getenv("GITHUB_TOKEN", "").strip()
    if not token:
        raise RuntimeError("PR_INTEL_GITHUB_TOKEN or GITHUB_TOKEN is required")
    return Settings(
        github_token=token,
        github_owner=os.getenv("GITHUB_OWNER", "apache"),
        github_repo=os.getenv("GITHUB_REPO", "polaris"),
        github_webhook_secret=os.getenv("GITHUB_WEBHOOK_SECRET", ""),
        review_needed_threshold=_float_env("REVIEW_NEEDED_THRESHOLD", 2.0),
        issue_interesting_threshold=_float_env("ISSUE_INTERESTING_THRESHOLD", 2.0),
        review_stale_24h_points=_float_env("REVIEW_STALE_24H_POINTS", 1.5),
        review_stale_72h_points=_float_env("REVIEW_STALE_72H_POINTS", 1.5),
        review_inactive_days=_int_env("REVIEW_INACTIVE_DAYS", 7),
        review_inactive_penalty_points=_float_env("REVIEW_INACTIVE_PENALTY_POINTS", 2.0),
        review_activity_hot_comments_24h_threshold=_int_env("REVIEW_ACTIVITY_HOT_COMMENTS_24H_THRESHOLD", 5),
        review_activity_hot_points=_float_env("REVIEW_ACTIVITY_HOT_POINTS", 1.5),
        review_activity_warm_comments_24h_threshold=_int_env("REVIEW_ACTIVITY_WARM_COMMENTS_24H_THRESHOLD", 2),
        review_activity_warm_points=_float_env("REVIEW_ACTIVITY_WARM_POINTS", 0.75),
        review_requested_points=_float_env("REVIEW_REQUESTED_POINTS", 2.0),
        review_large_diff_points=_float_env("REVIEW_LARGE_DIFF_POINTS", 1.5),
        review_medium_diff_points=_float_env("REVIEW_MEDIUM_DIFF_POINTS", 1.0),
        review_many_files_points=_float_env("REVIEW_MANY_FILES_POINTS", 1.0),
        review_target_login=(
            os.getenv("REVIEW_TARGET_LOGIN", "").strip()
            or os.getenv("GITHUB_REVIEWER_LOGIN", "").strip()
        ),
        store_backend=os.getenv("STORE_BACKEND", "sqlite").lower(),
        sqlite_path=os.getenv("SQLITE_PATH", ".data/polaris_pr_intel.db"),
        llm_provider=os.getenv("LLM_PROVIDER", "claude_code_local").lower(),
        llm_model=os.getenv("LLM_MODEL", ""),
        openai_api_key=os.getenv("OPENAI_API_KEY", ""),
        gemini_api_key=os.getenv("GEMINI_API_KEY", ""),
        anthropic_api_key=os.getenv("ANTHROPIC_API_KEY", ""),
        claude_code_cmd=os.getenv("CLAUDE_CODE_CMD", "claude"),
        claude_code_timeout_sec=_int_env("CLAUDE_CODE_TIMEOUT_SEC", 300),
        claude_code_max_turns=_int_env("CLAUDE_CODE_MAX_TURNS", 15),
        review_skill_file=os.getenv(
            "REVIEW_SKILL_FILE",
            os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "skills", "polaris-pr-review", "skill.md"),
        ),
        analysis_skill_file=os.getenv(
            "ANALYSIS_SKILL_FILE",
            os.path.join(
                os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
                "skills",
                "polaris-attention-analysis",
                "skill.md",
            ),
        ),
        claude_code_skill_file=os.getenv(
            "CLAUDE_CODE_SKILL_FILE",
            os.getenv(
                "REVIEW_SKILL_FILE",
                os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "skills", "polaris-pr-review", "skill.md"),
            ),
        ),
        local_review_repo_dir=(
            os.getenv("LOCAL_REVIEW_REPO_DIR", "").strip()
            or os.getenv("CLAUDE_CODE_REPO_DIR", "").strip()
            or os.getenv("CODEX_REPO_DIR", "").strip()
        ),
        codex_cmd=os.getenv("CODEX_CMD", "codex"),
        codex_timeout_sec=_int_env("CODEX_TIMEOUT_SEC", 300),
        codex_max_turns=_int_env("CODEX_MAX_TURNS", 15),
        codex_reasoning_effort=_codex_reasoning_effort_env("CODEX_REASONING_EFFORT", "high"),
        analysis_top_slice_limit=_int_env("ANALYSIS_TOP_SLICE_LIMIT", 10),
        enable_periodic_refresh=os.getenv("ENABLE_PERIODIC_REFRESH", "true").lower() in ("true", "1", "yes"),
        refresh_timezone=os.getenv("REFRESH_TIMEZONE", "").strip(),
        refresh_interval_minutes=_int_env("REFRESH_INTERVAL_MINUTES", 30),
        refresh_start_hour_local=_hour_env("REFRESH_START_HOUR_LOCAL", 8),
        refresh_end_hour_local=_hour_env("REFRESH_END_HOUR_LOCAL", 23),
        enable_self_review=os.getenv("ENABLE_SELF_REVIEW", "true").lower() in ("true", "1", "yes"),
    )
