# Configuration Reference

This document covers all environment variables available in PR Intelligence.

## Required Configuration

### Required
- **`PR_INTEL_GITHUB_TOKEN`** - GitHub API token (read-only is sufficient)

**That's it!** The system automatically manages repository access.

Note: `PR_INTEL_GITHUB_TOKEN` is the preferred project-specific variable. `GITHUB_TOKEN` is still accepted as a backward-compatible fallback.

## Repository Management

PR Intelligence automatically manages repository access:

1. **Auto-detection**: If running inside the target repository, uses that directory
2. **Auto-clone**: Otherwise, clones to `~/.cache/pr-intel/repos/{owner}-{repo}`
3. **Worktrees**: Creates isolated worktrees for each PR review (enabled by default)

### Optional Overrides

- **`GIT_REPO_PATH`** (optional) - Explicit path to use instead of auto-detection
  - Example: `/Users/you/code/polaris`
  - Overrides all auto-detection
  - For backward compatibility, `LOCAL_REVIEW_REPO_DIR` still works

- **`REPO_CACHE_DIR`** (optional) - Custom cache directory for auto-cloned repos
  - Default: `~/.cache/pr-intel/repos`
  - Example: `/tmp/pr-intel-cache`

- **`USE_WORKTREES`** (default: `true`) - Enable worktree isolation for PR reviews
  - When enabled, each PR review runs in its own isolated worktree
  - Allows parallel reviews without conflicts
  - Set to `false` to use traditional single-directory mode

- **`WORKTREE_BASE_DIR`** (optional) - Custom directory for worktrees
  - Default: `{repo}/.worktrees`
  - Example: `/tmp/pr-worktrees`

## Repository Settings

- **`GITHUB_OWNER`** (default: `apache`) - GitHub organization or user name
- **`GITHUB_REPO`** (default: `polaris`) - Repository name
- **`GITHUB_WEBHOOK_SECRET`** (optional) - Secret for webhook signature verification

## Storage Configuration

- **`STORE_BACKEND`** (default: `sqlite`) - Storage backend type
  - Options: `sqlite`, `memory`
- **`SQLITE_PATH`** (default: `.data/polaris_pr_intel.db`) - Path to SQLite database file

## LLM Provider Configuration

### Provider Selection
- **`LLM_PROVIDER`** (default: `claude_code_local`)
  - Options: `heuristic`, `claude_code_local`, `codex_local`
  - `heuristic` = rule-based local scoring/review only, no LLM calls

- **`LLM_MODEL`** (optional; provider-specific defaults)
  - **claude_code_local** (default: `opus`)
    - Valid values: `opus`, `sonnet`, `haiku`, `opus[1m]`, `sonnet[1m]`, `opusplan`
    - Or specific versions: `claude-opus-4-6`, `claude-sonnet-4-6`, `claude-haiku-4-5`, etc.
  - **codex_local** (default: `gpt-5.4`)
    - Valid values: `gpt-5.4`, `gpt-5.4-mini`, `gpt-5-codex` (availability varies by account)

### Skill Files
- **`REVIEW_SKILL_FILE`** (optional) - Skill used by individual PR review prompts
  - Default: `skills/polaris-pr-review/skill.md`
- **`ANALYSIS_SKILL_FILE`** (optional) - Skill used by post-sync report-analysis prompts
  - Default: `skills/polaris-attention-analysis/skill.md`

### Local Claude Code Provider
- **`CLAUDE_CODE_CMD`** (default: `claude`) - Command to invoke Claude Code CLI
- **`CLAUDE_CODE_TIMEOUT_SEC`** (default: `300`) - Timeout in seconds for Claude Code operations
- **`CLAUDE_CODE_MAX_TURNS`** (default: `15`) - Maximum conversation turns

