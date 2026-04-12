# Development Guide

Guide for developing and contributing to PR Intelligence.

## Prerequisites

- **Python 3.11+** required
- **uv** recommended for faster dependency management (optional)
- **Git** for version control

## Setup

### 1. Clone the Repository

```bash
git clone <repository-url>
cd pr-intelligence
```

### 2. Install Dependencies

```bash
./run.sh bootstrap
```

This will:
- Automatically detect and use `uv` if available
- Fall back to `.venv` and pip if `uv` is not installed
- Install all dependencies from `pyproject.toml`

### 3. Configure Environment

Create a `.env` file or export environment variables:

```bash
export PR_INTEL_GITHUB_TOKEN=your_token_here
export LOCAL_REVIEW_REPO_DIR=/path/to/local/repo
```

See [CONFIGURATION.md](CONFIGURATION.md) for all available options.

## Running Tests

### Run All Tests

```bash
pytest tests/
```

### Run with Coverage

```bash
pytest --cov=polaris_pr_intel tests/
```

### Run Specific Tests

```bash
pytest tests/test_specific.py
pytest tests/test_specific.py::test_function_name
```

### Watch Mode

```bash
pytest-watch
```

## Project Structure

```
pr-intelligence/
├── src/polaris_pr_intel/     # Main source code
│   ├── api/                  # FastAPI endpoints
│   ├── agents/               # Task-specific agents
│   ├── graphs/               # LangGraph workflows
│   ├── llm/                  # LLM adapter layer
│   ├── store/                # Storage layer
│   ├── scoring/              # Scoring rules
│   ├── scheduler/            # Periodic refresh
│   └── config.py             # Configuration
├── skills/                   # LLM skill prompts
├── tests/                    # Test suite
├── docs/                     # Documentation
├── run.sh                    # Main CLI script
└── pyproject.toml            # Project metadata
```

## Adding New Features

### 1. Adding a New Agent

Create a new file in `src/polaris_pr_intel/agents/`:

```python
from polaris_pr_intel.models import PR
from polaris_pr_intel.store.base import Repository

class MyNewAgent:
    def __init__(self, repo: Repository):
        self.repo = repo
    
    def process(self, pr: PR) -> dict:
        # Agent logic here
        return {"result": "..."}
```

Add tests in `tests/agents/test_my_new_agent.py`.

### 2. Adding a New Graph Workflow

Create a new file in `src/polaris_pr_intel/graphs/`:

```python
from langgraph.graph import StateGraph
from polaris_pr_intel.graphs.state import MyState

class MyNewGraph:
    def __init__(self, repo):
        self.repo = repo
        self.graph = self._build_graph()
    
    def _build_graph(self) -> StateGraph:
        # Build LangGraph workflow
        pass
    
    def invoke(self, input_data):
        return self.graph.invoke(input_data)
```

### 3. Adding a New API Endpoint

Edit `src/polaris_pr_intel/api/app.py`:

```python
@app.get("/my-new-endpoint")
async def my_new_endpoint():
    # Endpoint logic
    return {"result": "..."}
```

### 4. Adding a New LLM Provider

Create a new adapter in `src/polaris_pr_intel/llm/`:

```python
from polaris_pr_intel.llm.llm_adapter import LLMAdapter

class MyNewProvider(LLMAdapter):
    @property
    def provider(self) -> str:
        return "my_provider"
    
    @property
    def model(self) -> str:
        return self._model
    
    def review_pr(self, pr_data: dict) -> dict:
        # Provider-specific implementation
        pass
```

Register it in `src/polaris_pr_intel/llm/__init__.py`.

## Code Style

### Formatting

Use standard Python formatting:
- 4 spaces for indentation
- Max line length: 120 characters
- Follow PEP 8 conventions

### Type Hints

Use type hints for function parameters and return values:

```python
def process_pr(pr: PR, score: float) -> dict[str, Any]:
    ...
```

### Docstrings

Use docstrings for public functions and classes:

