from __future__ import annotations

import json
from html import escape
from typing import Any, Mapping


def render_new_updated_row(*, pr_number: int, html_url: str, title: str, updated_at_label: str) -> str:
    return (
        "<tr>"
        f"<td><a href=\"{escape(html_url)}\" target=\"_blank\" rel=\"noopener noreferrer\">#{pr_number}</a></td>"
        f"<td>{escape(title)}</td>"
        f"<td>{escape(updated_at_label)}</td>"
        f"<td><button class=\"action-btn\" onclick=\"runPrReview({pr_number}, this)\">Review</button></td>"
        "</tr>"
    )


def render_folded_new_updated_html(rows: list[str], *, activity_tz_label: str) -> str:
    if not rows:
        return ""
    return (
        "<details class=\"folded-section\">"
        f"<summary>Show {len(rows)} more PRs</summary>"
        "<table>"
        f"<thead><tr><th>PR</th><th>Title</th><th>Updated ({escape(activity_tz_label)})</th><th>Action</th></tr></thead>"
        f"<tbody>{''.join(rows)}</tbody>"
        "</table>"
        "</details>"
    )


def render_review_row(*, number: int, url: str, title: str, score: float, reasons: list[str]) -> str:
    return (
        f"<tr><td><a href=\"{escape(url)}\" target=\"_blank\" rel=\"noopener noreferrer\">#{number}</a></td>"
        f"<td>{escape(title)}</td>"
        "<td class=\"queue-overflow-cell\">"
        "<details class=\"queue-overflow\">"
        "<summary aria-label=\"Show score and reasons\">...</summary>"
        "<div class=\"queue-overflow-menu\">"
        f"<div><span class=\"queue-overflow-label\">Score</span><span>{score:.1f}</span></div>"
        f"<div><span class=\"queue-overflow-label\">Reasons</span><span>{escape(', '.join(reasons))}</span></div>"
        "</div>"
        "</details>"
        "</td>"
        f"<td><button class=\"action-btn\" onclick=\"runPrReview({number}, this)\">Review</button></td></tr>"
    )


def render_folded_review_html(rows: list[str]) -> str:
    if not rows:
        return ""
    return (
        "<details class=\"folded-section\">"
        f"<summary>Show {len(rows)} more PRs</summary>"
        "<table>"
        "<thead><tr><th>PR</th><th>Title</th><th></th><th>Action</th></tr></thead>"
        f"<tbody>{''.join(rows)}</tbody>"
        "</table>"
        "</details>"
    )


def render_issue_row(*, number: int, html_url: str, title: str, score: float, reasons: list[str]) -> str:
    return (
        f"<tr><td><a href=\"{escape(html_url)}\" target=\"_blank\" rel=\"noopener noreferrer\">#{number}</a></td>"
        f"<td>{escape(title)}</td><td>{score:.1f}</td><td>{escape(', '.join(reasons))}</td></tr>"
    )


def render_folded_issue_html(rows: list[str]) -> str:
    if not rows:
        return ""
    return (
        "<details class=\"folded-section\">"
        f"<summary>Show {len(rows)} more issues</summary>"
        "<table>"
        "<thead><tr><th>Issue</th><th>Title</th><th>Score</th><th>Reasons</th></tr></thead>"
        f"<tbody>{''.join(rows)}</tbody>"
        "</table>"
        "</details>"
    )


def render_deep_review_finding(
    *,
    agent_name: str,
    focus_area: str,
    verdict: str,
    score: float,
    confidence: float,
    summary: str,
    recommendations: list[str],
) -> str:
    recs = "".join(f"<li>{escape(rec)}</li>" for rec in recommendations)
    return (
        "<article class=\"finding\">"
        f"<div><strong>{escape(agent_name)}</strong> · {escape(focus_area)}</div>"
        f"<div class=\"finding-meta\"><span class=\"verdict {escape(verdict)}\">{escape(verdict.upper())}</span> score={score:.2f} confidence={confidence:.2f}</div>"
        f"<p>{escape(summary)}</p>"
        f"<ul>{recs}</ul>"
        "</article>"
    )


