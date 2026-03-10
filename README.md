# Polaris PR Intelligence (LangGraph + GitHub API)

Python service for monitoring `apache/polaris` pull requests and issues, scoring review priority, and generating daily reports.

## Features
- GitHub webhook ingestion (`pull_request`, `issues`, `issue_comment`, `pull_request_review`)
- LangGraph event pipeline:
  - PR summarization
  - PR needs-review scoring
  - interesting-issue scoring
- Daily report pipeline
- FastAPI service endpoints
- SQLite persistence by default (`STORE_BACKEND=sqlite`)

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

## Architecture

```mermaid
flowchart LR
    GH["GitHub (Webhooks + REST API)"] --> API["FastAPI API Layer<br/>/webhooks, /sync, /reports, /queues"]
    CLI["CLI (polaris-pr-intel)"] --> API
    API --> EG["EventGraph (LangGraph)<br/>ingest -> route -> summarize/score"]
    API --> DG["DailyReportGraph (LangGraph)<br/>generate -> publish"]
    API --> ING["SnapshotIngestor"]

    ING --> GHC["GitHubClient (read-only GET)"]
    GHC --> GH

    EG --> A1["PRSummarizerAgent"]
    EG --> A2["ReviewNeedAgent + scoring.rules"]
    EG --> A3["IssueInsightAgent + scoring.rules"]
    DG --> A4["DailyReporterAgent"]
    DG --> PUB["ConsolePublisher (extensible)"]

    A1 --> STORE["Repository Layer<br/>InMemoryRepository or SQLiteRepository"]
    A2 --> STORE
    A3 --> STORE
    A4 --> STORE
    API --> STORE

    SCHED["DailyScheduler (APScheduler)"] --> DG
```

### Component responsibilities
- **API layer**: receives webhooks, exposes manual sync/report endpoints, and serves queue/report queries.
- **EventGraph**: processes incoming PR/issue events and writes summaries/signals.
- **DailyReportGraph**: builds and publishes daily markdown reports.
- **GitHubClient**: reads PR/issue data from GitHub API.
- **Repository layer**: persists snapshots, signals, reports, and webhook idempotency keys.
- **Scheduler**: triggers daily report runs automatically.

## Sequence flows

### 1) Webhook event processing

```mermaid
sequenceDiagram
    participant GH as GitHub
    participant API as FastAPI /webhooks/github
    participant EG as EventGraph
    participant AG as Agents (PR/Issue)
    participant RS as Repository

    GH->>API: POST webhook event
    API->>API: Verify signature + dedupe delivery id
    API->>EG: invoke(event)
    EG->>RS: upsert PR/Issue snapshot
    EG->>AG: summarize/score
    AG->>RS: save summary/signal
    EG-->>API: notifications
    API-->>GH: 200 OK
```

### 2) Daily report generation

```mermaid
sequenceDiagram
    participant S as Scheduler or User
    participant API as FastAPI /reports/daily/run
    participant DG as DailyReportGraph
    participant DR as DailyReporterAgent
    participant RS as Repository
    participant PUB as ConsolePublisher

    S->>API: POST /reports/daily/run
    API->>DG: invoke()
    DG->>DR: run(repo)
    DR->>RS: read PRs/issues/signals
    DR-->>DG: DailyReport(markdown)
    DG->>RS: save_daily_report
    DG->>PUB: publish_daily_report
    DG-->>API: notifications
    API-->>S: 200 OK
```

## Run
```bash
cd /Users/yufei/projects/my-town/pr-intel
python -m venv .venv
source .venv/bin/activate
pip install -e .
export GITHUB_TOKEN=your_read_only_token
polaris-pr-intel serve --host 0.0.0.0 --port 8080
```

Open:
- `http://127.0.0.1:8080/` (service overview)
- `http://127.0.0.1:8080/ui` (dashboard UI)
- `http://127.0.0.1:8080/docs` (interactive API)

## Required env vars
- `GITHUB_TOKEN` - GitHub App installation token or PAT
- `GITHUB_OWNER` (default: `apache`)
- `GITHUB_REPO` (default: `polaris`)
- `GITHUB_WEBHOOK_SECRET` (optional)

## Storage backend
- `STORE_BACKEND` (default: `sqlite`) - `memory` or `sqlite`
- `SQLITE_PATH` (default: `.data/polaris_pr_intel.db`) - used when `STORE_BACKEND=sqlite`

## Optional scoring config
- `REVIEW_NEEDED_THRESHOLD` (default: `2.0`)
- `ISSUE_INTERESTING_THRESHOLD` (default: `2.0`)
- `REVIEW_STALE_24H_POINTS` (default: `1.5`)
- `REVIEW_STALE_72H_POINTS` (default: `1.5`)
- `REVIEW_REQUESTED_POINTS` (default: `2.0`)
- `REVIEW_LARGE_DIFF_POINTS` (default: `1.5`)
- `REVIEW_MEDIUM_DIFF_POINTS` (default: `1.0`)
- `REVIEW_MANY_FILES_POINTS` (default: `1.0`)

## API
- `GET /`
- `GET /ui`
- `POST /webhooks/github`
- `POST /reports/daily/run`
- `POST /sync/recent`
- `POST /sync/all-open`
- `GET /stats`
- `GET /reports/daily/latest`
- `GET /reports/daily/latest.md`
- `GET /reports/daily`
- `GET /queues/needs-review`
- `GET /queues/interesting-issues`
- `GET /healthz`

## Quick workflow
1. Pull recent data from GitHub:
   - `POST /sync/all-open`
2. Generate a report:
   - `POST /reports/daily/run` (refreshes from GitHub by default)
3. View report:
   - `GET /reports/daily/latest.md`