```python
def calculate_score(pr: PR) -> float:
    \"\"\"Calculate priority score for a PR.
    
    Args:
        pr: The PR to score
    
    Returns:
        Priority score (higher = more urgent)
    \"\"\"
    ...
```

## Dependency Management

### Using uv (Recommended)

```bash
# Add a dependency
uv add package-name

# Add a dev dependency
uv add --dev package-name

# Update dependencies
uv sync

# Lock dependencies
uv lock
```

### Using pip

```bash
# Add to pyproject.toml, then:
pip install -e .

# Or install directly:
pip install package-name
```

## Debugging

### Enable Debug Logging

The service logs at INFO level by default. For more detail, modify `src/polaris_pr_intel/main.py`:

```python
logging.basicConfig(level=logging.DEBUG, ...)
```

### Interactive Debugging

Use Python's built-in debugger:

```python
import pdb; pdb.set_trace()
```

Or use your IDE's debugger with breakpoints.

### Testing with Different Providers

Switch providers for testing:

```bash
# Test with rule-based provider (no LLM)
export LLM_PROVIDER=heuristic
./run.sh serve

# Test with Claude Code
export LLM_PROVIDER=claude_code_local
export LOCAL_REVIEW_REPO_DIR=/path/to/repo
./run.sh serve
```

### Using In-Memory Storage

For faster iteration during development:

```bash
export STORE_BACKEND=memory
./run.sh serve
```

## Common Development Tasks

### Running the Server Locally

```bash
./run.sh serve
```

### Triggering a Refresh

```bash
./run.sh refresh
```

### Viewing the Report

```bash
./run.sh report
```

### Queueing a Review

```bash
./run.sh review 123
```

### Making API Calls

```bash
curl -X POST "http://127.0.0.1:8080/refresh"
curl "http://127.0.0.1:8080/reports/daily/latest.md"
```

## Building for Production

### Environment Variables

Set all required configuration for your environment. See [CONFIGURATION.md](CONFIGURATION.md).

### Database Setup

Ensure SQLite path is writable:

```bash
mkdir -p .data
export SQLITE_PATH=.data/polaris_pr_intel.db
```

### Process Management

Use a process manager like systemd, supervisor, or Docker:

```bash
# Example systemd service
[Unit]
Description=PR Intelligence Service
After=network.target

[Service]
Type=simple
User=youruser
WorkingDirectory=/path/to/pr-intelligence
Environment="PR_INTEL_GITHUB_TOKEN=..."
Environment="LOCAL_REVIEW_REPO_DIR=..."
ExecStart=/path/to/run.sh serve
Restart=on-failure

[Install]
WantedBy=multi-user.target
```

## Contributing

### Branch Workflow

1. Create a feature branch: `git checkout -b feature/my-feature`
2. Make changes and commit: `git commit -m "Add my feature"`
3. Push to remote: `git push origin feature/my-feature`
4. Open a pull request

### Commit Messages

Follow conventional commits:

- `feat:` New feature
- `fix:` Bug fix
- `docs:` Documentation changes
- `test:` Test changes
- `refactor:` Code refactoring
- `chore:` Maintenance tasks

Example:
```
feat: add support for GitLab webhooks

- Add GitLab webhook parser
- Update event graph to handle GitLab events
- Add tests for GitLab integration
```

### Pull Request Guidelines

- Include tests for new features
- Update documentation as needed
- Ensure all tests pass
- Keep PRs focused and reasonably sized
- Respond to review feedback promptly

## Release Process

1. Update version in `pyproject.toml`
2. Update CHANGELOG.md
3. Create a git tag: `git tag v0.2.0`
4. Push tag: `git push origin v0.2.0`
5. Create GitHub release with notes

## Getting Help

- Check [TROUBLESHOOTING.md](TROUBLESHOOTING.md) for common issues
- Review [ARCHITECTURE.md](ARCHITECTURE.md) for system design
- Read existing code and tests for examples
- Ask questions in issues or discussions