def render_deep_review_entry(
    *,
    pr_number: int,
    html_url: str,
    title: str,
    overall_priority: float,
    provider: str,
    model: str,
    recommendation: str,
    findings_html: str,
) -> str:
    return (
        "<details class=\"review-detail\">"
        "<summary>"
        f"<span><a href=\"{escape(html_url)}\" target=\"_blank\" rel=\"noopener noreferrer\">#{pr_number}</a> · {escape(title)}</span>"
        f"<span>priority={overall_priority:.2f} · <a href=\"/reviews/pr/{pr_number}/latest.html\" target=\"_blank\" rel=\"noopener noreferrer\" class=\"view-report-link\">View Report</a></span>"
        "</summary>"
        f"<p class=\"muted\">Provider: {escape(provider)} | Model: {escape(model)} | Recommendation: {escape(recommendation)}</p>"
        f"{findings_html if findings_html else '<p>No findings.</p>'}"
        "</details>"
    )


def render_review_job_row(
    *,
    job_id: str,
    pr_number: int | str,
    status: str,
    created_at: str,
    finished_at: str | None,
) -> str:
    status_cls = "job-status-" + ("queued" if status == "queued" else "running" if status == "running" else "done")
    return (
        "<tr>"
        f"<td><code>{escape(job_id)}</code></td>"
        f"<td>#{escape(str(pr_number))}</td>"
        f"<td><span class=\"job-status {status_cls}\">{escape(status)}</span></td>"
        f"<td>{escape(created_at)}</td>"
        f"<td>{escape(finished_at) if finished_at else '-'}</td>"
        f"<td><a href=\"/reviews/jobs/{escape(job_id)}\" target=\"_blank\" rel=\"noopener noreferrer\">JSON</a></td>"
        "</tr>"
    )


