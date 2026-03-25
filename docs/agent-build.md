# Agent Build

This repo should be understood as a workflow-based agent system, not a toy single loop.

Useful mental model:

`input -> decision -> action -> persisted state`

In this codebase, that loop is split across a few bounded workflows instead of one open-ended agent.

## Core Shape

### Inputs

The system is driven by:

- GitHub sync and webhook ingestion
- API requests
- scheduled refresh jobs

Relevant code:

- `src/polaris_pr_intel/ingest.py`
- `src/polaris_pr_intel/api/app.py`
- `src/polaris_pr_intel/scheduler/daily.py`

### Orchestration

The main control loops live in LangGraph workflows:

- `EventGraph`: ingest event -> summarize or score -> persist
- `DailyReportGraph`: analyze repo state -> render report -> publish
- `PRReviewGraph`: load PR -> review -> aggregate -> persist

Relevant code:

- `src/polaris_pr_intel/graphs/event_graph.py`
- `src/polaris_pr_intel/graphs/daily_report_graph.py`
- `src/polaris_pr_intel/graphs/pr_review_graph.py`

These graphs should stay responsible for workflow execution, not for deciding how work gets triggered.

### Decision Layer

LLMs are used for judgment-heavy steps, not for owning system state.

Examples:

- batched PR attention ranking
- comprehensive PR review
- optional self-review flow

Relevant code:

- `src/polaris_pr_intel/llm/base.py`
- `src/polaris_pr_intel/llm/adapters.py`
- `src/polaris_pr_intel/llm/factory.py`
- `src/polaris_pr_intel/agents/pr_reviewer.py`
- `src/polaris_pr_intel/agents/derived_analysis.py`

### State

The repository is the source of truth. Important outputs are persisted:

- PR and issue snapshots
- rule-based signals
- PR review reports
- analysis runs

Relevant code:

- `src/polaris_pr_intel/models.py`
- `src/polaris_pr_intel/store/base.py`
- `src/polaris_pr_intel/store/sqlite_repository.py`
- `src/polaris_pr_intel/store/repository.py`

## Triggering vs Orchestration

This repo should keep agent triggering separate from agent orchestration.

Triggering is about how work starts:

- API request
- webhook event
- scheduler tick
- CLI command
- future chatbot request

Orchestration is about how work runs once started:

- step ordering
- routing
- retries or fallbacks
- aggregation
- persistence

For this codebase, the intended split is:

- trigger layer decides `run now`, `enqueue`, `deduplicate`, or `ignore`
- graph layer executes the workflow
- repository stores the result

This matters because the same workflows should be reusable from multiple front doors. A chatbot should trigger the same backend flows as the API, not introduce a parallel execution path.

## What To Keep In Mind

The simplified idea `agent = loop + tools` is useful, but incomplete for this repo.

This system is really:

- bounded loops inside each workflow
- shared persisted state across workflows
- deterministic rules mixed with LLM judgment
- API and scheduler driven execution

Examples:

- refresh: sync -> score -> analyze -> report
- review: fetch PR if needed -> review -> aggregate -> persist
- webhook: ingest event -> route by type -> score or summarize

## Chatbot / ChatOps

A chatbot fits well here, but only as a control surface.

Recommended shape:

- chatbot = conversational front door
- trigger layer = request handling and job start policy
- existing graphs = execution layer
- repository = source of truth

Good chatbot responsibilities:

- answer status questions from persisted state
- explain why a PR is ranked highly
- summarize the latest report or review
- trigger refresh or review jobs through existing APIs
- propose actions before executing them

The chatbot should call the same backend operations that the UI and CLI use. It should not keep private workflow state or duplicate backend business logic.

## Design Rules

- Keep LLMs responsible for judgment, not persistence.
- Keep triggering separate from orchestration.
- Keep orchestration in graphs or explicit workflow code.
- Keep repository state as the canonical record.
- Prefer bounded steps over open-ended conversations.
- Make fallback behavior visible.
- Add new user-facing surfaces by calling existing backend flows first.

## Bottom Line

The point of the simplified agent framing is clarity, not completeness.

For this repo, the important pieces are:

- clear workflow boundaries
- durable state
- validated model outputs
- observable execution
- tool-backed actions

That is what makes the system operational instead of just conversational.
