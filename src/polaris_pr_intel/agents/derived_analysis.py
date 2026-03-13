from __future__ import annotations

from datetime import datetime, timezone

from polaris_pr_intel.config import Settings
from polaris_pr_intel.llm.base import LLMAdapter
from polaris_pr_intel.models import AnalysisItem, AnalysisRun, DailyReport, PullRequestSnapshot, ReportArtifact
from polaris_pr_intel.store.base import Repository


class DerivedAnalysisAgent:
    def __init__(self, repo: Repository, llm: LLMAdapter, settings: Settings) -> None:
        self.repo = repo
        self.llm = llm
        self.settings = settings
        self.top_slice_limit = max(1, int(getattr(settings, "analysis_top_slice_limit", 10)))

    def run(self) -> tuple[AnalysisRun, DailyReport]:
        items = self._build_items()
        catalog_counts = self._catalog_counts(items)
        artifacts = self._build_artifacts(items, catalog_counts)
        run = AnalysisRun(
            source_sync_at=self.repo.last_sync_at,
            top_slice_limit=self.top_slice_limit,
            catalog_counts=catalog_counts,
            artifacts=artifacts,
            items=items,
        )
        legacy_report = DailyReport(
            date=datetime.now(timezone.utc).strftime("%Y-%m-%d"),
            markdown=self._legacy_markdown(run),
        )
        return run, legacy_report

    def _build_items(self) -> list[AnalysisItem]:
        items: list[AnalysisItem] = []
        seen_pr_numbers: set[int] = set()
        now_dt = datetime.now(timezone.utc)
        local_now = datetime.now().astimezone()
        local_tz = local_now.tzinfo
        local_today = local_now.date()

        def _is_updated_today_local(updated_at: datetime) -> bool:
            dt = updated_at if updated_at.tzinfo else updated_at.replace(tzinfo=timezone.utc)
            if local_tz is None:
                return dt.date() == local_today
            return dt.astimezone(local_tz).date() == local_today

        top_prs = self._select_top_prs()
        top_pr_numbers = {pr.number for pr in top_prs}
        batch_findings = self.llm.analyze_catalog_routing_batch(top_prs) if top_prs else {}

        for signal in sorted(self.repo.review_signals.values(), key=lambda s: s.score, reverse=True):
            pr = self.repo.prs.get(signal.pr_number)
            if not pr or pr.state != "open":
                continue
            seen_pr_numbers.add(pr.number)
            catalogs = self._base_pr_catalogs(pr, signal.score, signal.reasons, now_dt, _is_updated_today_local(pr.updated_at))
            llm_summary = ""
            llm_tags: list[str] = []
            llm_catalogs: list[str] = []
            confidence = 0.0
            if pr.number in top_pr_numbers:
                finding = batch_findings.get(pr.number)
                if finding is not None:
                    llm_summary = finding.summary
                    llm_tags = list(dict.fromkeys(finding.tags))
                    llm_catalogs = list(dict.fromkeys(finding.suggested_catalogs))
                    confidence = finding.confidence
            items.append(
                AnalysisItem(
                    item_type="pr",
                    number=pr.number,
                    title=pr.title,
                    url=pr.html_url,
                    score=signal.score,
                    heuristic_reasons=signal.reasons,
                    catalogs=self._merge_catalogs(catalogs, llm_catalogs),
                    llm_summary=llm_summary,
                    llm_tags=llm_tags,
                    llm_provider=self.llm.provider if pr.number in top_pr_numbers else "",
                    llm_model=self.llm.model if pr.number in top_pr_numbers else "",
                    confidence=confidence,
                    updated_at=pr.updated_at,
                )
            )

        for pr in sorted(self.repo.prs.values(), key=lambda item: item.updated_at, reverse=True):
            if pr.state != "open" or pr.number in seen_pr_numbers:
                continue
            catalogs = self._base_pr_catalogs(pr, 0.0, ["synced-open-pr"], now_dt, _is_updated_today_local(pr.updated_at))
            if not catalogs:
                continue
            items.append(
                AnalysisItem(
                    item_type="pr",
                    number=pr.number,
                    title=pr.title,
                    url=pr.html_url,
                    score=0.0,
                    heuristic_reasons=["synced-open-pr"],
                    catalogs=catalogs,
                    updated_at=pr.updated_at,
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
                )
            )

        items.sort(key=lambda item: (item.score, item.updated_at.timestamp()), reverse=True)
        return items

    def _select_top_prs(self) -> list[PullRequestSnapshot]:
        ranked: list[tuple[float, PullRequestSnapshot]] = []
        for signal in self.repo.review_signals.values():
            pr = self.repo.prs.get(signal.pr_number)
            if not pr or pr.state != "open":
                continue
            score = signal.score
            if signal.needs_review:
                score += 1.0
            ranked.append((score, pr))
        ranked.sort(key=lambda item: (item[0], item[1].updated_at.timestamp()), reverse=True)
        return [pr for _, pr in ranked[: self.top_slice_limit]]

    def _base_pr_catalogs(
        self,
        pr: PullRequestSnapshot,
        score: float,
        reasons: list[str],
        now_dt: datetime,
        updated_today: bool,
    ) -> list[str]:
        catalogs: list[str] = []
        text = f"{pr.title}\n{pr.body}".lower()
        labels = {label.lower() for label in pr.labels}
        if score >= self.settings.review_needed_threshold:
            catalogs.append("needs-review")
        if "requested-you" in reasons:
            catalogs.append("needs-review")
        if updated_today:
            catalogs.append("recently-updated")
        age_hours = (now_dt - pr.updated_at).total_seconds() / 3600
        if age_hours >= 72:
            catalogs.append("aging-prs")
        if "security" in text or "permission" in text or "security" in labels:
            catalogs.append("security-risk")
        if pr.additions + pr.deletions >= 500 or pr.changed_files >= 20 or "large-diff" in reasons:
            catalogs.append("release-risk")
        return list(dict.fromkeys(catalogs))

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

    def _merge_catalogs(self, base: list[str], llm_catalogs: list[str]) -> list[str]:
        allowed = {
            "needs-review",
            "aging-prs",
            "security-risk",
            "release-risk",
            "interesting-issues",
            "recently-updated",
        }
        merged = [catalog for catalog in [*base, *llm_catalogs] if catalog in allowed]
        return list(dict.fromkeys(merged))

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
        review_items.sort(key=lambda item: ("requested-you" not in item.heuristic_reasons, -item.score, item.updated_at.timestamp()))
        focus_items = review_items[:6]
        if not focus_items:
            lines.append("- No PRs currently require review.")
        else:
            for item in focus_items:
                lines.append(self._format_attention_pr(item))
        aging_watch = [item for item in review_items if "aging-prs" in item.catalogs and item not in focus_items][:3]
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
                reason = self._compact_reason(item)
                lines.append(f"- [#{item.number}]({item.url}) {item.title} | {reason}")
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
                f"- {catalog_counts.get('needs-review', 0)} PRs need review overall, but only the top queue is shown below.",
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
        if "requested-you" in item.heuristic_reasons:
            return "explicitly requested from you"
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
        llm_note = ""
        if item.llm_summary:
            llm_note = item.llm_summary.split(".")[0].strip()
            if llm_note:
                llm_note = f" | note: {llm_note}"
        return f"- [#{item.number}]({item.url}) {item.title} | why: {why}{llm_note}"
