"""Integration tests for git operations and worktree management."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from polaris_pr_intel.git.repo_manager import RepositoryManager
from polaris_pr_intel.git.worktree_manager import WorktreeManager


@pytest.fixture
def test_repo(tmp_path):
    """Create a test git repository with commits."""
    repo_path = tmp_path / "test-repo"
    repo_path.mkdir()

    # Initialize git repo
    subprocess.run(["git", "init"], cwd=repo_path, check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"],
        cwd=repo_path,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test User"],
        cwd=repo_path,
        check=True,
        capture_output=True,
    )

    # Create initial commit
    (repo_path / "README.md").write_text("# Test Repo", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repo_path, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "Initial commit"],
        cwd=repo_path,
        check=True,
        capture_output=True,
    )

    # Create a feature branch (simulating a PR)
    subprocess.run(
        ["git", "checkout", "-b", "feature-branch"],
        cwd=repo_path,
        check=True,
        capture_output=True,
    )
    (repo_path / "feature.txt").write_text("New feature", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repo_path, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "Add feature"],
        cwd=repo_path,
        check=True,
        capture_output=True,
    )

    # Go back to main
    subprocess.run(
        ["git", "checkout", "main"],
        cwd=repo_path,
        check=True,
        capture_output=True,
    )

    return repo_path


def test_fetch_pr_branch_creates_local_branch(test_repo):
    """Test that fetch_pr_branch creates correct local branch."""
    # Setup: Create PR-like ref
    # In real GitHub, PRs are exposed as refs/pull/{number}/head
    # We simulate this by creating that ref structure
    subprocess.run(
        ["git", "update-ref", "refs/pull/123/head", "main"],
        cwd=test_repo,
        check=True,
        capture_output=True,
    )

    manager = RepositoryManager(
        owner="test-owner",
        repo="test-repo",
        explicit_path=str(test_repo),
    )

    # Fetch PR branch
    branch = manager.fetch_pr_branch(123)

    # Verify correct branch name
    assert branch == "pr-123"

    # Verify branch exists and points to correct commit
    result = subprocess.run(
        ["git", "rev-parse", "--verify", "pr-123"],
        cwd=test_repo,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0

    # Verify it points to main (same as our PR ref)
    main_sha = subprocess.run(
        ["git", "rev-parse", "main"],
        cwd=test_repo,
        capture_output=True,
        text=True,
    ).stdout.strip()

    pr_sha = subprocess.run(
        ["git", "rev-parse", "pr-123"],
        cwd=test_repo,
        capture_output=True,
        text=True,
    ).stdout.strip()

    assert main_sha == pr_sha


def test_fetch_pr_branch_overwrites_existing_branch(test_repo):
    """Test that fetch_pr_branch overwrites existing branch with --force."""
    # Setup: Create two commits
    subprocess.run(
        ["git", "update-ref", "refs/pull/456/head", "main"],
        cwd=test_repo,
        check=True,
        capture_output=True,
    )

    # Create new commit
    (test_repo / "new.txt").write_text("new", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=test_repo, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "New commit"],
        cwd=test_repo,
        check=True,
        capture_output=True,
    )

    # Update PR ref to point to new commit
    subprocess.run(
        ["git", "update-ref", "refs/pull/456/head", "main"],
        cwd=test_repo,
        check=True,
        capture_output=True,
    )

    manager = RepositoryManager(
        owner="test-owner",
        repo="test-repo",
        explicit_path=str(test_repo),
    )

    # Fetch twice - second should overwrite
    branch1 = manager.fetch_pr_branch(456)
    branch2 = manager.fetch_pr_branch(456)

    # Should be same branch name
    assert branch1 == branch2 == "pr-456"

    # Should point to latest commit
    main_sha = subprocess.run(
        ["git", "rev-parse", "main"],
        cwd=test_repo,
        capture_output=True,
        text=True,
    ).stdout.strip()

    pr_sha = subprocess.run(
        ["git", "rev-parse", "pr-456"],
        cwd=test_repo,
        capture_output=True,
        text=True,
    ).stdout.strip()

    assert main_sha == pr_sha


def test_fetch_pr_branch_fails_for_nonexistent_pr(test_repo):
    """Test that fetch_pr_branch raises error for nonexistent PR."""
    manager = RepositoryManager(
        owner="test-owner",
        repo="test-repo",
        explicit_path=str(test_repo),
    )

    # Try to fetch PR that doesn't exist
    with pytest.raises(RuntimeError, match="Failed to fetch PR #999"):
        manager.fetch_pr_branch(999)


def test_repository_manager_lazy_initialization(tmp_path):
    """Test that RepositoryManager doesn't clone until get_base_repo is called."""
    cache_dir = tmp_path / "cache"

    # Create manager but don't call get_base_repo
    manager = RepositoryManager(
        owner="test-owner",
        repo="test-repo",
        explicit_path=None,
        cache_dir=str(cache_dir),
    )

    # Cache directory should not exist yet (lazy)
    assert not cache_dir.exists()

    # Internal state should be None
    assert manager._base_repo_path is None


