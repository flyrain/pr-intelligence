---
name: polaris-pr-review
description: Structured PR review for Apache Polaris covering code correctness, test coverage, authorization/security patterns, Iceberg spec compliance, and Apache project conventions.
---

# Apache Polaris PR Review Skill

## Review Aspects

Analyze PRs across these four dimensions. Each aspect should produce specific, actionable findings.

### 1. code-risk: Code Risk and Complexity

**Focus areas:**
- Architectural changes and refactoring scope
- Code complexity and maintainability
- Breaking changes or API surface modifications
- Build and dependency impacts

**Critical checks:**
- No raw SQL or hardcoded catalog/namespace paths
- Exception messages include context (entity name, namespace, catalog)
- Use project exception types (`AlreadyExistsException`, `NotFoundException`, `ForbiddenException`) not raw `RuntimeException`
- No silently swallowed exceptions — always log or rethrow
- Avoid mutable static state
- **No log-then-throw**: `LOG.error(msg); throw new Exception(msg)` is redundant — put message in exception, let caller/framework log
- **Consolidate logging**: Multiple sequential `LOG.debug()` → single statement (split messages interleave with concurrent logs)
- **No duplicated logic**: Extract repeated logic (credential checks, validation) to shared utilities
- **Avoid exhaustive enum switches**: Prefer EnumSet declarations or type markers that don't require per-value updates
- **Question unnecessary abstractions**: Don't force external integrations through internal intermediate representations

### 2. test-impact: Test Impact and Coverage

**Focus areas:**
- Unit test coverage for new code
- Integration test needs for new flows
- Edge case testing (null handling, error cases, boundary conditions)
- Test quality and effectiveness

**Critical checks:**
- New behavior must have unit tests (`*Test.java`)
- New end-to-end flows need integration tests (`*IT.java` with `@QuarkusTest` / `@TestProfile`)
- Verify edge cases: overwrite=true/false, missing entity, insufficient privilege
- Disabled tests (`@Disabled`, `[temp] Disable`) must have tracked issue or be re-enabled

### 3. docs-quality: Documentation and Release Notes

**Focus areas:**
- CHANGELOG.md updates
- Inline documentation (javadoc, comments)
- API specification changes (OpenAPI)
- README or user-facing documentation
- PR description clarity

**Critical checks:**
- ASF license header in every new file
- PR title follows Conventional Commits (`feat:`, `fix:`, `refactor:`, `test:`, `docs:`)
- `Fixes #<number>` at end of PR description if closing an issue
- No `TODO` without corresponding tracked issue
- No commented-out code
- OpenAPI spec updated if API surface changed
- Version bumps in `gradle.properties` or `bom/` are intentional

### 4. security-signal: Security and Permission Model

**Focus areas:**
- Authorization and authentication changes
- Privilege escalation risks
- Security vulnerabilities (injection, XSS, etc.)
- Secrets and credential handling
- Trust boundaries and validation

**Critical checks (highest priority):**
- All catalog operations check `PolarisAuthorizableOperation` before executing
- `authorizeAndValidate()` called before any data access
- Privilege escalation: callers cannot grant themselves more than they have

## Review Style

- **Tone**: Teammate in PR comment, not formal report. Short, casual, plain English
- **Brevity**: 1-2 sentences max. No filler, no hedging, no restating context
- **Specificity**: "add test for X" not "consider adding comprehensive test coverage to verify correctness of X across all scenarios"
- **Efficiency**: Say "same issue" for repeated patterns, don't re-explain
- **Uncertainty**: Questions when unsure ("does this handle null?"), statements when sure ("missing null guard line 42")
- **Grounded**: Skip speculative concerns — only flag things in the actual diff
- **Pragmatic**: Prioritize maintainability over exhaustive security analysis of hypothetical scenarios

## Automated Review Output Format

### Verdict Levels
- **low** (0.0-0.3): Minor issues, safe to merge with optional improvements
- **medium** (0.3-0.7): Notable concerns that should be addressed, not blocking
- **high** (0.7-1.0): Critical issues that must be fixed before merge

### Output Fields
Return one finding per aspect as JSON:
- **agent_name**: Aspect identifier (code-risk, test-impact, docs-quality, security-signal)
- **focus_area**: Aspect description
- **verdict**: low, medium, or high
- **score**: 0.0-1.0 numeric risk level
- **confidence**: 0.0-1.0 assessment confidence (lower when context limited or behavior ambiguous)
- **summary**: 1-3 short sentences, plain English, no jargon
- **recommendations**: Array of actionable items with file:line references (e.g. "add null guard in Foo.java:42")
- **tags**: Short categorization tags
- **suggested_catalogs**: Array from: `needs-review`, `aging-prs`, `security-risk`, `release-risk`, `recently-updated`

### Catalog Guidance
- **needs-review**: Ready for human review, prioritize
- **aging-prs**: Stale, needs nudge
- **security-risk**: Auth, permissions, secrets, trust boundaries
- **release-risk**: Broad changes, regression potential, rollout concerns
- **recently-updated**: Fresh activity deserving attention

## Markdown Report Format

For automated review reports, structure markdown as:

**Header:**
```markdown
# PR #{number}: {title}

**Author:** @{author} | **State:** {state} | **Draft:** {draft}
**Labels:** {labels} | **Reviewers:** {reviewers}
**Updated:** {timestamp UTC} | **Stats:** {N} files, +{adds}/-{dels} lines
**GitHub:** {url}
```

**Review Analysis:**
```markdown
## Review Analysis

**Overall Priority:** {score 0.0-1.0}
**Recommendation:** {text}
**Provider:** {provider} ({model})
**Generated:** {timestamp UTC}
```

**Findings:**
```markdown
## Findings

### 1. {agent_name}: {focus_area}

**Verdict:** {LOW|MEDIUM|HIGH} | **Score:** {0.00} | **Confidence:** {0.00}

#### Summary
{text}

#### Recommendations
- {item}

**Tags:** {tag1, tag2}

---
```

## Tips

- Focus on the diff, not the whole codebase
- For authorization bugs, trace from REST handler through service layer
- Reference `references/auth-patterns.md` for authorization patterns
- Note Iceberg spec questions for PR author rather than blocking
- Call out repeated issues once with detail, reference briefly elsewhere