def render_dashboard_page(
    *,
    configured_llm_display: str,
    last_sync_at_label: str,
    seconds_until_next_refresh: int | None,
    next_refresh_label: str,
    stats: Mapping[str, Any],
    activity_tz_label: str,
    review_rows_html: str,
    folded_review_html: str,
    new_updated_count: int,
    new_updated_rows_html: str,
    folded_new_updated_html: str,
    issue_rows_html: str,
    folded_issue_html: str,
    deep_review_count: int,
    deep_review_entries_html: str,
    job_rows_html: str,
) -> str:
    next_refresh_attr = "" if seconds_until_next_refresh is None else str(seconds_until_next_refresh)
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Polaris PR Intelligence</title>
  <style>
    :root {{
      --bg: #0b1220;
      --card: #101a2c;
      --ink: #e6edf7;
      --muted: #93a4bb;
      --line: #22324b;
      --accent: #2dd4bf;
      --accent2: #34d399;
      --good: #86efac;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      color: var(--ink);
      font-family: "IBM Plex Sans", "Segoe UI", sans-serif;
      background:
        radial-gradient(circle at 0% 0%, rgba(45, 212, 191, 0.12) 0%, transparent 35%),
        radial-gradient(circle at 100% 20%, rgba(96, 165, 250, 0.14) 0%, transparent 35%),
        var(--bg);
    }}
    .wrap {{ max-width: 1200px; margin: 0 auto; padding: 24px; }}
    .hero {{
      border: 1px solid var(--line);
      background: linear-gradient(120deg, #0f1b2f 0%, #112138 55%, #0f2438 100%);
      border-radius: 18px;
      padding: 12px 16px;
      box-shadow: 0 14px 34px rgba(0,0,0,0.35);
    }}
    .hero-top {{
      display: grid;
      grid-template-columns: minmax(0, 1fr) fit-content(320px) fit-content(240px) 140px;
      grid-template-areas: "brand last next sync";
      gap: 14px;
      align-items: center;
    }}
    .hero-heading {{
      min-width: 0;
      display: flex;
      align-items: flex-start;
      gap: 0;
    }}
    .brand-copy {{
      min-width: 0;
    }}
    h1,h2,h3 {{ font-family: inherit; font-weight: 700; margin: 0 0 12px 0; }}
    h1 {{ font-size: 22px; margin-bottom: 1px; line-height: 1.02; }}
    h2 {{ font-size: 24px; margin-top: 18px; }}
    h3 {{ font-size: 20px; }}
    .muted {{ color: var(--muted); margin: 0; }}
    .hero-side {{
      min-width: 0;
      display: contents;
    }}
    .btn {{
      border: 1px solid rgba(45, 212, 191, 0.22);
      background: linear-gradient(135deg, #1f948f 0%, #177c80 100%);
      color: #f8fffe;
      padding: 8px 12px;
      border-radius: 12px;
      text-decoration: none;
      font-weight: 700;
      cursor: pointer;
      text-align: center;
      min-height: 40px;
      display: flex;
      align-items: center;
      justify-content: center;
      box-shadow: 0 6px 16px rgba(8, 54, 61, 0.24);
      transition: transform 0.15s ease, filter 0.15s ease, box-shadow 0.15s ease;
      white-space: nowrap;
      width: 140px;
    }}
    .btn:hover {{
      filter: brightness(1.05);
      transform: translateY(-1px);
      box-shadow: 0 8px 18px rgba(8, 54, 61, 0.28);
    }}
    .btn.primary {{
      background: linear-gradient(135deg, #238f9e 0%, #1d7d91 100%);
      border-color: rgba(125, 239, 233, 0.2);
    }}
    .btn.sync-btn {{
      grid-area: sync;
      background: linear-gradient(135deg, #1b877f 0%, #176f75 100%);
      border-color: rgba(79, 214, 197, 0.18);
    }}
    .btn.sync-btn.loading {{
      opacity: 0.82;
      cursor: default;
      position: relative;
    }}
    .btn.sync-btn.loading::after {{
      content: "";
      width: 12px;
      height: 12px;
      border-radius: 999px;
      border: 2px solid rgba(255, 255, 255, 0.35);
      border-top-color: #ffffff;
      margin-left: 8px;
      animation: sync-spin 0.8s linear infinite;
    }}
    .btn.sync-btn:disabled {{
      opacity: 0.75;
      cursor: default;
      box-shadow: none;
    }}
    @keyframes sync-spin {{
      to {{ transform: rotate(360deg); }}
    }}
    .grid {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(210px, 1fr));
      gap: 12px;
      margin: 16px 0 22px;
    }}
    .card {{
      background: var(--card);
      border: 1px solid var(--line);
      border-radius: 14px;
      padding: 14px;
    }}
    a {{ color: #7dd3fc; }}
    a:visited {{ color: #a5b4fc; }}
    .k {{ font-size: 12px; text-transform: uppercase; letter-spacing: .08em; color: var(--muted); }}
    .v {{ font-size: 28px; font-weight: 700; margin-top: 4px; }}
    .layout {{
      display: grid;
      grid-template-columns: 1.1fr .9fr;
      gap: 16px;
      align-items: start;
    }}
    .tab-fold > summary {{
      cursor: pointer;
      font-family: inherit;
      font-size: 20px;
      font-weight: 700;
      margin-bottom: 10px;
    }}
    .tab-fold[open] > summary {{
      margin-bottom: 12px;
    }}
    table {{ width: 100%; border-collapse: collapse; font-size: 14px; }}
    th, td {{ border-bottom: 1px solid var(--line); padding: 8px 6px; text-align: left; vertical-align: top; }}
    th {{ color: var(--muted); font-weight: 600; }}
    .report h1 {{ font-size: 28px; }}
    .report h2 {{ font-size: 20px; }}
    .report ul {{ margin: 0 0 8px 20px; padding: 0; }}
    .report li {{ margin-bottom: 6px; }}
    .review-detail {{
      border: 1px solid var(--line);
      border-radius: 10px;
      background: #0d1728;
      padding: 10px 12px;
      margin-bottom: 10px;
    }}
    .review-detail summary {{
      cursor: pointer;
      display: flex;
      justify-content: space-between;
      gap: 10px;
      flex-wrap: wrap;
      font-weight: 600;
      color: var(--accent2);
    }}
    .view-report-link {{
      color: var(--accent);
      text-decoration: none;
      font-size: 13px;
      padding: 2px 8px;
      border: 1px solid var(--accent);
      border-radius: 4px;
      transition: background 0.2s;
    }}
    .view-report-link:hover {{
      background: var(--accent);
      color: white;
    }}
    .finding {{
      margin-top: 10px;
      border-top: 1px dashed var(--line);
      padding-top: 10px;
    }}
    .finding-meta {{ color: var(--muted); font-size: 13px; margin: 4px 0; }}
    .verdict {{
      display: inline-block;
      border-radius: 999px;
      padding: 1px 8px;
      font-size: 11px;
      letter-spacing: .04em;
      border: 1px solid var(--line);
      margin-right: 6px;
    }}
    .verdict.high {{ background: #3a1f27; border-color: #ad4b64; }}
    .verdict.medium {{ background: #3b2f1d; border-color: #b88a37; }}
    .verdict.low {{ background: #183227; border-color: #3f9f70; }}
    .job-status {{
      display: inline-block;
      border-radius: 999px;
      padding: 2px 8px;
      font-size: 11px;
      border: 1px solid var(--line);
      text-transform: uppercase;
      letter-spacing: .04em;
    }}
    .job-status-queued {{ background: #1b2638; border-color: #485d7a; }}
    .job-status-running {{ background: #0f3042; border-color: #2779a7; }}
    .job-status-done {{ background: #173628; border-color: #3d8a62; }}
    .action-btn {{
      border: 1px solid var(--line);
      border-radius: 8px;
      background: var(--accent2);
      color: #fff;
      padding: 4px 10px;
      font-size: 12px;
      cursor: pointer;
    }}
    .action-btn[disabled] {{
      opacity: 0.65;
      cursor: default;
    }}
    .queue-overflow-cell {{
      width: 1%;
      white-space: nowrap;
      position: relative;
    }}
    .queue-overflow {{
      position: relative;
    }}
    .queue-overflow > summary {{
      list-style: none;
      width: 34px;
      height: 34px;
      display: inline-flex;
      align-items: center;
      justify-content: center;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: #132138;
      color: var(--ink);
      cursor: pointer;
      font-size: 18px;
      line-height: 1;
    }}
    .queue-overflow > summary::-webkit-details-marker {{
      display: none;
    }}
    .queue-overflow[open] > summary {{
      background: #182844;
      border-color: #36537b;
    }}
    .queue-overflow-menu {{
      position: absolute;
      right: 0;
      top: calc(100% + 6px);
      z-index: 20;
      width: min(360px, 60vw);
      padding: 10px 12px;
      border: 1px solid var(--line);
      border-radius: 10px;
      background: #0d1728;
      box-shadow: 0 16px 36px rgba(0, 0, 0, 0.35);
      white-space: normal;
    }}
    .queue-overflow-menu > div {{
      display: grid;
      grid-template-columns: 64px 1fr;
      gap: 10px;
      align-items: start;
    }}
    .queue-overflow-menu > div + div {{
      margin-top: 8px;
      padding-top: 8px;
      border-top: 1px solid rgba(147, 164, 187, 0.16);
    }}
    .queue-overflow-label {{
      color: var(--muted);
      font-size: 12px;
      text-transform: uppercase;
      letter-spacing: .06em;
    }}
    .folded-section {{
      margin-top: 10px;
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 8px;
      background: #0d1728;
    }}
    .folded-section > summary {{
      cursor: pointer;
      font-weight: 600;
      color: var(--accent2);
      margin-bottom: 6px;
    }}
    .queue-section > summary {{
      cursor: pointer;
      font-family: inherit;
      font-size: 20px;
      font-weight: 700;
      color: var(--ink);
      margin-bottom: 8px;
    }}
    .queue-section[open] > summary {{
      margin-bottom: 10px;
    }}
    .status-item {{
      padding: 9px 14px 9px 16px;
      border-radius: 16px;
      background:
        radial-gradient(circle at top right, rgba(45, 212, 191, 0.08) 0%, transparent 30%),
        linear-gradient(180deg, rgba(14, 26, 44, 0.96) 0%, rgba(11, 21, 37, 0.98) 100%);
      border: 1px solid rgba(125, 211, 252, 0.14);
      box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.04), 0 8px 20px rgba(2, 8, 18, 0.24);
      position: relative;
      overflow: hidden;
      white-space: nowrap;
    }}
    .status-item::before {{
      content: "";
      position: absolute;
      inset: 7px auto 7px 0;
      width: 4px;
      border-radius: 999px;
      background: linear-gradient(180deg, var(--accent) 0%, #7dd3fc 100%);
    }}
    .status-item .k {{
      font-size: 11px;
      letter-spacing: 0.08em;
      text-transform: uppercase;
      color: #9fb7d8;
      position: relative;
      margin: 0;
      display: inline;
    }}
    .status-item .v {{
      font-size: 14px;
      font-weight: 500;
      color: #f8fbff;
      text-shadow: 0 1px 0 rgba(0, 0, 0, 0.3);
      position: relative;
      line-height: 1.15;
      white-space: nowrap;
      display: inline;
      margin-left: 8px;
    }}
    .hero-heading {{
      grid-area: brand;
      display: flex;
      align-items: center;
      gap: 14px;
      min-width: 0;
    }}
    .status-last {{
      grid-area: last;
    }}
    .status-next {{
      grid-area: next;
    }}
    .brand-logo {{
      width: 56px;
      height: 56px;
      object-fit: contain;
      flex: 0 0 auto;
    }}
    .brand-copy h1 {{
      margin: 0;
      white-space: nowrap;
    }}
    .brand-copy .muted {{
      margin-top: 6px;
      white-space: nowrap;
    }}
    @media (max-width: 1180px) {{
      .hero-top {{
        grid-template-columns: minmax(0, 1fr) fit-content(320px) 140px;
        grid-template-areas:
          "brand last sync"
          "brand next sync";
        align-items: stretch;
      }}
    }}
    @media (max-width: 960px) {{
      .hero-top {{
        grid-template-columns: 1fr;
        grid-template-areas:
          "brand"
          "last"
          "next"
          "sync";
      }}
      .btn.sync-btn {{
        width: 100%;
      }}
      .layout {{ grid-template-columns: 1fr; }}
      h1 {{ font-size: 28px; }}
    }}
  </style>
  <script>
    async function refreshAll(btn) {{
      const original = btn.textContent;
      btn.disabled = true;
      btn.classList.add("loading");
      try {{
        const res = await fetch("/refresh?per_page=100&max_pages=20", {{ method: "POST" }});
        const data = await res.json();
        if (data.ok) {{
          btn.textContent = "Done";
          setTimeout(() => window.location.reload(), 600);
        }} else {{
          btn.textContent = "Failed";
          console.error("refresh failed", data);
        }}
      }} catch (e) {{
        btn.textContent = "Failed";
        console.error(e);
      }} finally {{
        setTimeout(() => {{
          btn.disabled = false;
          btn.classList.remove("loading");
          if (btn.textContent !== "Failed") btn.textContent = original;
        }}, 2000);
      }}
    }}

    async function runPrReview(prNumber, btn) {{
      const original = btn.textContent;
      btn.disabled = true;
      btn.textContent = "Queued...";
      try {{
        const res = await fetch(`/reviews/pr/${{prNumber}}/run`, {{ method: "POST" }});
        const data = await res.json();
        if (data.ok && data.accepted) {{
          btn.textContent = "Queued";
          setTimeout(() => window.location.reload(), 300);
        }} else {{
          btn.textContent = "Failed";
          console.error("review enqueue failed", data);
        }}
      }} catch (e) {{
        btn.textContent = "Failed";
        console.error(e);
      }} finally {{
        setTimeout(() => {{
          btn.disabled = false;
          if (btn.textContent !== "Failed") btn.textContent = original;
        }}, 2000);
      }}
    }}

    function formatDuration(totalSeconds) {{
      if (totalSeconds == null) return "N/A";
      const totalMinutes = Math.max(0, Math.floor(Number(totalSeconds) / 60));
      const hours = Math.floor(totalMinutes / 60);
      const minutes = totalMinutes - hours * 60;
      const parts = [];
      if (hours) parts.push(`${{hours}}h`);
      parts.push(`${{minutes}}m`);
      return parts.join(" ");
    }}

    function startRefreshCountdown() {{
      const countdownEl = document.getElementById("next-refresh-countdown");
      if (!countdownEl) return;
      let remaining = Number(countdownEl.dataset.remainingSeconds || "");
      if (!Number.isFinite(remaining)) return;
      countdownEl.textContent = formatDuration(remaining);
      window.setInterval(() => {{
        remaining = Math.max(0, remaining - 1);
        countdownEl.textContent = formatDuration(remaining);
      }}, 1000);
    }}

    window.addEventListener("DOMContentLoaded", startRefreshCountdown);
  </script>
</head>
	 <body>
	  <div class="wrap">
	    <section class="hero">
	      <div class="hero-top">
          <div class="hero-heading">
            <img class="brand-logo" src="/docs-static/orca.png" alt="Orca logo" />
            <div class="brand-copy">
	          <h1>PR Intelligence</h1>
	          <p class="muted">{escape(configured_llm_display)}</p>
            </div>
          </div>
          <article class="status-item status-last">
            <div class="k">Last Update</div>
            <div class="v">{escape(last_sync_at_label)}</div>
          </article>
          <article class="status-item status-next">
            <div class="k">Next Update In:</div>
            <div class="v" id="next-refresh-countdown" data-remaining-seconds="{next_refresh_attr}">{escape(next_refresh_label)}</div>
          </article>
          <button class="btn sync-btn" type="button" onclick="refreshAll(this)">Sync</button>
        </div>
    </section>

    <section class="grid">
      <article class="card"><div class="k">PRs Tracked</div><div class="v">{escape(str(stats["prs_tracked"]))}</div></article>
      <article class="card"><div class="k">Issues Tracked</div><div class="v">{escape(str(stats["issues_tracked"]))}</div></article>
      <article class="card"><div class="k">Needs Review Queue</div><div class="v">{escape(str(stats["needs_review_queue"]))}</div></article>
      <article class="card"><div class="k">Interesting Issues</div><div class="v">{escape(str(stats["interesting_issues_queue"]))}</div></article>
      <article class="card"><div class="k">Deep PR Reviews</div><div class="v">{escape(str(stats["deep_pr_reviews"]))}</div></article>
    </section>

    <section class="layout">
      <div>
        <article class="card">
          <details class="queue-section">
            <summary>PRs Needing Review ({escape(str(stats["needs_review_queue"]))})</summary>
            <table>
              <thead><tr><th>PR</th><th>Title</th><th></th><th>Action</th></tr></thead>
              <tbody>{review_rows_html if review_rows_html else '<tr><td colspan="4">No PRs queued.</td></tr>'}</tbody>
            </table>
            {folded_review_html}
          </details>
        </article>
        <article class="card" style="margin-top: 14px;">
          <details class="tab-fold">
            <summary>New/Updated PRs Today ({escape(activity_tz_label)}) ({new_updated_count})</summary>
            <table>
              <thead><tr><th>PR</th><th>Title</th><th>Updated ({escape(activity_tz_label)})</th><th>Action</th></tr></thead>
              <tbody>{new_updated_rows_html if new_updated_rows_html else '<tr><td colspan="4">No PR updates observed today.</td></tr>'}</tbody>
            </table>
            {folded_new_updated_html}
          </details>
        </article>
      </div>
      <aside>
        <article class="card">
          <details class="queue-section">
            <summary>Interesting Issues ({escape(str(stats["interesting_issues_queue"]))})</summary>
            <table>
              <thead><tr><th>Issue</th><th>Title</th><th>Score</th><th>Reasons</th></tr></thead>
              <tbody>{issue_rows_html if issue_rows_html else '<tr><td colspan="4">No issues queued.</td></tr>'}</tbody>
            </table>
            {folded_issue_html}
          </details>
        </article>
        <article class="card" style="margin-top: 14px;">
          <details class="queue-section">
            <summary>Deep PR Reviews ({deep_review_count})</summary>
            <div>{deep_review_entries_html if deep_review_entries_html else '<p class="muted">No deep reviews yet.</p>'}</div>
          </details>
        </article>
        <article class="card" style="margin-top: 14px;">
          <h3>Review Jobs</h3>
          <p class="muted">Shows recent async PR review jobs (queued/running/completed).</p>
          <table>
            <thead><tr><th>Job ID</th><th>PR</th><th>Status</th><th>Created</th><th>Finished</th><th>Details</th></tr></thead>
            <tbody>{job_rows_html if job_rows_html else '<tr><td colspan="6">No review jobs yet.</td></tr>'}</tbody>
          </table>
        </article>
      </aside>
    </section>
  </div>
</body>
</html>"""


def render_latest_pr_review_page(*, pr_number: int, markdown_content: str) -> str:
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>PR #{pr_number} Review</title>
    <script src="https://cdn.jsdelivr.net/npm/marked@11.1.1/marked.min.js"></script>
    <style>
        * {{
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }}

        body {{
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif;
            line-height: 1.6;
            color: #24292f;
            background: #f6f8fa;
            padding: 20px;
        }}

        .container {{
            max-width: 980px;
            margin: 0 auto;
            background: white;
            padding: 40px;
            border-radius: 8px;
            box-shadow: 0 1px 3px rgba(0,0,0,0.1);
        }}

        h1 {{
            font-size: 2em;
            margin-bottom: 0.5em;
            padding-bottom: 0.3em;
            border-bottom: 1px solid #d0d7de;
            color: #0969da;
        }}

        h2 {{
            font-size: 1.5em;
            margin-top: 1.5em;
            margin-bottom: 0.5em;
            padding-bottom: 0.3em;
            border-bottom: 1px solid #d0d7de;
        }}

        h3 {{
            font-size: 1.25em;
            margin-top: 1.5em;
            margin-bottom: 0.5em;
        }}

        h4 {{
            font-size: 1.1em;
            margin-top: 1em;
            margin-bottom: 0.5em;
            color: #57606a;
        }}

        p {{
            margin-bottom: 1em;
        }}

        strong {{
            color: #24292f;
            font-weight: 600;
        }}

        a {{
            color: #0969da;
            text-decoration: none;
        }}

        a:hover {{
            text-decoration: underline;
        }}

        hr {{
            border: none;
            border-top: 1px solid #d0d7de;
            margin: 1.5em 0;
        }}

        ul, ol {{
            margin-left: 2em;
            margin-bottom: 1em;
        }}

        li {{
            margin-bottom: 0.25em;
        }}

        code {{
            background: #f6f8fa;
            padding: 0.2em 0.4em;
            border-radius: 3px;
            font-family: 'SF Mono', Monaco, 'Courier New', monospace;
            font-size: 0.9em;
        }}

        pre {{
            background: #f6f8fa;
            padding: 16px;
            border-radius: 6px;
            overflow-x: auto;
            margin-bottom: 1em;
        }}

        pre code {{
            background: none;
            padding: 0;
        }}

        .back-link {{
            display: inline-block;
            margin-bottom: 20px;
            padding: 8px 16px;
            background: #f6f8fa;
            border: 1px solid #d0d7de;
            border-radius: 6px;
            color: #24292f;
            text-decoration: none;
            font-size: 0.9em;
        }}

        .back-link:hover {{
            background: #eaeef2;
            text-decoration: none;
        }}

        @media (max-width: 768px) {{
            body {{
                padding: 10px;
            }}

            .container {{
                padding: 20px;
            }}

            h1 {{
                font-size: 1.5em;
            }}
        }}
    </style>
</head>
<body>
    <div class="container">
        <a href="/ui" class="back-link">&larr; Back to Dashboard</a>
        <div id="content"></div>
    </div>

    <script>
        const markdown = {json.dumps(markdown_content)};
        document.getElementById('content').innerHTML = marked.parse(markdown);
    </script>
</body>
</html>"""
