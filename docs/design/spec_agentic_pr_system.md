# Spec-Driven Agentic PR System (v1)

## Overview

This system automates software development using agents in a closed loop:

Spec → Code → Review → Fix → Repeat → Merge

The core principle:

The spec is the single source of truth.

---

## Architecture

Spec (input)
  ↓
Coding Agent → creates PR
  ↓
Validation (tests, lint, build)
  ↓
Review Agent (checks against spec)
  ↓
Fix Agent (resolves violations)
  ↓
Loop (max N iterations)
  ↓
Merge or escalate

---

## Spec

Each task must include a spec (JSON + Markdown):

### Required fields

- Requirements (R1, R2…)
- Acceptance Criteria (A1, A2…)
- Design (high-level)
- Constraints
- Non-goals
- Validation rules
- Review rules (e.g., max iterations)

### Example (simplified)

{
  "requirements": [
    { "id": "R1", "description": "Generate PR from issue" }
  ],
  "acceptance_criteria": [
    { "id": "A1", "description": "PR created successfully" }
  ]
}

---

## Agents

### Coding Agent
- Input: spec
- Output: PR (code + tests)

---

### Review Agent
- Input: PR + spec
- Output: structured violations

Example:
{
  "violations": [
    { "id": "R2", "reason": "Missing tests" }
  ],
  "decision": "reject"
}

---

### Fix Agent
- Input: PR + violations
- Output: updated PR

---

### Controller
- Runs loop (max iterations)
- Decides:
  - continue
  - merge
  - escalate

---

## Validation Layer

Non-LLM checks (hard gates):
- unit tests
- lint
- type checks
- build

---

## Loop Rules

- Max iterations: 1 to 3
- Stop when:
  - all acceptance criteria pass
- Escalate when:
  - no convergence
  - conflicting signals

---

## Human Involvement

Required:
- spec creation and approval

Optional:
- final PR review (for critical changes)

---

## Integration with pr-intelligence

### Minimal changes

1. Add spec input:
/specs/{issue_id}.json

2. Replace scoring with:
- spec-based violations

3. Add loop controller

4. Add:
- coding agent
- review agent
- fix agent

---

## Key Principle

Systems without a spec optimize for plausibility  
Systems with a spec optimize for correctness
