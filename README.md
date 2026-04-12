# PR Intelligence

An intelligent GitHub repository monitoring service that uses LangGraph and LLM agents to automate PR review prioritization, issue tracking, and reporting.

**Originally built for Apache Polaris, but configurable for any GitHub repository.**

License: Apache-2.0. See [LICENSE](LICENSE).

## Features

✨ **Smart PR Prioritization** - Scores PRs based on staleness, activity, size, and review requests
🤖 **Multi-Provider LLM Support** - Claude Code, Codex, or rule-based analysis
📋 **Automated Reports** - Daily attention reports highlighting what needs review
🔍 **Deep PR Reviews** - Multi-turn LLM analysis with subagents
⏱️ **Periodic Refresh** - Automatic sync during configurable time windows
🚀 **REST API** - Full API with web dashboard and async review queue

## Quick Start

### Installation

```bash
./run.sh bootstrap
```

### Configuration

Set required environment variables:

```bash
export PR_INTEL_GITHUB_TOKEN=your_github_token
export LOCAL_REVIEW_REPO_DIR=/path/to/local/repo  # For Claude/Codex providers
```

Optional (with defaults):
```bash
export GITHUB_OWNER=apache          # Repository owner
export GITHUB_REPO=polaris          # Repository name
export LLM_PROVIDER=claude_code_local  # or codex_local, heuristic
export LLM_MODEL=opus               # Provider-specific model
```

See [Configuration Reference](docs/CONFIGURATION.md) for all options.

### Run the Server

```bash
./run.sh serve
```

Open:
- Dashboard: http://127.0.0.1:8080/ui
- API Docs: http://127.0.0.1:8080/docs

### Basic Usage

```bash
# Full refresh (sync + score + analyze + report)
./run.sh refresh

# View latest report
./run.sh report

# Queue deep review for PR #123
./run.sh review 123

# Sync review (wait for completion)
./run.sh review-sync 123
```

## Commands

```bash
./run.sh bootstrap              # Install dependencies
./run.sh serve                  # Start API server
./run.sh refresh                # Full refresh
./run.sh report                 # View latest report
./run.sh review <pr_number>     # Async PR review
./run.sh review-sync <pr_number> # Sync PR review
```

## API Examples

```bash
# Full refresh
curl -X POST "http://127.0.0.1:8080/refresh"

# View latest report
curl "http://127.0.0.1:8080/reports/daily/latest.md"

# Queue async review
curl -X POST "http://127.0.0.1:8080/reviews/pr/123/run"

# Check review status
curl "http://127.0.0.1:8080/reviews/pr/123/job"

# Get review results
curl "http://127.0.0.1:8080/reviews/pr/123/latest.md"

# Get prioritized queue
curl "http://127.0.0.1:8080/queues/needs-review"
```

See [API Reference](docs/API_REFERENCE.md) for complete endpoint documentation.

## Architecture

PR Intelligence uses:
- **LangGraph** for workflow orchestration
- **FastAPI** for REST API
- **SQLite** for persistence (or in-memory for testing)
- **Local CLI Providers** (Claude Code, Codex) for code-aware LLM analysis
- **APScheduler** for periodic refresh

### Key Components

- **EventGraph** - Processes GitHub webhook events
- **DailyReportGraph** - Generates attention reports
- **PRReviewGraph** - Deep PR review with subagents
- **Async Review Queue** - Parallel review processing

See [Architecture](docs/ARCHITECTURE.md) for detailed diagrams and code layout.

## Documentation

- 🛠️ [Configuration Reference](docs/CONFIGURATION.md) - All environment variables
- 🏛️ [Architecture](docs/ARCHITECTURE.md) - System design and diagrams
- 📚 [API Reference](docs/API_REFERENCE.md) - Complete endpoint documentation
- 🔧 [Development Guide](docs/DEVELOPMENT.md) - Setup, testing, contributing
- 🐞 [Troubleshooting](docs/TROUBLESHOOTING.md) - Common issues and solutions
- ⭐ [Self-Review Feature](docs/SELF_REVIEW.md) - 3-step LLM critique and revision
- 📑 [Agent Build Notes](docs/design/agent-build.md) - Real agent workflow system

## LLM Providers

### claude_code_local (Default)

Uses local Claude Code CLI for code-aware analysis.

```bash
export LLM_PROVIDER=claude_code_local
export LLM_MODEL=opus  # or sonnet, haiku
export LOCAL_REVIEW_REPO_DIR=/path/to/repo
```

### codex_local

Uses local Codex CLI for code-aware analysis.

```bash
export LLM_PROVIDER=codex_local
export LLM_MODEL=gpt-5.4  # or gpt-5.4-mini
export LOCAL_REVIEW_REPO_DIR=/path/to/repo
```

### heuristic

Rule-based scoring and analysis (no LLM calls).

```bash
export LLM_PROVIDER=heuristic
```

## Skills System

PR Intelligence uses separate skill files for different analysis tasks:

1. **`skills/polaris-pr-review/skill.md`** - Deep individual PR reviews
2. **`skills/polaris-attention-analysis/skill.md`** - Batch analysis across PRs

This allows different prompting strategies for deep vs. broad analysis.

## Self-Review (Experimental)

Optional 3-step process for higher quality reviews:

1. Generate initial findings
2. Critique findings (specificity, coverage, consistency, clarity)
3. Revise based on critique

```bash
export ENABLE_SELF_REVIEW=true  # Default
```

**Trade-offs:**
- ✅ Higher quality, more specific, better coverage
- ❌ ~3x latency, ~3x cost

See [Self-Review Feature](docs/SELF_REVIEW.md) for details.

## Periodic Refresh

Automatic refresh during configurable time windows:

```bash
export ENABLE_PERIODIC_REFRESH=true  # Default
export REFRESH_INTERVAL_MINUTES=30   # Default
export REFRESH_START_HOUR_LOCAL=8    # Default (8 AM)
export REFRESH_END_HOUR_LOCAL=23     # Default (11 PM)
export REFRESH_TIMEZONE=America/Los_Angeles  # Optional
```

## Development

### Running Tests

```bash
./run.sh bootstrap
pytest tests/
pytest --cov=polaris_pr_intel tests/  # With coverage
```

### Local Development

```bash
# Use in-memory storage for faster iteration
export STORE_BACKEND=memory

# Use heuristic provider for no LLM calls
export LLM_PROVIDER=heuristic

./run.sh serve
```

See [Development Guide](docs/DEVELOPMENT.md) for more details.

## Troubleshooting

### Common Issues

**Missing token:**
```bash
export PR_INTEL_GITHUB_TOKEN=your_token
```

**Port in use:**
```bash
PORT=9090 ./run.sh serve
```

**Review timeouts:**
```bash
export REVIEW_JOB_TIMEOUT_SEC=2400
```

**CLI not found:**
```bash
export CLAUDE_CODE_CMD=/path/to/claude
export CODEX_CMD=/path/to/codex
```

See [Troubleshooting Guide](docs/TROUBLESHOOTING.md) for complete solutions.

## Requirements

- Python 3.11+
- GitHub token (read-only is sufficient)
- Local git clone of monitored repo (for Claude/Codex providers)
- Claude Code or Codex CLI (for respective providers)

## License

Apache-2.0. See [LICENSE](LICENSE).
