"""Smart repository manager for PR Intelligence."""

from __future__ import annotations

import logging
import shutil
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)


class RepositoryManager:
    """Manages base repository location with smart defaults."""

    def __init__(
        self,
        owner: str,
        repo: str,
        token: str | None = None,
        explicit_path: str | None = None,
        cache_dir: str | None = None,
    ) -> None:
        """
        Initialize repository manager.

        Args:
            owner: GitHub owner/org (e.g., "apache")
            repo: Repository name (e.g., "polaris")
            token: GitHub token for cloning private repos
            explicit_path: Optional explicit path to use (overrides all auto-detection)
            cache_dir: Directory for auto-cloned repos (default: ~/.cache/pr-intel/repos)
        """
        self.owner = owner
        self.repo = repo
        self.token = token
        self.explicit_path = Path(explicit_path) if explicit_path else None

        if cache_dir:
            self.cache_dir = Path(cache_dir)
        else:
            self.cache_dir = Path.home() / ".cache" / "pr-intel" / "repos"

        # Lazy initialization - repo is only resolved when first accessed
        self._base_repo_path: Path | None = None

    def get_base_repo(self) -> Path:
        """
        Get base repository path, using smart detection and auto-cloning.

        This is lazy - the repository is only cloned/detected on first access.

        Priority:
        1. Explicit path if provided
        2. Current working directory if it's the right repo
        3. Cached clone (auto-clone if needed)

        Returns:
            Path to the base repository
        """
        # Return cached path if already resolved
        if self._base_repo_path is not None:
            return self._base_repo_path

        # 1. Explicit path wins
        if self.explicit_path:
            if not self.explicit_path.is_dir():
                raise RuntimeError(f"Explicit repo path does not exist: {self.explicit_path}")
            logger.info("Using explicit repo path: %s", self.explicit_path)
            self._base_repo_path = self.explicit_path
            return self._base_repo_path

        # 2. Check if CWD is the repo we're monitoring
        cwd = Path.cwd()
        if self._is_target_repo(cwd):
            logger.info("Auto-detected repo in current directory: %s", cwd)
            self._base_repo_path = cwd
            return self._base_repo_path

        # 3. Use cache (clone if needed)
        cached_path = self.cache_dir / f"{self.owner}-{self.repo}"

        if not cached_path.exists():
            logger.info("Repository not found in cache, cloning %s/%s...", self.owner, self.repo)
            self._clone_to_cache(cached_path)
        else:
            logger.info("Using cached repo: %s", cached_path)
            # Fetch updates to keep it fresh
            self._fetch_updates(cached_path)

        self._base_repo_path = cached_path
        return self._base_repo_path

    def _is_target_repo(self, path: Path) -> bool:
        """Check if a path is the repository we're monitoring."""
        if not path.is_dir():
            return False

        git_dir = path / ".git"
        if not git_dir.exists():
            return False

        try:
            # Get remote URL
            result = subprocess.run(
                ["git", "remote", "get-url", "origin"],
                cwd=path,
                capture_output=True,
                text=True,
                check=True,
                timeout=5,
            )
            remote_url = result.stdout.strip()

            # Check if it matches our repo
            # Handles both https://github.com/owner/repo and git@github.com:owner/repo
            repo_identifier = f"{self.owner}/{self.repo}"
            return repo_identifier in remote_url
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
            return False

    def _clone_to_cache(self, dest: Path) -> None:
        """Clone repository to cache directory."""
        dest.parent.mkdir(parents=True, exist_ok=True)

        # Build clone URL
        if self.token:
            # Use token for authentication
            clone_url = f"https://{self.token}@github.com/{self.owner}/{self.repo}.git"
        else:
            # Public repo or use system git credentials
            clone_url = f"https://github.com/{self.owner}/{self.repo}.git"

        logger.info("Cloning %s/%s to %s...", self.owner, self.repo, dest)

        try:
            # Don't use --depth 1 to allow worktrees for any branch
            subprocess.run(
                ["git", "clone", clone_url, str(dest)],
                check=True,
                capture_output=True,
                text=True,
                timeout=300,  # 5 minutes
            )
            logger.info("Clone completed successfully")
        except subprocess.CalledProcessError as e:
            # Clean up partial clone on failure
            if dest.exists():
                logger.warning("Cleaning up partial clone at %s", dest)
                shutil.rmtree(dest, ignore_errors=True)

            error_msg = e.stderr.strip() if e.stderr else str(e)
            # Don't expose token in error messages
            error_msg = error_msg.replace(self.token or "", "***") if self.token else error_msg
            raise RuntimeError(f"Failed to clone repository: {error_msg}") from e
        except subprocess.TimeoutExpired as e:
            # Clean up partial clone on timeout
            if dest.exists():
                logger.warning("Cleaning up partial clone after timeout at %s", dest)
                shutil.rmtree(dest, ignore_errors=True)
            raise RuntimeError(f"Clone timed out after {e.timeout} seconds") from e

    def _fetch_updates(self, repo_path: Path) -> None:
        """Fetch latest updates from remote."""
        try:
            logger.debug("Fetching updates for %s", repo_path)
            subprocess.run(
                ["git", "fetch", "origin"],
                cwd=repo_path,
                capture_output=True,
                text=True,
                check=True,
                timeout=60,
            )
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as e:
            # Don't fail if fetch fails - repo might still be usable
            logger.warning("Failed to fetch updates: %s", e)

    def fetch_pr_branch(self, pr_number: int) -> str:
        """
        Fetch a PR branch from GitHub.

        This fetches the PR's head ref into a local branch named pr-{number}.
        Works even if the PR branch was deleted after merge.

        Args:
            pr_number: Pull request number

        Returns:
            Local branch name (e.g., "pr-123")

        Raises:
            RuntimeError: If fetch fails
        """
        repo_path = self.get_base_repo()
        local_branch = f"pr-{pr_number}"

        logger.info("Fetching PR #%d branch to %s", pr_number, local_branch)

        # If the base repo is checked out at pr-{N}, fetching into that ref fails with
        # "refusing to fetch into branch ... checked out at ...". Detach HEAD first so
        # the fetch can overwrite the branch. The base repo is a review cache, never a
        # working copy for the user.
        try:
            head = subprocess.run(
                ["git", "symbolic-ref", "--short", "HEAD"],
                cwd=repo_path,
                capture_output=True,
                text=True,
                timeout=5,
            )
            if head.returncode == 0 and head.stdout.strip() == local_branch:
                logger.info(
                    "Base repo is on %s; detaching HEAD so fetch can overwrite the branch",
                    local_branch,
                )
                subprocess.run(
                    ["git", "switch", "--detach"],
                    cwd=repo_path,
                    capture_output=True,
                    text=True,
                    timeout=10,
                    check=False,
                )
        except subprocess.TimeoutExpired:
            pass

        try:
            # Fetch the PR ref from GitHub
            # GitHub exposes PRs as refs/pull/{number}/head
            # Use --force to overwrite if branch already exists from previous review
            subprocess.run(
                [
                    "git",
                    "fetch",
                    "origin",
                    f"pull/{pr_number}/head:{local_branch}",
                    "--force",
                ],
                cwd=repo_path,
                capture_output=True,
                text=True,
                check=True,
                timeout=60,
            )
            logger.info("Successfully fetched PR #%d to branch %s", pr_number, local_branch)

            # Verify the branch was created
            verify_result = subprocess.run(
                ["git", "rev-parse", "--verify", local_branch],
                cwd=repo_path,
                capture_output=True,
                text=True,
                timeout=5,
            )
            if verify_result.returncode != 0:
                raise RuntimeError(f"Branch {local_branch} was not created after fetch")

            return local_branch
        except subprocess.CalledProcessError as e:
            error_msg = e.stderr.strip() if e.stderr else str(e)
            raise RuntimeError(f"Failed to fetch PR #{pr_number}: {error_msg}") from e
        except subprocess.TimeoutExpired as e:
            raise RuntimeError(f"Fetch PR #{pr_number} timed out after {e.timeout} seconds") from e
