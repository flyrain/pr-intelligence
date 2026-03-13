from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, Field


class GitHubEvent(BaseModel):
    event_type: str
    action: str | None = None
    received_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    payload: dict


class PullRequestSnapshot(BaseModel):
    number: int
    title: str
    body: str = ""
    state: str
    draft: bool
    author: str
    labels: list[str] = Field(default_factory=list)
    requested_reviewers: list[str] = Field(default_factory=list)
    comments: int = 0
    review_comments: int = 0
    commits: int = 0
    changed_files: int = 0
    additions: int = 0
    deletions: int = 0
    diff_text: str = ""
    html_url: str
    updated_at: datetime


class IssueSnapshot(BaseModel):
    number: int
    title: str
    body: str = ""
    state: str
    author: str
    labels: list[str] = Field(default_factory=list)
    comments: int = 0
    assignees: list[str] = Field(default_factory=list)
    html_url: str
    updated_at: datetime


class PRSummary(BaseModel):
    pr_number: int
    headline: str
    technical_summary: str
    impact_areas: list[str] = Field(default_factory=list)
    risk_level: Literal["low", "medium", "high"]
    suggested_reviewers: list[str] = Field(default_factory=list)


class ReviewSignal(BaseModel):
    pr_number: int
    score: float
    reasons: list[str]
    needs_review: bool
    rule_version: str = "v1"
    evaluated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class IssueSignal(BaseModel):
    issue_number: int
    score: float
    reasons: list[str]
    interesting: bool
    rule_version: str = "v1"
    evaluated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class DailyReport(BaseModel):
    date: str
    markdown: str


class ReportArtifact(BaseModel):
    name: str
    title: str
    markdown: str


class AnalysisItem(BaseModel):
    item_type: Literal["pr", "issue"]
    number: int
    title: str
    url: str
    score: float
    heuristic_reasons: list[str] = Field(default_factory=list)
    catalogs: list[str] = Field(default_factory=list)
    llm_summary: str = ""
    llm_tags: list[str] = Field(default_factory=list)
    llm_provider: str = ""
    llm_model: str = ""
    confidence: float = 0.0
    updated_at: datetime
    analyzed_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    analysis_version: str = "v1"


class AnalysisRun(BaseModel):
    run_id: str = Field(default_factory=lambda: str(uuid4()))
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    source_sync_at: datetime | None = None
    analysis_version: str = "v1"
    top_slice_limit: int = 0
    catalog_counts: dict[str, int] = Field(default_factory=dict)
    artifacts: list[ReportArtifact] = Field(default_factory=list)
    items: list[AnalysisItem] = Field(default_factory=list)


class QueueItem(BaseModel):
    number: int
    title: str
    score: float
    reasons: list[str]
    url: str


class PRSubagentFinding(BaseModel):
    agent_name: str
    focus_area: str
    verdict: Literal["low", "medium", "high"]
    score: float
    summary: str
    recommendations: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    suggested_catalogs: list[str] = Field(default_factory=list)
    confidence: float = 0.6


class PRReviewReport(BaseModel):
    pr_number: int
    generated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    provider: str
    model: str
    findings: list[PRSubagentFinding] = Field(default_factory=list)
    overall_priority: float
    overall_recommendation: str
