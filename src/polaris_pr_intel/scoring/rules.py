from __future__ import annotations

from datetime import datetime, timezone

from polaris_pr_intel.models import IssueSnapshot, PullRequestSnapshot



def score_review_need(pr: PullRequestSnapshot) -> tuple[float, list[str]]:
    score = 0.0
    reasons: list[str] = []

    if pr.state != "open":
        return 0.0, ["not-open"]
    if pr.draft:
        return 0.1, ["draft-pr"]

    age_hours = (datetime.now(timezone.utc) - pr.updated_at).total_seconds() / 3600
    if age_hours > 24:
        score += 1.5
        reasons.append("stale-over-24h")
    if age_hours > 72:
        score += 1.5
        reasons.append("stale-over-72h")

    if pr.requested_reviewers:
        score += 2.0
        reasons.append("reviewers-requested")

    diff_size = pr.additions + pr.deletions
    if diff_size > 800:
        score += 1.5
        reasons.append("large-diff")
    elif diff_size > 250:
        score += 1.0
        reasons.append("medium-diff")

    if pr.changed_files > 20:
        score += 1.0
        reasons.append("many-files")

    if not reasons:
        reasons.append("normal-priority")
    return score, reasons



def score_issue_interest(issue: IssueSnapshot) -> tuple[float, list[str]]:
    score = 0.0
    reasons: list[str] = []
    labels = {l.lower() for l in issue.labels}

    for key, pts in (("bug", 1.5), ("regression", 2.0), ("security", 2.5), ("performance", 1.5)):
        if key in labels:
            score += pts
            reasons.append(f"label:{key}")

    if issue.comments >= 5:
        score += 1.0
        reasons.append("high-discussion")

    if not issue.assignees:
        score += 0.5
        reasons.append("unassigned")

    if issue.state == "open":
        score += 0.5
        reasons.append("open")

    if not reasons:
        reasons.append("low-signal")
    return score, reasons
