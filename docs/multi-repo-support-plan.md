# Multi-Repo Support Plan

## Summary

Keep the product repo-scoped.

- `/ui` should be a simple repo index
- each repo gets its own dashboard and API routes
- do not mix PRs from different repos into one default queue

This is the simplest path and matches how the current app already works.

```mermaid
flowchart TD
    A["/ui"] --> B["apache/polaris"]
    A --> C["my-org/service-a"]
    B --> D["/repos/apache/polaris/ui"]
    C --> E["/repos/my-org/service-a/ui"]
```

## Repo-Scoped Routes

Add these routes:

- `GET /repos`
- `GET /repos/{owner}/{repo}/stats`
- `POST /repos/{owner}/{repo}/refresh`
- `GET /repos/{owner}/{repo}/queues/needs-review`
- `GET /repos/{owner}/{repo}/queues/interesting-issues`
- `GET /repos/{owner}/{repo}/reports/daily/latest.md`
- `POST /repos/{owner}/{repo}/reviews/pr/{pr_number}/run`
- `POST /repos/{owner}/{repo}/reviews/pr/{pr_number}/run-sync`
- `GET /repos/{owner}/{repo}/reviews/pr/{pr_number}/job`
- `GET /repos/{owner}/{repo}/reviews/pr/{pr_number}/latest`
- `GET /repos/{owner}/{repo}/reviews/pr/{pr_number}/latest.md`
- `GET /repos/{owner}/{repo}/reviews/pr/{pr_number}/latest.html`
- `GET /repos/{owner}/{repo}/reviews/pr/top`

```mermaid
flowchart LR
    A["Repo Index"] --> B["Repo Dashboard"]
    B --> C["Stats"]
    B --> D["Queues"]
    B --> E["Report"]
    B --> F["PR Review"]
```

## UI

### `/ui`

Make `/ui` a repo list page. Each repo card should show:

- `owner/repo`
- PR count
- issue count
- needs-review count
- interesting-issues count
- last sync

### `/repos/{owner}/{repo}/ui`

Move the current dashboard here with minimal changes.

Keep the existing sections:

- PRs needing review
- new or updated PRs
- interesting issues
- deep PR reviews
- review jobs

## Data And Storage

Keep this simple in phase 1:

- one app
- one repo runtime per configured repo
- one SQLite file per repo

That avoids PR number collisions like `#123` appearing in multiple repos.

## Config

Keep single-repo env config as-is.

For multi-repo mode, add one config file listing repos and per-repo paths.

Example:

```toml
[[repos]]
owner = "apache"
repo = "polaris"
local_review_repo_dir = "/path/to/apache/polaris"
sqlite_path = ".data/apache__polaris.db"

[[repos]]
owner = "my-org"
repo = "service-a"
local_review_repo_dir = "/path/to/service-a"
sqlite_path = ".data/my-org__service-a.db"
```

## Compatibility

- if only one repo is configured, existing non-repo-scoped routes can keep working
- if multiple repos are configured, repo-scoped routes are the source of truth

## Implementation Steps

1. Add a small repo registry in the app layer.
2. Load multiple repo configs.
3. Create one runtime per repo.
4. Add the repo-scoped routes listed above.
5. Move the current dashboard to `/repos/{owner}/{repo}/ui`.
6. Turn `/ui` into the repo index.
7. Make review jobs repo-aware.
8. Add tests for two repos with overlapping PR numbers.

## Tests

Cover at least:

- two repos both having PR `#123`
- repo-scoped queue routes
- repo-scoped review routes
- repo index rendering
- single-repo compatibility behavior

## Recommendation

Do this first. It is much simpler than building a mixed cross-repo queue and it fits the current architecture.
