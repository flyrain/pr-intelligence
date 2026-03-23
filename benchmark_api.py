#!/usr/bin/env python3
"""Benchmark sync vs async GitHub REST API performance."""
import os
import time

from polaris_pr_intel.github.client import GitHubClient
from polaris_pr_intel.github.async_client import GitHubClientWrapper


def benchmark_sync():
    """Benchmark synchronous REST API (40 sequential requests for 10 PRs) - OLD."""
    token = os.getenv("PR_INTEL_GITHUB_TOKEN") or os.getenv("GITHUB_TOKEN")
    if not token:
        raise RuntimeError("GITHUB_TOKEN required")

    owner = os.getenv("GITHUB_OWNER", "apache")
    repo = os.getenv("GITHUB_REPO", "polaris")

    client = GitHubClient(token, owner, repo)

    start = time.time()
    prs = client.list_recent_pull_requests(per_page=10)
    elapsed = time.time() - start

    client.close()

    print(f"❌ OLD (Sync REST): {len(prs)} PRs in {elapsed:.2f}s ({elapsed/len(prs):.2f}s per PR)")
    return elapsed, len(prs)


def benchmark_async():
    """Benchmark async REST API (40 parallel requests for 10 PRs) - NEW DEFAULT."""
    token = os.getenv("PR_INTEL_GITHUB_TOKEN") or os.getenv("GITHUB_TOKEN")
    if not token:
        raise RuntimeError("GITHUB_TOKEN required")

    owner = os.getenv("GITHUB_OWNER", "apache")
    repo = os.getenv("GITHUB_REPO", "polaris")

    client = GitHubClientWrapper(token, owner, repo)

    start = time.time()
    prs = client.list_recent_pull_requests(per_page=10)
    elapsed = time.time() - start

    client.close()

    print(f"✅ NEW (Async REST - DEFAULT): {len(prs)} PRs in {elapsed:.2f}s ({elapsed/len(prs):.2f}s per PR)")
    return elapsed, len(prs)


if __name__ == "__main__":
    print("Comparing sync vs async REST API...\n")

    sync_time, sync_count = benchmark_sync()
    async_time, async_count = benchmark_async()

    speedup = sync_time / async_time
    print(f"\n🚀 Async is {speedup:.1f}x faster!")
    print(f"   Time saved per sync: {sync_time - async_time:.2f}s")
