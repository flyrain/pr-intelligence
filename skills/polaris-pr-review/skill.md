---
name: polaris-pr-review
description: This skill should be used when the user asks to "review a PR", "review this PR", "check my PR", "review pull request", "look at PR #<number>", or any similar request to analyze a GitHub pull request in the Apache Polaris project. Provides structured PR review covering code correctness, test coverage, authorization/security patterns, Iceberg spec compliance, and Apache project conventions.
---

# Apache Polaris PR Review Skill

Perform a structured code review of a pull request in the Apache Polaris repository.

## When This Skill Applies

Use when the user wants to review a PR — their own or someone else's — in the `apache/polaris` GitHub repo.

## Review Workflow

### Step 1: Identify the PR

- If the user provides a PR number, fetch it: `gh pr view <number> --repo apache/polaris`
- If on a feature branch, check current branch's PR: `gh pr view`
- Get the diff: `gh pr diff <number> --repo apache/polaris` or `git diff main...HEAD`
- List changed files: `gh pr diff <number> --name-only --repo apache/polaris`

### Step 2: Understand the Change

Read the PR title and description for:
- The stated purpose/rationale
- What issues/tickets it references (`Fixes #...`)
- Whether the description is clear to someone with no prior context

### Step 3: Review Changed Files

Read relevant changed files from the diff. Focus on:
- Core logic in `service/`, `api/`, `persistence/`, `catalog/`, `quarkus/`
- Tests in `*Test.java` / `*IT.java` files
- API specs in `spec/` or OpenAPI yaml files

### Step 4: Apply These Review Checks

#### Authorization & Security (highest priority)
- All catalog operations must check `PolarisAuthorizableOperation` before executing
- `authorizeAndValidate()` must be called before any data access
- Privilege escalation: ensure callers cannot grant themselves more than they have
- `TABLE_FULL_METADATA` vs `TABLE_READ_DATA` vs `TABLE_WRITE_DATA` scoping — verify the right privilege is used
- `overwrite` paths must require full metadata privilege, not just read
- Check for missing `null` guards on principal/role lookups

#### Test Coverage
- New behavior must have unit tests; new end-to-end flows need integration tests
- Look for tests in `*Test.java` (unit) and `*IT.java` (integration/reg)
- Verify edge cases: overwrite=true/false, missing entity, insufficient privilege
- Check for `@QuarkusTest` / `@TestProfile` annotations for integration tests
- Disabled tests (`@Disabled`, `[temp] Disable`) must have a tracked issue or be re-enabled

#### Apache Iceberg Spec Compliance
- `RegisterTable` operations must follow the Iceberg REST spec
- Metadata location validation: verify it's within allowed storage paths
- UUID handling: new registrations should get a new table UUID, not preserve old ones unless explicitly re-registering
- `format-version` must be respected (1 vs 2 differences)

#### Code Quality
- No raw SQL or hardcoded catalog/namespace paths
- Exception messages should include enough context (entity name, namespace, catalog)
- Use project exception types (`AlreadyExistsException`, `NotFoundException`, `ForbiddenException`) not raw `RuntimeException`
- Methods should not silently swallow exceptions — always log or rethrow
- Avoid mutable static state
- **No log-then-throw**: Flag `LOG.error(msg); throw new Exception(msg)` as redundant — put the message in the exception and let the caller/framework handle logging
- **Consolidate debug logging**: Multiple sequential `LOG.debug()` calls should be a single log statement — split messages interleave with concurrent logs and are harder to read
- **No duplicated logic across implementations**: If the same logic (e.g., credential checks, validation) appears in multiple implementations of an interface, it should be extracted to a shared utility class
- **Maintainability over exhaustive enumeration**: Large switch statements that enumerate every enum value are a maintenance burden — prefer EnumSet declarations, annotations, or type markers that don't require per-value updates when enums change
- **Question unnecessary abstraction layers**: If an external integration can map directly from the high-level operation type, don't force it through an internal intermediate representation. Keep external integrations decoupled from internal models

