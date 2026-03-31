from __future__ import annotations

from polaris_pr_intel.agents.issue_insight import IssueInsightAgent
from polaris_pr_intel.agents.review_need import ReviewNeedAgent
from polaris_pr_intel.graphs.daily_report_graph import DailyReportGraph
from polaris_pr_intel.ingest import SnapshotIngestor
from polaris_pr_intel.store.base import Repository


def run_full_refresh(
    *,
    snapshot_ingestor: SnapshotIngestor,
    repo: Repository,
    review_need_agent: ReviewNeedAgent,
    issue_insight_agent: IssueInsightAgent,
    daily_graph: DailyReportGraph,
    per_page: int = 100,
    max_pages: int = 20,
    prune_missing_open_prs: bool = True,
) -> dict:
    synced = snapshot_ingestor.sync_recent(
        per_page=per_page,
        max_pages=max_pages,
        since=None,
        prune_missing_open_prs=prune_missing_open_prs,
    )

    prs_scored = 0
    issues_scored = 0
    needs_review = 0
    interesting_issues = 0

    for pr in repo.prs.values():
        if pr.state != "open":
            continue
        signal = review_need_agent.run(pr)
        repo.save_review_signal(signal)
        prs_scored += 1
        if signal.needs_review:
            needs_review += 1

    for issue in repo.issues.values():
        if issue.state != "open":
            continue
        signal = issue_insight_agent.run(issue)
        repo.save_issue_signal(signal)
        issues_scored += 1
        if signal.interesting:
            interesting_issues += 1

    out = daily_graph.invoke()
    analysis_run = out.get("analysis_run")

    return {
        "ok": True,
        "synced": synced,
        "scored": {
            "prs": prs_scored,
            "issues": issues_scored,
            "needs_review": needs_review,
            "interesting_issues": interesting_issues,
        },
        "analysis_run": analysis_run,
        "notifications": out.get("notifications", []),
        "report_markdown": out.get("report_markdown"),
    }
