  # Worktree Mode (Default)

## Overview

PR Intelligence uses git worktrees by default for isolated PR reviews. This provides:

- **Isolation**: Each PR review runs in its own dedicated worktree
- **Parallelism**: Multiple PRs can be reviewed simultaneously without conflicts
- **Correct Context**: Each review runs with the actual PR branch/commit checked out
- **Automatic Cleanup**: Worktrees are created and removed automatically
- **Zero Configuration**: Works automatically with smart repository detection

## How It Works

### Traditional Mode (default)
```
LOCAL_REVIEW_REPO_DIR=/path/to/repo
                      │
                      └─ All reviews run here
```

### Worktree Mode
```
LOCAL_REVIEW_REPO_DIR=/path/to/repo (base)
                      │
                      ├─ .worktrees/
                      │   ├─ pr-123/  ← Review for PR #123
                      │   ├─ pr-456/  ← Review for PR #456
                      │   └─ pr-789/  ← Review for PR #789
```

## Quick Start

### 1. Minimal Setup (Recommended)

```bash
export PR_INTEL_GITHUB_TOKEN=your_token
# That's it! System auto-detects or clones the repo
```

### 2. Start the Service

```bash
./run.sh serve
```

### 3. Review PRs

When you trigger a PR review, the system will automatically:
1. Find or clone the base repository
2. Create a worktree at `.worktrees/pr-{number}`
3. Checkout the appropriate branch/commit
4. Run the review in that worktree
5. Clean up the worktree after completion

## Configuration Options

### Basic (Auto Mode - Recommended)

No configuration needed! The system:
- Auto-detects if you're in the repo directory
- Or clones to `~/.cache/pr-intel/repos/{owner}-{repo}`
- Creates worktrees automatically

### Advanced Overrides

```bash
# Override repository location
export GIT_REPO_PATH=/custom/path/to/repo

# Custom cache location for auto-cloned repos
export REPO_CACHE_DIR=/custom/cache/dir

# Disable worktrees (not recommended)
export USE_WORKTREES=false

# Custom worktree location
export WORKTREE_BASE_DIR=/tmp/pr-worktrees
```

## Example Configuration

### Zero Config (Recommended)
```bash
# Just set your token - everything else is automatic!
export PR_INTEL_GITHUB_TOKEN=xxx
export GITHUB_OWNER=apache
export GITHUB_REPO=polaris

./run.sh serve

# System automatically:
# 1. Detects or clones apache/polaris
# 2. Creates worktrees in .worktrees/pr-*/
# 3. Cleans up after each review
```

### Custom Repository Location
```bash
# Use your existing local clone
export PR_INTEL_GITHUB_TOKEN=xxx
export GITHUB_OWNER=apache
export GITHUB_REPO=polaris
export GIT_REPO_PATH=/Users/you/code/polaris

# Worktrees will be created at:
# /Users/you/code/polaris/.worktrees/pr-*/
```

### Custom Worktree Location
```bash
# Keep worktrees separate from the repo
export PR_INTEL_GITHUB_TOKEN=xxx
export WORKTREE_BASE_DIR=/tmp/pr-worktrees

# Worktrees will be created at:
# /tmp/pr-worktrees/pr-*/
```

## Manual Worktree Management

You can also use the WorktreeManager directly in Python:

```python
from polaris_pr_intel.git.worktree_manager import WorktreeManager

# Create manager
manager = WorktreeManager(
    base_repo_path="/path/to/repo",
    worktree_base_dir="/path/to/worktrees",
    auto_cleanup=True,
)

# Create worktree for PR #123
ctx = manager.create_worktree_for_pr(123)
print(f"Worktree at: {ctx.path}")

# List all worktrees
worktrees = manager.list_worktrees()
print(f"Active: {worktrees}")

# Remove specific worktree
manager.remove_worktree(123)

# Cleanup all worktrees
manager.cleanup_all()
```

See `examples/worktree_demo.py` for a complete example.

## Benefits

### Concurrent Reviews
With worktree mode, you can review multiple PRs simultaneously:
- Each PR gets its own isolated environment
- No conflicts between different PR branches
- Parallel review execution is safe

### Accurate Context
Each review runs with the actual PR code checked out:
- Claude Code CLI sees the real PR changes
- File paths and line numbers match the PR exactly
- No confusion from having a different branch checked out

### Clean Isolation
Worktrees are automatically managed:
- Created on-demand for each review
- Cleaned up after completion
- No leftover state between reviews

## Limitations

1. **Git Repository Required**: `LOCAL_REVIEW_REPO_DIR` must be a valid git repository
2. **Disk Space**: Each worktree uses disk space (typically small, as git uses hardlinks)
3. **Network Access**: If PRs reference branches not yet fetched, you may need to run `git fetch` in the base repo periodically

## Troubleshooting

### "Failed to create worktree"
Ensure your base repository is up to date:
```bash
cd $LOCAL_REVIEW_REPO_DIR
git fetch origin
```

### Stale Worktrees
If worktrees aren't being cleaned up:
```bash
# List all worktrees
cd $LOCAL_REVIEW_REPO_DIR
git worktree list

# Remove stale worktrees
git worktree prune

# Or manually remove specific worktree
git worktree remove .worktrees/pr-123 --force
```

### Permission Issues
Ensure the worktree base directory is writable:
```bash
mkdir -p $WORKTREE_BASE_DIR
chmod 755 $WORKTREE_BASE_DIR
```

## Comparison with Traditional Mode

| Feature | Traditional Mode | Worktree Mode (Default) |
|---------|-----------------|-------------------------|
| Concurrent Reviews | ❌ Risky (shared state) | ✅ Safe (isolated) |
| Correct Branch | ❌ Manual checkout needed | ✅ Automatic |
| Configuration | ⚠️ Manual repo setup | ✅ Auto-managed |
| Disk Usage | ✅ Single checkout | ⚠️ Multiple worktrees |
| Setup | ⚠️ Manual clone required | ✅ Automatic |
| Cleanup | ⚠️ Manual management | ✅ Automatic |

## When to Use

**Worktree Mode (Default):** Best for almost everyone
- ✅ Zero configuration
- ✅ Safe parallel reviews
- ✅ Accurate per-PR context
- ✅ Automatic management

**Traditional Mode:** Only for specific cases
- You need to manually control git state
- Disk space is extremely constrained
- You have a custom git workflow

To disable worktrees:
```bash
export USE_WORKTREES=false
export GIT_REPO_PATH=/your/single/directory
```