def test_repository_manager_explicit_path(test_repo):
    """Test that explicit path is used without cloning."""
    manager = RepositoryManager(
        owner="test-owner",
        repo="test-repo",
        explicit_path=str(test_repo),
    )

    # Should return the explicit path
    result = manager.get_base_repo()
    assert result == test_repo
    assert (result / ".git").exists()


def test_repository_manager_caches_result(test_repo):
    """Test that get_base_repo caches its result."""
    manager = RepositoryManager(
        owner="test-owner",
        repo="test-repo",
        explicit_path=str(test_repo),
    )

    # First call
    result1 = manager.get_base_repo()

    # Second call should return cached result
    result2 = manager.get_base_repo()

    assert result1 == result2
    assert manager._base_repo_path is not None


def test_worktree_manager_creates_and_removes_worktree(test_repo):
    """Test end-to-end worktree creation and cleanup."""
    worktree_base = test_repo / ".worktrees"

    manager = WorktreeManager(
        base_repo_path=test_repo,
        worktree_base_dir=worktree_base,
        auto_cleanup=True,
    )

    # Create worktree for PR #123
    ctx = manager.create_worktree_for_pr(123, branch="main")

    # Worktree should exist
    assert ctx.path.exists()
    assert (ctx.path / ".git").exists()
    assert (ctx.path / "README.md").exists()

    # List worktrees
    worktrees = manager.list_worktrees()
    assert 123 in worktrees

    # Remove worktree
    manager.remove_worktree(123)

    # Worktree should be gone
    assert not ctx.path.exists()
    worktrees = manager.list_worktrees()
    assert 123 not in worktrees


def test_worktree_with_specific_branch(test_repo):
    """Test creating worktree with a specific branch."""
    worktree_base = test_repo / ".worktrees"

    manager = WorktreeManager(
        base_repo_path=test_repo,
        worktree_base_dir=worktree_base,
        auto_cleanup=True,
    )

    # Create worktree for feature branch
    ctx = manager.create_worktree_for_pr(456, branch="feature-branch")

    # Worktree should exist with feature file
    assert ctx.path.exists()
    assert (ctx.path / "feature.txt").exists()
    assert (ctx.path / "feature.txt").read_text(encoding="utf-8") == "New feature"

    # Cleanup
    manager.remove_worktree(456)


def test_worktree_replaces_existing(test_repo):
    """Test that creating worktree for same PR replaces existing one."""
    worktree_base = test_repo / ".worktrees"

    manager = WorktreeManager(
        base_repo_path=test_repo,
        worktree_base_dir=worktree_base,
        auto_cleanup=True,
    )

    # Create worktree twice for same PR
    ctx1 = manager.create_worktree_for_pr(789, branch="main")
    ctx2 = manager.create_worktree_for_pr(789, branch="main")

    # Should be same path
    assert ctx1.path == ctx2.path
    assert ctx2.path.exists()

    # Only one worktree should exist
    worktrees = manager.list_worktrees()
    assert worktrees.count(789) == 1

    # Cleanup
    manager.remove_worktree(789)


def test_integration_repo_manager_with_cwd_detection(test_repo, monkeypatch):
    """Test that RepositoryManager detects repo when running inside it."""
    # Change to test repo directory
    monkeypatch.chdir(test_repo)

    # Set up remote to match
    subprocess.run(
        ["git", "remote", "add", "origin", "https://github.com/test-owner/test-repo.git"],
        cwd=test_repo,
        check=True,
        capture_output=True,
    )

    manager = RepositoryManager(
        owner="test-owner",
        repo="test-repo",
        explicit_path=None,
    )

    # Should detect current directory
    result = manager.get_base_repo()
    assert result == test_repo


def test_repository_manager_cleans_up_failed_clone(tmp_path, monkeypatch):
    """Test that failed clones are cleaned up."""
    cache_dir = tmp_path / "cache"

    manager = RepositoryManager(
        owner="test-owner",
        repo="nonexistent-repo",
        explicit_path=None,
        cache_dir=str(cache_dir),
    )

    # Mock subprocess to simulate failure
    def mock_run(*args, **kwargs):
        if "clone" in args[0]:
            # Create partial directory
            dest = Path(args[0][-1])
            dest.mkdir(parents=True)
            (dest / "partial.txt").write_text("partial", encoding="utf-8")
            raise subprocess.CalledProcessError(1, args[0], stderr="fatal: repository not found")
        return subprocess.run(*args, **kwargs)

    monkeypatch.setattr(subprocess, "run", mock_run)

    # Should fail
    with pytest.raises(RuntimeError, match="Failed to clone repository"):
        manager.get_base_repo()

    # Partial clone should be cleaned up
    expected_path = cache_dir / "test-owner-nonexistent-repo"
    assert not expected_path.exists()
