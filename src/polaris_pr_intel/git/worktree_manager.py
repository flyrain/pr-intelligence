"""Git worktree manager for isolated PR reviews."""

from __future__ import annotations

import logging
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class WorktreeContext:
    """Context for a PR worktree."""

    pr_number: int
    path: Path
    branch: str | None = None

    def __enter__(self) -> Path:
        return self.path

    def __exit__(self, *args: Any) -> None:
        pass  # Cleanup handled by WorktreeManager


class WorktreeManager:
    """Manages git worktrees for PR reviews."""

    def __init__(
        self,
        base_repo_path: str | Path,
        worktree_base_dir: str | Path | None = None,
        auto_cleanup: bool = True,
    ) -> None:
        """
        Initialize worktree manager.

        Args:
            base_repo_path: Path to the main git repository
            worktree_base_dir: Directory where worktrees will be created (default: base_repo/.worktrees)
            auto_cleanup: Whether to automatically remove worktrees after use
        """
        self.base_repo = Path(base_repo_path).resolve()
        if not self.base_repo.is_dir():
            raise ValueError(f"Repository path does not exist: {base_repo_path}")

        if worktree_base_dir:
            self.worktree_base = Path(worktree_base_dir).resolve()
        else:
            self.worktree_base = self.base_repo / ".worktrees"

        self.auto_cleanup = auto_cleanup
        self.worktree_base.mkdir(parents=True, exist_ok=True)

    def _run_git(self, args: list[str], cwd: Path | None = None) -> subprocess.CompletedProcess:
        """Run a git command."""
        cmd = ["git", *args]
        return subprocess.run(
            cmd,
            check=True,
            capture_output=True,
            text=True,
            cwd=cwd or self.base_repo,
        )

    def create_worktree_for_pr(
        self,
        pr_number: int,
        branch: str | None = None,
        commit: str | None = None,
    ) -> WorktreeContext:
        """
        Create a worktree for PR review.

        Args:
            pr_number: PR number
            branch: Branch name to checkout (e.g., "origin/feature-branch")
            commit: Specific commit SHA to checkout (alternative to branch)

        Returns:
            WorktreeContext with the path to the worktree
        """
        worktree_path = self.worktree_base / f"pr-{pr_number}"

        # Remove existing worktree if it exists
        if worktree_path.exists():
            logger.info("Removing existing worktree for PR #%d at %s", pr_number, worktree_path)
            self.remove_worktree(pr_number)

        # Determine what to checkout
        checkout_ref = commit or branch
        if not checkout_ref:
            # Default to main/master HEAD
            try:
                result = self._run_git(["symbolic-ref", "refs/remotes/origin/HEAD"])
                checkout_ref = result.stdout.strip().split("/")[-1]
            except subprocess.CalledProcessError:
                checkout_ref = "HEAD"

        logger.info("Creating worktree for PR #%d at %s (ref: %s)", pr_number, worktree_path, checkout_ref)

        # Create the worktree
        cmd = ["worktree", "add", str(worktree_path)]
        if checkout_ref:
            cmd.append(checkout_ref)

        try:
            self._run_git(cmd)
        except subprocess.CalledProcessError as e:
            logger.error("Failed to create worktree: %s", e.stderr)
            raise RuntimeError(f"Failed to create worktree for PR #{pr_number}: {e.stderr}") from e

        return WorktreeContext(pr_number=pr_number, path=worktree_path, branch=branch)

    def remove_worktree(self, pr_number: int) -> None:
        """Remove a worktree for a PR."""
        worktree_path = self.worktree_base / f"pr-{pr_number}"

        if not worktree_path.exists():
            logger.debug("Worktree for PR #%d does not exist, skipping removal", pr_number)
            return

        logger.info("Removing worktree for PR #%d at %s", pr_number, worktree_path)

        try:
            # Try git worktree remove first
            self._run_git(["worktree", "remove", str(worktree_path), "--force"])
        except subprocess.CalledProcessError:
            # Fallback to manual cleanup
            logger.warning("Git worktree remove failed, falling back to manual cleanup")
            try:
                shutil.rmtree(worktree_path)
                # Clean up worktree metadata
                self._run_git(["worktree", "prune"])
            except Exception as e:
                logger.error("Failed to remove worktree directory: %s", e)

    def cleanup_all(self) -> None:
        """Remove all worktrees."""
        if not self.worktree_base.exists():
            return

        for worktree_dir in self.worktree_base.iterdir():
            if worktree_dir.is_dir() and worktree_dir.name.startswith("pr-"):
                try:
                    pr_num = int(worktree_dir.name.split("-")[1])
                    self.remove_worktree(pr_num)
                except (ValueError, IndexError):
                    logger.warning("Skipping invalid worktree directory: %s", worktree_dir)

    def list_worktrees(self) -> list[int]:
        """List all PR worktrees."""
        if not self.worktree_base.exists():
            return []

        pr_numbers = []
        for worktree_dir in self.worktree_base.iterdir():
            if worktree_dir.is_dir() and worktree_dir.name.startswith("pr-"):
                try:
                    pr_num = int(worktree_dir.name.split("-")[1])
                    pr_numbers.append(pr_num)
                except (ValueError, IndexError):
                    pass

        return sorted(pr_numbers)
