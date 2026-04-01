from __future__ import annotations

import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path

from polaris_pr_intel.models import (
    AnalysisRun,
    IssueSignal,
    IssueSnapshot,
    PRReviewReport,
    PRSummary,
    PullRequestSnapshot,
    ReviewSignal,
)


class SQLiteRepository:
    def __init__(self, db_path: str) -> None:
        self.db_path = db_path
        path = Path(db_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._init_schema()

    def _init_schema(self) -> None:
        with self._lock, self._conn:
            self._conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS prs (
                    number INTEGER PRIMARY KEY,
                    payload TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS issues (
                    number INTEGER PRIMARY KEY,
                    payload TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS pr_summaries (
                    pr_number INTEGER PRIMARY KEY,
                    payload TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS review_signals (
                    pr_number INTEGER PRIMARY KEY,
                    score REAL NOT NULL,
                    needs_review INTEGER NOT NULL,
                    payload TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS issue_signals (
                    issue_number INTEGER PRIMARY KEY,
                    score REAL NOT NULL,
                    interesting INTEGER NOT NULL,
                    payload TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS pr_review_reports (
                    pr_number INTEGER PRIMARY KEY,
                    overall_priority REAL NOT NULL,
                    generated_at TEXT NOT NULL,
                    payload TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS analysis_runs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at TEXT NOT NULL,
                    payload TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS processed_events (
                    delivery_id TEXT PRIMARY KEY,
                    processed_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS metadata (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                """
            )

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def _get_metadata_value(self, key: str) -> str | None:
        with self._lock:
            row = self._conn.execute("SELECT value FROM metadata WHERE key = ?", (key,)).fetchone()
        if not row:
            return None
        return str(row["value"])

    def _set_metadata_value(self, key: str, value: str | None) -> None:
        with self._lock, self._conn:
            if value is None:
                self._conn.execute("DELETE FROM metadata WHERE key = ?", (key,))
                return
            self._conn.execute(
                "INSERT INTO metadata(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, value),
            )

    def _get_metadata_datetime(self, key: str) -> datetime | None:
        raw = self._get_metadata_value(key)
        if raw is None:
            return None
        return datetime.fromisoformat(raw)

    def _set_metadata_datetime(self, key: str, value: datetime | None) -> None:
        self._set_metadata_value(key, value.isoformat() if value else None)

    @property
    def prs(self) -> dict[int, PullRequestSnapshot]:
        with self._lock:
            rows = self._conn.execute("SELECT number, payload FROM prs").fetchall()
        return {int(row["number"]): PullRequestSnapshot.model_validate_json(row["payload"]) for row in rows}

    @property
    def issues(self) -> dict[int, IssueSnapshot]:
        with self._lock:
            rows = self._conn.execute("SELECT number, payload FROM issues").fetchall()
        return {int(row["number"]): IssueSnapshot.model_validate_json(row["payload"]) for row in rows}

    @property
    def pr_summaries(self) -> dict[int, PRSummary]:
        with self._lock:
            rows = self._conn.execute("SELECT pr_number, payload FROM pr_summaries").fetchall()
        return {int(row["pr_number"]): PRSummary.model_validate_json(row["payload"]) for row in rows}

    @property
    def review_signals(self) -> dict[int, ReviewSignal]:
        with self._lock:
            rows = self._conn.execute("SELECT pr_number, payload FROM review_signals").fetchall()
        return {int(row["pr_number"]): ReviewSignal.model_validate_json(row["payload"]) for row in rows}

    @property
    def issue_signals(self) -> dict[int, IssueSignal]:
        with self._lock:
            rows = self._conn.execute("SELECT issue_number, payload FROM issue_signals").fetchall()
        return {int(row["issue_number"]): IssueSignal.model_validate_json(row["payload"]) for row in rows}

    @property
    def pr_review_reports(self) -> dict[int, PRReviewReport]:
        with self._lock:
            rows = self._conn.execute("SELECT pr_number, payload FROM pr_review_reports").fetchall()
        return {int(row["pr_number"]): PRReviewReport.model_validate_json(row["payload"]) for row in rows}

    @property
    def analysis_runs(self) -> list[AnalysisRun]:
        with self._lock:
            rows = self._conn.execute("SELECT payload FROM analysis_runs ORDER BY id ASC").fetchall()
        return [AnalysisRun.model_validate_json(row["payload"]) for row in rows]

    @property
    def last_sync_at(self) -> datetime | None:
        return self._get_metadata_datetime("last_sync_at")

    @last_sync_at.setter
    def last_sync_at(self, value: datetime | None) -> None:
        self._set_metadata_datetime("last_sync_at", value)

    @property
    def scheduled_refresh_attempted_at(self) -> datetime | None:
        return self._get_metadata_datetime("scheduled_refresh_attempted_at")

    @scheduled_refresh_attempted_at.setter
    def scheduled_refresh_attempted_at(self, value: datetime | None) -> None:
        self._set_metadata_datetime("scheduled_refresh_attempted_at", value)

    @property
    def scheduled_refresh_succeeded_at(self) -> datetime | None:
        return self._get_metadata_datetime("scheduled_refresh_succeeded_at")

    @scheduled_refresh_succeeded_at.setter
    def scheduled_refresh_succeeded_at(self, value: datetime | None) -> None:
        self._set_metadata_datetime("scheduled_refresh_succeeded_at", value)

    @property
    def scheduled_refresh_failed_at(self) -> datetime | None:
        return self._get_metadata_datetime("scheduled_refresh_failed_at")

    @scheduled_refresh_failed_at.setter
    def scheduled_refresh_failed_at(self, value: datetime | None) -> None:
        self._set_metadata_datetime("scheduled_refresh_failed_at", value)

    @property
    def scheduled_refresh_last_error(self) -> str | None:
        return self._get_metadata_value("scheduled_refresh_last_error")

    @scheduled_refresh_last_error.setter
    def scheduled_refresh_last_error(self, value: str | None) -> None:
        self._set_metadata_value("scheduled_refresh_last_error", value)

    def upsert_pr(self, pr: PullRequestSnapshot) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO prs(number, payload) VALUES(?, ?) ON CONFLICT(number) DO UPDATE SET payload = excluded.payload",
                (pr.number, pr.model_dump_json()),
            )

    def upsert_issue(self, issue: IssueSnapshot) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO issues(number, payload) VALUES(?, ?) ON CONFLICT(number) DO UPDATE SET payload = excluded.payload",
                (issue.number, issue.model_dump_json()),
            )

    def save_pr_summary(self, summary: PRSummary) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO pr_summaries(pr_number, payload) VALUES(?, ?) ON CONFLICT(pr_number) DO UPDATE SET payload = excluded.payload",
                (summary.pr_number, summary.model_dump_json()),
            )

    def save_review_signal(self, signal: ReviewSignal) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                """
                INSERT INTO review_signals(pr_number, score, needs_review, payload) VALUES(?, ?, ?, ?)
                ON CONFLICT(pr_number) DO UPDATE SET score = excluded.score, needs_review = excluded.needs_review, payload = excluded.payload
                """,
                (signal.pr_number, signal.score, int(signal.needs_review), signal.model_dump_json()),
            )

    def save_issue_signal(self, signal: IssueSignal) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                """
                INSERT INTO issue_signals(issue_number, score, interesting, payload) VALUES(?, ?, ?, ?)
                ON CONFLICT(issue_number) DO UPDATE SET score = excluded.score, interesting = excluded.interesting, payload = excluded.payload
                """,
                (signal.issue_number, signal.score, int(signal.interesting), signal.model_dump_json()),
            )

    def save_pr_review_report(self, report: PRReviewReport) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                """
                INSERT INTO pr_review_reports(pr_number, overall_priority, generated_at, payload) VALUES(?, ?, ?, ?)
                ON CONFLICT(pr_number) DO UPDATE SET overall_priority = excluded.overall_priority, generated_at = excluded.generated_at, payload = excluded.payload
                """,
                (report.pr_number, report.overall_priority, report.generated_at.isoformat(), report.model_dump_json()),
            )

    def save_analysis_run(self, run: AnalysisRun) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO analysis_runs(created_at, payload) VALUES(?, ?)",
                (run.created_at.isoformat(), run.model_dump_json()),
            )

    def latest_analysis_run(self) -> AnalysisRun | None:
        with self._lock:
            row = self._conn.execute("SELECT payload FROM analysis_runs ORDER BY id DESC LIMIT 1").fetchone()
        if not row:
            return None
        return AnalysisRun.model_validate_json(row["payload"])

    def list_analysis_runs(self, limit: int = 30, offset: int = 0) -> list[AnalysisRun]:
        if offset < 0:
            offset = 0
        if limit < 1:
            limit = 1
        with self._lock:
            rows = self._conn.execute(
                "SELECT payload FROM analysis_runs ORDER BY id DESC LIMIT ? OFFSET ?",
                (limit, offset),
            ).fetchall()
        return [AnalysisRun.model_validate_json(row["payload"]) for row in rows]

    def latest_pr_review_report(self, pr_number: int) -> PRReviewReport | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT payload FROM pr_review_reports WHERE pr_number = ? LIMIT 1",
                (pr_number,),
            ).fetchone()
        if not row:
            return None
        return PRReviewReport.model_validate_json(row["payload"])

    def top_pr_review_reports(self, limit: int = 20) -> list[PRReviewReport]:
        if limit < 1:
            limit = 1
        with self._lock:
            rows = self._conn.execute(
                "SELECT payload FROM pr_review_reports ORDER BY overall_priority DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [PRReviewReport.model_validate_json(row["payload"]) for row in rows]

    def has_processed_event(self, delivery_id: str) -> bool:
        with self._lock:
            row = self._conn.execute("SELECT 1 FROM processed_events WHERE delivery_id = ? LIMIT 1", (delivery_id,)).fetchone()
        return row is not None

    def mark_processed_event(self, delivery_id: str) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT OR IGNORE INTO processed_events(delivery_id, processed_at) VALUES(?, ?)",
                (delivery_id, datetime.now(timezone.utc).isoformat()),
            )
