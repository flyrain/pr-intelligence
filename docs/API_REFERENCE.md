# API Reference

PR Intelligence provides a REST API for all operations. The API is documented with OpenAPI at `/docs`.

## Base URL

```
http://127.0.0.1:8080
```

Override with:
```bash
PORT=9090 ./run.sh serve
```

## Core Workflow

### POST /refresh

Full refresh: sync + score + analyze + report

This is the main command that:
- Syncs open PRs/issues from GitHub
- Prunes stale locally-open PRs no longer open on GitHub
- Recomputes review/issue priority scores using deterministic scoring rules
- Runs post-sync derived analysis in a batched LLM call for the top PR slice
- Persists one structured analysis run plus its derived artifacts

**Example:**
```bash
curl -X POST \"http://127.0.0.1:8080/refresh\"
```

## Reports

### GET /reports/daily/latest.md

Latest generated report (markdown rendered from the latest persisted analysis run)

**Example:**
```bash
curl \"http://127.0.0.1:8080/reports/daily/latest.md\"
```

## PR Reviews (Deep Analysis)

### POST /reviews/pr/{pr_number}/run

Queue an async review for a PR. Returns immediately with a job ID.

**Query Parameters:**
- `wait` (optional, default: false) - Wait for completion if true

**Example (async):**
```bash
curl -X POST \"http://127.0.0.1:8080/reviews/pr/123/run\"
```

**Example (sync):**
```bash
curl -X POST \"http://127.0.0.1:8080/reviews/pr/123/run?wait=true\"
```

**Response:**
```json
{
  \"job_id\": \"abc123\",
  \"pr_number\": 123,
  \"status\": \"queued\",
  \"deduplicated\": false
}
```

### POST /reviews/pr/{pr_number}/run-sync

Alias for `POST /reviews/pr/{pr_number}/run?wait=true` - waits for completion.

**Example:**
```bash
curl -X POST \"http://127.0.0.1:8080/reviews/pr/123/run-sync\"
```

### GET /reviews/pr/{pr_number}/job

Get the latest job status for a specific PR number.

**Example:**
```bash
curl \"http://127.0.0.1:8080/reviews/pr/123/job\"
```

**Response:**
```json
{
  \"job_id\": \"abc123\",
  \"pr_number\": 123,
  \"status\": \"completed\",
  \"created_at\": \"2026-04-12T10:00:00Z\",
  \"started_at\": \"2026-04-12T10:00:05Z\",
  \"finished_at\": \"2026-04-12T10:05:30Z\",
  \"result\": {
    \"ok\": true,
    \"message\": \"Review completed\"
  }
}
```

### GET /reviews/jobs/{job_id}

Get job status by job ID.

**Example:**
```bash
curl \"http://127.0.0.1:8080/reviews/jobs/abc123\"
```

### GET /reviews/pr/{pr_number}/latest

Latest review result for a PR (JSON format).

**Example:**
```bash
curl \"http://127.0.0.1:8080/reviews/pr/123/latest\"
```

**Response:**
```json
{
  \"pr_number\": 123,
  \"reviewed_at\": \"2026-04-12T10:05:30Z\",
  \"findings\": [
    {
      \"type\": \"issue\",
      \"severity\": \"medium\",
      \"message\": \"Missing error handling\",
      \"file\": \"src/main.py\",
      \"line\": 42
    }
  ],
  \"summary\": \"Overall assessment...\"
}
```

### GET /reviews/pr/{pr_number}/latest.md

Latest review result for a PR (markdown format with PR metadata, review analysis, and findings).

**Example:**
```bash
curl \"http://127.0.0.1:8080/reviews/pr/123/latest.md\"
```

### GET /reviews/pr/{pr_number}/latest.html

Latest review result for a PR (rendered HTML page).

**Example:**
```bash
open \"http://127.0.0.1:8080/reviews/pr/123/latest.html\"
```

### GET /reviews/pr/top

List of top-rated reviews.

**Example:**
```bash
curl \"http://127.0.0.1:8080/reviews/pr/top\"
```

## Queues

### GET /queues/needs-review

Repo-wide prioritized queue of PRs needing review (from the latest persisted attention analysis run).

Note: This is no longer filtered by `REVIEW_TARGET_LOGIN`.

**Example:**
```bash
curl \"http://127.0.0.1:8080/queues/needs-review\"
```

**Response:**
```json
[
  {
    \"pr_number\": 123,
    \"title\": \"Add new feature\",
    \"score\": 5.5,
    \"url\": \"https://github.com/apache/polaris/pull/123\"
  }
]
```

### GET /queues/interesting-issues

Prioritized list of interesting issues.

**Example:**
```bash
curl \"http://127.0.0.1:8080/queues/interesting-issues\"
```

## Webhooks

### POST /webhooks/github

GitHub webhook receiver for real-time event processing.

**Supported Events:**
- `pull_request`
- `issues`
- `issue_comment`
- `pull_request_review`

**Configuration:**
Set `GITHUB_WEBHOOK_SECRET` for signature verification.

## Other Endpoints

### GET /

API index with links to all endpoints.

**Example:**
```bash
curl \"http://127.0.0.1:8080/\"
```

### GET /ui

Web dashboard for visualizing PR priorities and reviews.

**Example:**
```bash
open \"http://127.0.0.1:8080/ui\"
```

### GET /docs

Interactive OpenAPI documentation (Swagger UI).

**Example:**
```bash
open \"http://127.0.0.1:8080/docs\"
```

### GET /healthz

Health check endpoint.

**Example:**
```bash
curl \"http://127.0.0.1:8080/healthz\"
```

**Response:**
```json
{\"status\": \"healthy\"}
```

### GET /stats

Service statistics.

**Example:**
```bash
curl \"http://127.0.0.1:8080/stats\"
```

**Response:**
```json
{
  \"prs_open\": 45,
  \"issues_open\": 23,
  \"reviews_completed\": 12,
  \"uptime_seconds\": 3600
}
```

## Job Status Values

Review jobs progress through these states:

- **`queued`** - Job created, waiting for worker
- **`running`** - Worker is processing the job
- **`completed`** - Job finished successfully
- **`failed`** - Job failed (timeout, error, or invalid)

## Error Responses

All endpoints return standard HTTP status codes:

- **200** - Success
- **400** - Bad request (invalid parameters)
- **404** - Resource not found (PR, job, etc.)
- **500** - Internal server error

**Example Error Response:**
```json
{
  \"detail\": \"PR #999 not found\"
}
```
