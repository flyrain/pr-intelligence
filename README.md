# Polaris PR Intelligence (LangGraph + GitHub API)

A service for monitoring a github repo pull requests and issues, scoring review priority, running LLM subagent deep reviews, and generating daily reports.

License: Apache-2.0. See [LICENSE](LICENSE).

## Quick Start

### 1) Setup + run
```bash
./run.sh bootstrap

export GITHUB_TOKEN=your_read_only_token
export LOCAL_REVIEW_REPO_DIR=/path/to/apache/polaris
./run.sh serve
```

`run.sh` now prefers `uv` automatically when installed, and falls back to `.venv` otherwise.

Open:
- `http://127.0.0.1:8080/ui` (dashboard)
- `http://127.0.0.1:8080/docs` (API docs)

### Which command updates what?

- Update **New/Updated PRs Today** tab data (from latest PR snapshots) and recompute queues:
```bash
./run.sh sync-all
```

- Generate/update **Latest Report** tab content:
```bash
./run.sh report
```
By default this runs refresh first (`refresh=true`) with full sync-all semantics:
- sync open PRs/issues
- prune stale locally-open PRs no longer open on GitHub
- recompute review/issue signals
- then generate the report markdown

- Full refresh (recommended):
```bash
./run.sh sync-all
./run.sh report
```

### 2) Typical workflow
```bash
# 1. sync data
./run.sh sync-all

# 2. run deep review on one PR (async)
./run.sh review 123

# 3. or sync review on one PR (wait for result)
./run.sh review-sync 123

# 4. generate report
./run.sh report
```

### 3) Common curl equivalents
```bash
# sync all open PRs/issues
curl -X POST "http://127.0.0.1:8080/sync/all-open?per_page=100&max_pages=20"
# (default) also marks stale locally-open PRs as closed when no longer in GitHub open list
# (default) also recomputes needs-review / interesting-issues queues

# recompute needs-review / interesting-issues queues
curl -X POST "http://127.0.0.1:8080/scores/recompute"

# async deep review
curl -X POST "http://127.0.0.1:8080/reviews/pr/123/run"

# check latest job by PR number
curl "http://127.0.0.1:8080/reviews/pr/123/job"

# sync deep review
curl -X POST "http://127.0.0.1:8080/reviews/pr/123/run?wait=true"

# generate report
curl -X POST "http://127.0.0.1:8080/reports/daily/run"
# (default: refresh=true, recompute=true, prune_missing_open_prs=true)

# latest markdown report
curl "http://127.0.0.1:8080/reports/daily/latest.md"
```

## `run.sh` Commands

```bash
./run.sh serve                # start API server
./run.sh sync-all             # sync all open PRs/issues + recompute review/issue signals
./run.sh sync                 # sync recent PRs/issues
./run.sh report               # generate + print daily report
./run.sh review 123           # async deep review for PR 123
./run.sh review-sync 123      # sync deep review for PR 123
./run.sh run-daily            # run daily graph via CLI
./run.sh bootstrap            # install dependencies (uv if available, else .venv)
./run.sh install              # sync/install dependencies
```

Override host/port:
```bash
PORT=9090 ./run.sh serve
```

## Required Configuration

### Required
- `GITHUB_TOKEN`
- `LOCAL_REVIEW_REPO_DIR` (required when using local CLI providers: `claude_code_local` / `codex_local`)

### Common optional
- `GITHUB_OWNER` (default: `apache`)
- `GITHUB_REPO` (default: `polaris`)
- `GITHUB_WEBHOOK_SECRET` (optional)
- `STORE_BACKEND` (default: `sqlite`)
- `SQLITE_PATH` (default: `.data/polaris_pr_intel.db`)
- `REVIEW_JOB_WORKERS` (default: `1`; async PR review worker count, higher values increase concurrency)

### LLM provider selection
- `LLM_PROVIDER` (default: `claude_code_local`)
  - supported: `heuristic`, `openai`, `gemini`, `anthropic`, `claude_code_local`, `codex_local`
- `LLM_MODEL` (optional; provider-specific default when unset)

### Local Claude Code provider
- `CLAUDE_CODE_CMD` (default: `claude`)
- `CLAUDE_CODE_TIMEOUT_SEC` (default: `300`)
- `CLAUDE_CODE_MAX_TURNS` (default: `15`)

### Local Codex provider
- `CODEX_CMD` (default: `codex`)
- `CODEX_TIMEOUT_SEC` (default: `300`)
- `CODEX_MAX_TURNS` (default: `15`)

### Scoring knobs
- `REVIEW_NEEDED_THRESHOLD` (default: `2.0`)
- `REVIEW_TARGET_LOGIN` (optional; if this login is in `requested_reviewers`, PR is always included in "PRs Needing Review")
- `ISSUE_INTERESTING_THRESHOLD` (default: `2.0`)
- `REVIEW_STALE_24H_POINTS` (default: `1.5`)
- `REVIEW_STALE_72H_POINTS` (default: `1.5`)
- `REVIEW_REQUESTED_POINTS` (default: `2.0`)
- `REVIEW_LARGE_DIFF_POINTS` (default: `1.5`)
- `REVIEW_MEDIUM_DIFF_POINTS` (default: `1.0`)
- `REVIEW_MANY_FILES_POINTS` (default: `1.0`)