#### Apache Project Conventions
- ASF license header in every new file
- PR title should follow Conventional Commits (`feat:`, `fix:`, `refactor:`, `test:`, `docs:`)
- `Fixes #<number>` at end of PR description if closing an issue
- No `TODO` without a corresponding tracked issue
- No commented-out code left in

#### Build & Format
- Remind the user to run `./gradlew format compileAll` locally before merge
- Check if the PR touches API surface — if so, OpenAPI spec update may be needed
- If `gradle.properties` or `bom/` changed, verify version bumps are intentional

### Step 5: Structure Your Output

```
## PR #<number>: <title>

### Summary
<2-3 sentences describing what this PR does>

### Critical Issues (must fix before merge)
- [AuthZ] <description> — `file:line`
- [Correctness] <description> — `file:line`

### Important Issues (should fix)
- [Tests] <description>
- [Spec] <description>

### Suggestions (nice to have)
- <description>

### Strengths
- <what's done well>

### Checklist
- [ ] ASF headers present on new files
- [ ] Tests added for new behavior
- [ ] Authorization checks correct
- [ ] PR description explains rationale
- [ ] Conventional Commits title
- [ ] Linked issue (if applicable)

### Verdict
APPROVE / REQUEST CHANGES / COMMENT — <one-sentence summary>
```

## Review Style

- Write like a teammate in a PR comment, not a formal report. Short, casual, plain English.
- Keep summaries to 1-2 sentences max. No filler words, no hedging, no restating context the reader already has.
- Recommendations should be punchy and specific — "add a test for X" not "consider adding comprehensive test coverage to verify the correctness of X across all scenarios"
- Say "same issue" for repeated patterns, don't re-explain
- Phrase things as questions when unsure ("does this handle the null case?"), statements when sure ("missing null guard on line 42")
- Skip speculative or theoretical concerns — only flag things grounded in the actual diff
- Prioritize maintainability over exhaustive security analysis of hypothetical scenarios

## Automated Review Report Format

When generating markdown reports for automated PR reviews (via the review queue system), structure the output as follows:

### Header Section
- **Title:** `# PR #{number}: {title}`
- **Metadata line 1:** Author, State, Draft status
- **Metadata line 2:** Labels (comma-separated), Requested reviewers (comma-separated)
- **Metadata line 3:** Last updated timestamp, Diff statistics (files/additions/deletions)
- **Metadata line 4:** Direct GitHub URL link

### Review Analysis Section
- Overall priority score (0-10 scale)
- Overall recommendation (text summary)
- Provider and model information
- Generation timestamp

### Findings Section
- Each finding from a subagent as a numbered section
- Include: agent name, focus area as section title
- Verdict (LOW/MEDIUM/HIGH), Score, Confidence in bold
- Summary subsection with detailed analysis
- Recommendations subsection as bullet list (if any)
- Tags at the end (if any)
- Horizontal rule separator between findings

### Formatting Guidelines
- Use bold (`**text**`) for field labels
- Use horizontal rules (`---`) to separate major sections
- Use proper heading hierarchy: `#` for title, `##` for major sections, `###` for findings, `####` for subsections
- Keep metadata compact (multiple fields per line with `|` separator)
- Make labels, reviewers, and tags comma-separated lists
- Format timestamps as YYYY-MM-DD HH:MM:SS UTC
- Show diff stats as: `N files, +additions/-deletions lines`

## Tips

- Focus on the diff, not the whole codebase
- For authorization bugs, trace the call from the REST handler down through the service layer
- Reference `references/auth-patterns.md` for authorization decision patterns in this codebase
- If unsure about the Iceberg spec behavior, note it as a question for the PR author rather than a blocker
- When multiple places have the same issue, call it out once with detail, then reference it briefly elsewhere
