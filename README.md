# PR Intelligence

An intelligent GitHub repository monitoring service that uses LangGraph and LLM agents to automate PR review prioritization, issue tracking, and reporting.

## Features

- **Smart PR Prioritization** - Scores PRs based on staleness, activity, size, and review requests
- **Multi-Provider LLM Support** - Claude Code, Codex, or rule-based analysis
- **Automated Reports** - Daily attention reports highlighting what needs review
- **Deep PR Reviews** - Multi-turn LLM analysis with subagents
- **Periodic Refresh** - Automatic sync during configurable time windows
- **REST API** - Full API with web dashboard and async review queue

## Quick Start
```bash
./run.sh bootstrap                     # install dependencies
export PR_INTEL_GITHUB_TOKEN=your_github_token  # config

# Optional configs (with defaults):
export GITHUB_OWNER=apache              # Repository owner
export GITHUB_REPO=polaris              # Repository name
export REVIEW_TARGET_LOGIN=flyrain
export LLM_PROVIDER=claude_code_local   # or codex_local, heuristic
export LLM_MODEL=opus                   # Provider-specific model

./run.sh serve                          # run it
```

Open:
- Dashboard: http://127.0.0.1:8080/ui
- API Docs: http://127.0.0.1:8080/docs

## Documentation

- 🛠️ [Configuration Reference](docs/CONFIGURATION.md) - All environment variables
- 🏛️ [Architecture](docs/ARCHITECTURE.md) - System design and diagrams
- 📚 [API Reference](docs/API_REFERENCE.md) - Complete endpoint documentation
- 🔧 [Development Guide](docs/DEVELOPMENT.md) - Setup, testing, contributing
- 🐞 [Troubleshooting](docs/TROUBLESHOOTING.md) - Common issues and solutions
- ⭐ [Self-Review Feature](docs/SELF_REVIEW.md) - 3-step LLM critique and revision
- 📑 [Agent Build Notes](docs/design/agent-build.md) - Real agent workflow system