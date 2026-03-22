from __future__ import annotations

from typing import Any

from langgraph.graph import END, StateGraph

from polaris_pr_intel.agents.issue_insight import IssueInsightAgent
from polaris_pr_intel.agents.pr_summarizer import PRSummarizerAgent
from polaris_pr_intel.agents.review_need import ReviewNeedAgent
from polaris_pr_intel.config import Settings
from polaris_pr_intel.graphs.state import PRIntelState
from polaris_pr_intel.models import GitHubEvent, IssueSnapshot, PullRequestSnapshot
from polaris_pr_intel.store.base import Repository


class EventGraph:
    def __init__(self, repo: Repository, settings: Settings) -> None:
        self.repo = repo
        self.pr_summarizer = PRSummarizerAgent()
        self.review_need = ReviewNeedAgent(settings)
        self.issue_insight = IssueInsightAgent(settings)
        self.graph = self._build()

    def _build(self):
        g = StateGraph(PRIntelState)
        g.add_node("ingest_event", self.ingest_event)
        g.add_node("summarize_pr", self.summarize_pr)
        g.add_node("score_review_need", self.score_review_need)
        g.add_node("score_issue", self.score_issue)

        g.set_entry_point("ingest_event")
        g.add_conditional_edges("ingest_event", self.route_after_ingest, {
            "pr": "summarize_pr",
            "issue": "score_issue",
            "done": END,
        })
        g.add_edge("summarize_pr", "score_review_need")
        g.add_edge("score_review_need", END)
        g.add_edge("score_issue", END)
        return g.compile()

    def ingest_event(self, state: PRIntelState) -> dict[str, Any]:
        event = state["event"]
        payload = event.payload
        notifications: list[str] = []

        if event.event_type in {"pull_request", "pull_request_review"} and payload.get("pull_request"):
            pr = PullRequestSnapshot.model_validate(
                {
                    "number": payload["pull_request"]["number"],
                    "title": payload["pull_request"].get("title", ""),
                    "body": payload["pull_request"].get("body") or "",
                    "state": payload["pull_request"].get("state", "open"),
                    "draft": bool(payload["pull_request"].get("draft", False)),
                    "author": (payload["pull_request"].get("user") or {}).get("login", "unknown"),
                    "labels": [l["name"] for l in payload["pull_request"].get("labels", [])],
                    "requested_reviewers": [u["login"] for u in payload["pull_request"].get("requested_reviewers", [])],
                    "comments": payload["pull_request"].get("comments", 0),
                    "review_comments": payload["pull_request"].get("review_comments", 0),
                    "commits": payload["pull_request"].get("commits", 0),
                    "changed_files": payload["pull_request"].get("changed_files", 0),
                    "additions": payload["pull_request"].get("additions", 0),
                    "deletions": payload["pull_request"].get("deletions", 0),
                    "activity_comments_24h": 0,
                    "activity_comments_7d": 0,
                    "activity_reviews_24h": 0,
                    "activity_reviews_7d": 0,
                    "html_url": payload["pull_request"].get("html_url", ""),
                    "updated_at": payload["pull_request"]["updated_at"],
                }
            )
            self.repo.upsert_pr(pr)
            notifications.append(f"ingested-pr:{pr.number}")
            return {"pr": pr, "notifications": notifications}

        if event.event_type in {"issues", "issue_comment"} and payload.get("issue"):
            if payload["issue"].get("pull_request"):
                return {"notifications": ["ignored-pr-comment-event"]}
            issue = IssueSnapshot.model_validate(
                {
                    "number": payload["issue"]["number"],
                    "title": payload["issue"].get("title", ""),
                    "body": payload["issue"].get("body") or "",
                    "state": payload["issue"].get("state", "open"),
                    "author": (payload["issue"].get("user") or {}).get("login", "unknown"),
                    "labels": [l["name"] for l in payload["issue"].get("labels", [])],
                    "comments": payload["issue"].get("comments", 0),
                    "assignees": [a["login"] for a in payload["issue"].get("assignees", [])],
                    "html_url": payload["issue"].get("html_url", ""),
                    "updated_at": payload["issue"]["updated_at"],
                }
            )
            self.repo.upsert_issue(issue)
            notifications.append(f"ingested-issue:{issue.number}")
            return {"issue": issue, "notifications": notifications}

        return {"notifications": ["ignored-event"]}

    @staticmethod
    def route_after_ingest(state: PRIntelState) -> str:
        if state.get("pr"):
            return "pr"
        if state.get("issue"):
            return "issue"
        return "done"

    def summarize_pr(self, state: PRIntelState) -> dict[str, Any]:
        pr = state["pr"]
        summary = self.pr_summarizer.run(pr)
        self.repo.save_pr_summary(summary)
        return {"pr_summary": summary}

    def score_review_need(self, state: PRIntelState) -> dict[str, Any]:
        pr = state["pr"]
        signal = self.review_need.run(pr)
        self.repo.save_review_signal(signal)
        return {"review_signal": signal}

    def score_issue(self, state: PRIntelState) -> dict[str, Any]:
        issue = state["issue"]
        signal = self.issue_insight.run(issue)
        self.repo.save_issue_signal(signal)
        return {"issue_signal": signal}

    def invoke(self, event: GitHubEvent) -> PRIntelState:
        return self.graph.invoke({"event": event})
