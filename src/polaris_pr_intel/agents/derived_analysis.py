from __future__ import annotations

from datetime import datetime, timezone

from polaris_pr_intel.config import Settings
from polaris_pr_intel.llm.adapters import HeuristicLLMAdapter
from polaris_pr_intel.llm.base import LLMAdapter
from polaris_pr_intel.models import (
    AnalysisItem,
    AnalysisRun,
    DailyReport,
    PRAttentionContext,
    PRAttentionDecision,
    PullRequestSnapshot,
    ReportArtifact,
)
from polaris_pr_intel.store.base import Repository


class DerivedAnalysisAgent:
    recent_attention_hours = 72.0

    def __init__(self, repo: Repository, llm: LLMAdapter, settings: Settings) -> None:
        self.repo = repo
        self.llm = llm
        self.settings = settings

    def run(self) -> tuple[AnalysisRun, DailyReport]:
        contexts = self._build_attention_contexts()
        decisions = self._build_attention_decisions(contexts)
        items = self._build_items(contexts, decisions)
        catalog_counts = self._catalog_counts(items)
        artifacts = self._build_artifacts(items, catalog_counts)
        run = AnalysisRun(
            source_sync_at=self.repo.last_sync_at,
            analysis_version="v2",
            catalog_counts=catalog_counts,
            artifacts=artifacts,
            items=items,
            attention_contexts=contexts,
            attention_decisions=decisions,
        )
        legacy_report = DailyReport(
            date=datetime.now(timezone.utc).strftime("%Y-%m-%d"),
            markdown=self._legacy_markdown(run),
        )
        return run, legacy_report

    def _build_attention_contexts(self) -> list[PRAttentionContext]:
        now_dt = datetime.now(timezone.utc)
        contexts: list[PRAttentionContext] = []
        for pr in sorted(self.repo.prs.values(), key=lambda item: item.updated_at, reverse=True):
            if pr.state != "open":
                continue
            signal = self.repo.review_signals.get(pr.number)
            age_hours = (now_dt - pr.updated_at).total_seconds() / 3600
            total_discussion = pr.comments + pr.review_comments
            contexts.append(
                PRAttentionContext(
                    pr_number=pr.number,
                    title=pr.title,
                    body=pr.body,
                    html_url=pr.html_url,
                    author=pr.author,
                    state=pr.state,
                    draft=pr.draft,
                    labels=pr.labels,
                    requested_reviewers=pr.requested_reviewers,
                    updated_at=pr.updated_at,
                    age_hours=age_hours,
                    inactive_days=age_hours / 24,
                    comments_total=pr.comments,
                    review_comments_total=pr.review_comments,
                    comments_24h=pr.activity_comments_24h,
                    comments_7d=max(pr.activity_comments_24h, min(total_discussion, 50)),
                    reviews_24h=1 if pr.activity_comments_24h > 0 and pr.review_comments > 0 else 0,
                    reviews_7d=min(pr.review_comments, 20),
                    commits=pr.commits,
                    changed_files=pr.changed_files,
                    additions=pr.additions,
                    deletions=pr.deletions,
                    diff_size=pr.additions + pr.deletions,
                    has_prior_review_activity=(pr.review_comments > 0),
                    has_prior_deep_review=self.repo.latest_pr_review_report(pr.number) is not None,
                    rule_reasons=list(signal.reasons) if signal is not None else [],
                )
            )
        return contexts

    def _build_attention_decisions(self, contexts: list[PRAttentionContext]) -> list[PRAttentionDecision]:
        raw = self.llm.analyze_attention_batch(contexts) if contexts else {}
        if len(raw) < len(contexts):
            fallback = HeuristicLLMAdapter().analyze_attention_batch(contexts)
            for ctx in contexts:
                raw.setdefault(ctx.pr_number, fallback[ctx.pr_number])
        decisions = [raw[ctx.pr_number] for ctx in contexts]
        decisions.sort(key=lambda item: item.priority_score, reverse=True)
        return decisions

    def _build_items(self, contexts: list[PRAttentionContext], decisions: list[PRAttentionDecision]) -> list[AnalysisItem]:
        items: list[AnalysisItem] = []
        contexts_by_number = {ctx.pr_number: ctx for ctx in contexts}
        local_now = datetime.now().astimezone()
        local_tz = local_now.tzinfo
        local_today = local_now.date()

        def _is_updated_today_local(updated_at: datetime) -> bool:
            dt = updated_at if updated_at.tzinfo else updated_at.replace(tzinfo=timezone.utc)
            if local_tz is None:
                return dt.date() == local_today
            return dt.astimezone(local_tz).date() == local_today

        for decision in decisions:
            ctx = contexts_by_number[decision.pr_number]
            pr = self.repo.prs.get(decision.pr_number)
            if pr is None:
                continue
            catalogs = self._decision_catalogs(ctx, decision, _is_updated_today_local(pr.updated_at))
            items.append(
                AnalysisItem(
                    item_type="pr",
                    number=pr.number,
                    title=pr.title,
                    url=pr.html_url,
                    score=decision.priority_score,
                    heuristic_reasons=ctx.rule_reasons or [decision.priority_band],
                    catalogs=catalogs,
                    llm_summary=decision.priority_reason,
                    llm_tags=decision.tags,
                    llm_provider=self.llm.provider,
                    llm_model=self.llm.model,
                    confidence=decision.confidence,
                    updated_at=pr.updated_at,
                    analysis_version="v2",
                )
            )

        for signal in sorted(self.repo.issue_signals.values(), key=lambda s: s.score, reverse=True):
            issue = self.repo.issues.get(signal.issue_number)
            if not issue or issue.state != "open":
                continue
            catalogs = self._base_issue_catalogs(issue.labels, signal.score, signal.reasons)
            items.append(
                AnalysisItem(
                    item_type="issue",
                    number=issue.number,
                    title=issue.title,
                    url=issue.html_url,
                    score=signal.score,
                    heuristic_reasons=signal.reasons,
                    catalogs=catalogs,
                    updated_at=issue.updated_at,
                    analysis_version="v2",
                )
            )

        items.sort(key=lambda item: (item.score, item.updated_at.timestamp()), reverse=True)
        return items

    def _decision_catalogs(
        self,
        ctx: PRAttentionContext,
        decision: PRAttentionDecision,
        updated_today: bool,
    ) -> list[str]:
        catalogs = list(decision.suggested_catalogs)
        if decision.needs_review:
            catalogs.append("needs-review")
        if updated_today or ctx.comments_24h > 0:
            catalogs.append("recently-updated")
        if ctx.inactive_days >= 3:
            catalogs.append("aging-prs")
        text = f"{ctx.title}\n{ctx.body}".lower()
        labels = {label.lower() for label in ctx.labels}
        if "security" in text or "permission" in text or "security" in labels:
            catalogs.append("security-risk")
        if ctx.diff_size >= 500 or ctx.changed_files >= 20:
            catalogs.append("release-risk")
        return list(dict.fromkeys(catalog for catalog in catalogs if catalog))

    def _base_issue_catalogs(self, labels: list[str], score: float, reasons: list[str]) -> list[str]:
        catalogs: list[str] = []
        labels_lower = {label.lower() for label in labels}
        if score >= self.settings.issue_interesting_threshold:
            catalogs.append("interesting-issues")
        if "security" in labels_lower or any(reason == "label:security" for reason in reasons):
            catalogs.append("security-risk")
        if {"regression", "bug", "release-blocker"} & labels_lower:
            catalogs.append("release-risk")
        return catalogs

    def _catalog_counts(self, items: list[AnalysisItem]) -> dict[str, int]:
        counts = {
            "needs-review": 0,
            "aging-prs": 0,
            "security-risk": 0,
            "release-risk": 0,
            "interesting-issues": 0,
            "recently-updated": 0,
        }
        for item in items:
            for catalog in item.catalogs:
                counts[catalog] = counts.get(catalog, 0) + 1
        return counts

    def _build_artifacts(self, items: list[AnalysisItem], catalog_counts: dict[str, int]) -> list[ReportArtifact]:
        return [
            ReportArtifact(name="reviewer-queue", title="Reviewer Queue Report", markdown=self._reviewer_queue_markdown(items)),
            ReportArtifact(name="issue-risk-digest", title="Issue and Risk Digest", markdown=self._issue_risk_digest_markdown(items)),
            ReportArtifact(name="executive-summary", title="Executive Summary", markdown=self._executive_summary_markdown(items, catalog_counts)),
            ReportArtifact(name="catalog-summary", title="Catalog Summary", markdown=self._catalog_summary_markdown(items, catalog_counts)),
        ]

    def _reviewer_queue_markdown(self, items: list[AnalysisItem]) -> str:
        lines = ["# Reviewer Queue Report", "", "## Review Now"]
        review_items = [item for item in items if item.item_type == "pr" and "needs-review" in item.catalogs]
        focus_items = self._review_now_items(review_items)[:6]
        if not focus_items:
            lines.append("- No PRs currently require review.")
        else:
            for item in focus_items:
                lines.append(self._format_attention_pr(item))
        aging_watch = [item for item in review_items if self._should_nudge(item) and item not in focus_items][:3]
        if aging_watch:
            lines += ["", "## Aging PRs To Nudge"]
            for item in aging_watch:
                lines.append(f"- [#{item.number}]({item.url}) {item.title} | stale and still waiting")
        recent_updates = [item for item in items if item.item_type == "pr" and "recently-updated" in item.catalogs and item not in focus_items][:3]
        if recent_updates:
            lines += ["", "## Recently Updated PRs"]
            for item in recent_updates:
                lines.append(f"- [#{item.number}]({item.url}) {item.title}")
        return "\n".join(lines)

    def _issue_risk_digest_markdown(self, items: list[AnalysisItem]) -> str:
        lines = ["# Issue and Risk Digest", "", "## Issues Worth Triage"]
        issue_items = [item for item in items if item.item_type == "issue" and "interesting-issues" in item.catalogs][:6]
        if not issue_items:
            lines.append("- No high-signal issues identified.")
        else:
            for item in issue_items:
                lines.append(f"- [#{item.number}]({item.url}) {item.title} | {self._compact_reason(item)}")
        risky_prs = [item for item in items if item.item_type == "pr" and {"security-risk", "release-risk"} & set(item.catalogs)][:4]
        if risky_prs:
            lines += ["", "## Risk Watchlist"]
            for item in risky_prs:
                lines.append(f"- [#{item.number}]({item.url}) {item.title} | {self._compact_reason(item)}")
        return "\n".join(lines)

    def _executive_summary_markdown(self, items: list[AnalysisItem], catalog_counts: dict[str, int]) -> str:
        requested_you = sum(
            1
            for item in items
            if "needs-review" in item.catalogs and "requested-you" in item.heuristic_reasons
        )
        return "\n".join(
            [
                "# Executive Summary",
                "",
                "## What Needs Attention",
                f"- {requested_you} PRs are explicitly waiting on you.",
                f"- {catalog_counts.get('needs-review', 0)} PRs need review overall, ranked by attention priority.",
                f"- {catalog_counts.get('interesting-issues', 0)} issues look worth triage.",
                f"- {catalog_counts.get('security-risk', 0)} PRs/issues touch security-sensitive areas.",
            ]
        )

    def _catalog_summary_markdown(self, items: list[AnalysisItem], catalog_counts: dict[str, int]) -> str:
        lines = ["# Catalog Summary", ""]
        for catalog, count in catalog_counts.items():
            lines.append(f"- {catalog}: {count}")
        return "\n".join(lines).strip()

    def _legacy_markdown(self, run: AnalysisRun) -> str:
        artifact_map = {artifact.name: artifact for artifact in run.artifacts}
        parts = ["# Polaris PR Attention Report"]
        for name in ("executive-summary", "reviewer-queue", "issue-risk-digest"):
            artifact = artifact_map.get(name)
            if artifact:
                parts.extend(["", artifact.markdown])
        return "\n".join(parts)

    def _compact_reason(self, item: AnalysisItem) -> str:
        pr = self.repo.prs.get(item.number) if item.item_type == "pr" else None
        if pr is not None and pr.draft:
            return "draft PR, lower priority"
        if item.llm_summary:
            return item.llm_summary
        if "requested-you" in item.heuristic_reasons:
            return "explicitly requested from you"
        if self._has_prior_review_signal(item):
            return "already reviewed before, only revisit if changed"
        if "security-risk" in item.catalogs:
            return "security-sensitive change"
        if "release-risk" in item.catalogs:
            return "broad change with regression risk"
        if "aging-prs" in item.catalogs:
            return "stale and still open"
        if item.heuristic_reasons:
            return item.heuristic_reasons[0].replace("-", " ")
        return "worth attention"

    def _format_attention_pr(self, item: AnalysisItem) -> str:
        why = self._compact_reason(item)
        trend_note = self._activity_trend_note(item)
        return f"- [#{item.number}]({item.url}) {item.title} | why: {why}{trend_note}"

    def _review_now_items(self, review_items: list[AnalysisItem]) -> list[AnalysisItem]:
        candidates = [item for item in review_items if self._should_show_in_review_now(item)]
        candidates.sort(
            key=lambda item: (
                "requested-you" not in item.heuristic_reasons,
                self._has_prior_review_signal(item),
                self._is_draft(item),
                -item.score,
                -item.updated_at.timestamp(),
            )
        )
        return candidates

    def _should_show_in_review_now(self, item: AnalysisItem) -> bool:
        if self._is_draft(item):
            return False
        if not self._is_recently_changed(item):
            return False
        return True

    def _should_nudge(self, item: AnalysisItem) -> bool:
        return "aging-prs" in item.catalogs or (not self._is_recently_changed(item))

    def _is_recently_changed(self, item: AnalysisItem) -> bool:
        age_hours = (datetime.now(timezone.utc) - item.updated_at).total_seconds() / 3600
        return age_hours <= self.recent_attention_hours

    def _has_prior_review_signal(self, item: AnalysisItem) -> bool:
        if item.item_type != "pr":
            return False
        pr = self.repo.prs.get(item.number)
        if pr is None:
            return False
        return pr.review_comments > 0 or self.repo.latest_pr_review_report(item.number) is not None

    def _is_draft(self, item: AnalysisItem) -> bool:
        if item.item_type != "pr":
            return False
        pr = self.repo.prs.get(item.number)
        return bool(pr and pr.draft)

    def _activity_trend_note(self, item: AnalysisItem) -> str:
        if item.item_type != "pr":
            return ""
        pr = self.repo.prs.get(item.number)
        if pr is None or pr.activity_comments_24h <= 0:
            return ""
        label = "comment" if pr.activity_comments_24h == 1 else "comments"
        return f" | activity: {pr.activity_comments_24h} {label} in last 24h"