## API Overview

- `GET /`, `GET /ui`, `GET /docs`, `GET /healthz`
- `POST /sync/recent`, `POST /sync/all-open`
- `POST /scores/recompute`
- `POST /reports/daily/run` (default refresh path uses sync-all semantics + recompute)
- `GET /reports/daily/latest`, `GET /reports/daily/latest.md`, `GET /reports/daily`
- `POST /reviews/pr/{pr_number}/run` (async by default)
- `POST /reviews/pr/{pr_number}/run-sync`
- `GET /reviews/pr/{pr_number}/job`, `GET /reviews/jobs/{job_id}`
- `GET /reviews/pr/{pr_number}/latest`, `GET /reviews/pr/top`
- `POST /reviews/run-open`
- `GET /queues/needs-review`, `GET /queues/interesting-issues`

## Provider Notes

- Adapter layer is provider-agnostic.
- Local providers (`claude_code_local`, `codex_local`) use your local repo path for code-aware analysis.
- If CLI execution fails or output parsing fails, adapters fall back to deterministic heuristic output.
- Async review jobs are queued in-memory.
- Repeated async requests for the same PR while a job is `queued`/`running` are deduplicated and return the existing `job_id` (`deduplicated: true`).

### Async Review Queue (Detailed)

![PR review queue architecture](docs/review-queue-diagram.png)

```mermaid
sequenceDiagram
    autonumber
    participant U as User / UI
    participant API as FastAPI (/reviews/pr/{pr}/run)
    participant JM as Job Map (in-memory)
    participant Q as Job Queue (in-memory)
    participant W as Worker (REVIEW_JOB_WORKERS)
    participant G as PRReviewGraph
    participant S as Store (SQLite/InMemory)

    U->>API: POST run (wait=false, pr=3960)
    API->>JM: Check existing queued/running for PR 3960
    alt Existing inflight job
        API-->>U: 200 accepted, deduplicated=true, existing job_id
    else No inflight job
        API->>JM: Create job status=queued
        API->>Q: Enqueue job_id
        API-->>U: 200 accepted, deduplicated=false, new job_id
    end

    W->>Q: Dequeue next job_id
    W->>JM: status=running, started_at=now
    W->>G: invoke(pr_number)
    G->>S: load PR, run subagents, aggregate, persist report
    alt Success
        W->>JM: status=completed, finished_at, result.ok=true
    else Failure
        W->>JM: status=failed, finished_at, result.ok=false
    end

    U->>API: GET /reviews/jobs/{job_id}
    API->>JM: Read job record
    API-->>U: status + result
```

```mermaid
stateDiagram-v2
    [*] --> queued: job created
    queued --> running: worker dequeues job
    running --> completed: graph succeeds
    running --> failed: graph error / timeout
    queued --> failed: invalid job canceled (defensive path)
```

```mermaid
flowchart LR
    subgraph Ingress["Incoming async review requests"]
      A["PR 101 request"]
      B["PR 102 request"]
      C["PR 101 request again"]
      D["PR 103 request"]
    end

    A --> M
    B --> M
    C --> M
    D --> M

    M{"Deduplicate\nsame PR queued/running?"}
    M -->|yes| X["Return existing job_id\n(no new queue entry)"]
    M -->|no| Q["FIFO queue"]

    Q --> W1["Worker 1"]
    Q --> W2["Worker 2"]
    Q --> W3["Worker N"]

    W1 --> R1["Run PRReviewGraph(PR 101)"]
    W2 --> R2["Run PRReviewGraph(PR 102)"]
    W3 --> R3["Run PRReviewGraph(PR 103)"]

    note1["With N>1 workers, jobs are dequeued FIFO,\nbut completion order depends on runtime duration."]
    Q -.-> note1
```

## Architecture (Reference)

### Features
- GitHub webhook ingestion (`pull_request`, `issues`, `issue_comment`, `pull_request_review`)
- LangGraph event pipeline (summarization + deterministic scoring)
- LangGraph PR deep review pipeline (subagents + aggregation)
- Daily report pipeline
- FastAPI + SQLite default persistence

### Code layout
- `src/polaris_pr_intel/api` - FastAPI app
- `src/polaris_pr_intel/github` - GitHub API client
- `src/polaris_pr_intel/graphs` - LangGraph workflows
- `src/polaris_pr_intel/agents` - task agents
- `src/polaris_pr_intel/llm` - provider-agnostic LLM adapter layer
- `src/polaris_pr_intel/store` - repository layer
- `src/polaris_pr_intel/scoring` - deterministic scoring
- `src/polaris_pr_intel/scheduler` - daily scheduler

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
    PRG["PRReviewGraph (LangGraph)<br/>load PR -> subagents -> aggregate -> persist"] --> STORE
    DG --> PUB["ConsolePublisher (extensible)"]

    A1 --> STORE["Repository Layer<br/>InMemoryRepository or SQLiteRepository"]
    A2 --> STORE
    A3 --> STORE
    A4 --> STORE
    API --> STORE
    API --> PRG

    SCHED["DailyScheduler (APScheduler)"] --> DG
```
