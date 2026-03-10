# Polaris PR Intelligence (LangGraph + GitHub API)

Minimal runnable skeleton for monitoring `apache/polaris` pull requests and issues.

## Features
- GitHub webhook ingestion (`pull_request`, `issues`, `issue_comment`, `pull_request_review`)
- LangGraph event pipeline:
  - PR summarization
  - PR needs-review scoring
  - interesting-issue scoring
- Daily report pipeline
- FastAPI service endpoints

## Layout
- `src/polaris_pr_intel/api` - FastAPI app
- `src/polaris_pr_intel/github` - GitHub API client
- `src/polaris_pr_intel/graphs` - LangGraph workflows
- `src/polaris_pr_intel/agents` - task agents
- `src/polaris_pr_intel/ingest.py` - periodic GitHub snapshot ingestion
- `src/polaris_pr_intel/scoring` - deterministic scoring
- `src/polaris_pr_intel/store` - repository layer
- `src/polaris_pr_intel/publish` - report/notification sinks
- `src/polaris_pr_intel/scheduler` - daily scheduler

## Run
```bash
cd tools/pr-intel
python -m venv .venv
source .venv/bin/activate
pip install -e .
polaris-pr-intel serve --host 0.0.0.0 --port 8080
```

## Required env vars
- `GITHUB_TOKEN` - GitHub App installation token or PAT
- `GITHUB_OWNER` (default: `apache`)
- `GITHUB_REPO` (default: `polaris`)
- `GITHUB_WEBHOOK_SECRET` (optional)

## API
- `POST /webhooks/github`
- `POST /reports/daily/run`
- `POST /sync/recent`
- `GET /reports/daily/latest`
- `GET /queues/needs-review`
- `GET /queues/interesting-issues`
- `GET /healthz`