### Local Codex Provider
- **`CODEX_CMD`** (default: `codex`) - Command to invoke Codex CLI
- **`CODEX_TIMEOUT_SEC`** (default: `900`) - Timeout in seconds for Codex operations
- **`CODEX_MAX_TURNS`** (default: `15`) - Maximum conversation turns
- **`CODEX_REASONING_EFFORT`** (default: `medium`) - Reasoning effort level passed to `codex exec`

## Review Job Configuration

- **`REVIEW_JOB_WORKERS`** (default: `1`) - Number of parallel async PR review workers
- **`REVIEW_JOB_TIMEOUT_SEC`** (default: `1200`) - Max time in seconds for a review job before marking as failed

## Analysis Configuration

- **`ANALYSIS_TOP_SLICE_LIMIT`** (default: `10`) - Reserved for future slicing logic, currently not used by `DerivedAnalysisAgent`

## Self-Review Feature

- **`ENABLE_SELF_REVIEW`** (default: `true`) - Enable the 3-step self-review process for PR reviews on local CLI providers
  - See [Self-Review documentation](SELF_REVIEW.md) for details

## Periodic Refresh Scheduler

- **`ENABLE_PERIODIC_REFRESH`** (default: `true`) - Enable automatic periodic refresh scheduler
- **`REFRESH_TIMEZONE`** (optional) - IANA timezone for the automatic refresh window
  - Example: `America/Los_Angeles`
  - Default: system local timezone
- **`REFRESH_INTERVAL_MINUTES`** (default: `30`) - Minutes between automatic refreshes during the refresh window
- **`REFRESH_START_HOUR_LOCAL`** (default: `8`) - First local hour included in the automatic refresh window
- **`REFRESH_END_HOUR_LOCAL`** (default: `23`) - Last local top-of-hour refresh in the automatic refresh window

## Scoring Parameters

These parameters control the deterministic scoring rules for PR prioritization.

### Thresholds
- **`REVIEW_NEEDED_THRESHOLD`** (default: `2.0`) - Minimum score for "needs review" classification
- **`ISSUE_INTERESTING_THRESHOLD`** (default: `2.0`) - Minimum score for "interesting" issue classification

### Staleness Points
- **`REVIEW_STALE_24H_POINTS`** (default: `1.5`) - Points added for PRs with no activity in last 24 hours
- **`REVIEW_STALE_72H_POINTS`** (default: `1.5`) - Points added for PRs with no activity in last 72 hours

### Inactivity Penalty
- **`REVIEW_INACTIVE_DAYS`** (default: `7`) - PRs with no activity past this age are downgraded
- **`REVIEW_INACTIVE_PENALTY_POINTS`** (default: `2.0`) - Points subtracted for inactive PRs

### Activity Tracking
- **`REVIEW_ACTIVITY_HOT_COMMENTS_24H_THRESHOLD`** (default: `5`) - Comment count for "hot" activity classification
- **`REVIEW_ACTIVITY_HOT_POINTS`** (default: `1.5`) - Points added for hot activity
- **`REVIEW_ACTIVITY_WARM_COMMENTS_24H_THRESHOLD`** (default: `2`) - Comment count for "warm" activity classification
- **`REVIEW_ACTIVITY_WARM_POINTS`** (default: `0.75`) - Points added for warm activity

### Review Request & Size
- **`REVIEW_REQUESTED_POINTS`** (default: `2.0`) - Points added when review is explicitly requested
- **`REVIEW_LARGE_DIFF_POINTS`** (default: `1.5`) - Points added for large diffs
- **`REVIEW_MEDIUM_DIFF_POINTS`** (default: `1.0`) - Points added for medium diffs
- **`REVIEW_MANY_FILES_POINTS`** (default: `1.0`) - Points added for PRs touching many files

### Target Reviewer
- **`REVIEW_TARGET_LOGIN`** (optional) - Used as a reviewer-specific signal in analysis and reporting
  - Note: No longer filters `GET /queues/needs-review`, which is now a repo-wide prioritized queue

## Current Limitations

- Attention ranking via `ANALYSIS_SKILL_FILE` is LLM-backed only for `claude_code_local` and `codex_local` providers
