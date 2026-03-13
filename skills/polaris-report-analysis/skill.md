---
name: polaris-report-analysis
description: This skill should be used when the task is to analyze many Apache Polaris pull requests or issues after sync, generate attention-oriented reports, rank what needs action now, or assign items into internal catalogs such as needs-review, aging-prs, security-risk, release-risk, interesting-issues, and recently-updated.
---

# Apache Polaris Report Analysis Skill

Use this skill for post-sync triage, report generation, and catalog routing across many PRs/issues in the `apache/polaris` repository.

## When This Skill Applies

Use when the task is not "review this one PR", but instead:
- rank multiple PRs for attention
- decide what belongs in `Review Now`
- build daily/attention reports
- route items into internal catalogs
- summarize which PRs/issues need action now

## Core Triage Rules

### Review Now
- Prioritize PRs that changed recently and are still actively moving
- Prefer PRs explicitly waiting on the target reviewer (`requested-you`) when they are still recent
- Prefer unreviewed PRs over PRs that already show prior review activity
- Draft PRs are lower priority and usually should not appear in `Review Now`

### Aging / Ignore For Now
- If a PR has not changed for a long time, do not put it in `Review Now`
- Long-stale PRs belong in an aging/nudge section instead of the immediate attention queue
- If a PR was already reviewed and has not changed recently, deprioritize it heavily

### What Counts As Prior Review
- Existing review comments
- A previously saved deep review report
- Obvious signs the PR has already received review attention

### Issue Triage
- Show only issues that are actionable or high-signal
- Prefer bugs, regressions, security, or high-discussion issues
- Avoid long flat dumps; keep the issue list short and decision-oriented

## Catalog Guidance

- `needs-review`: ready for human review now
- `aging-prs`: stale PRs that may need a nudge rather than immediate review
- `security-risk`: auth, permissions, secrets, trust boundaries, or security-sensitive behavior
- `release-risk`: broad changes, high regression potential, risky rollout surface
- `interesting-issues`: issues worth triage now
- `recently-updated`: fresh activity that may deserve attention

## Output Style

- Optimize for "what needs my attention now?"
- Keep summaries short, operational, and specific
- Do not repeat the same PR across many sections unless it materially helps prioritization
- Prefer a short ranked list over a long catalog dump
- For each surfaced PR, answer why it needs attention now, not just why it is generally risky
