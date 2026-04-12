# Troubleshooting

Common issues and solutions for PR Intelligence.

## Configuration Issues

### Missing GitHub Token

**Error:**
```
RuntimeError: PR_INTEL_GITHUB_TOKEN or GITHUB_TOKEN is required
```

**Solution:**
```bash
export PR_INTEL_GITHUB_TOKEN=your_token_here
./run.sh serve
```

### Missing Local Repository Directory

**Error:**
```
RuntimeError: LOCAL_REVIEW_REPO_DIR must be set for claude_code_local provider
```

**Solution:**
When using `claude_code_local` or `codex_local`, you must set:
```bash
export LOCAL_REVIEW_REPO_DIR=/path/to/your/local/repo
```

This directory should be a git clone of the repository you're monitoring.

## Server Issues

### Port Already in Use

**Error:**
```
ERROR: [Errno 48] Address already in use
```

**Solution:**
Change the port:
```bash
PORT=9090 ./run.sh serve
```

Or find and kill the process using the port:
```bash
lsof -i :8080
kill <pid>
```

### Server Won't Start

**Checklist:**
1. Ensure all required environment variables are set
2. Check that the port is not in use
3. Verify Python version is 3.11+
4. Check that dependencies are installed: `./run.sh bootstrap`
5. Review server logs for specific errors

## Database Issues

### SQLite Database Locked

**Symptoms:**
```
sqlite3.OperationalError: database is locked
```

**Solution:**
Ensure only one server instance is running. Check for zombie processes:
```bash
ps aux | grep polaris-pr-intel
kill <pid>
```

If the problem persists, you may need to wait for locks to release or restart.

### Database Corruption

**Solution:**
If the database is corrupted, you can delete it and let it be recreated:
```bash
rm .data/polaris_pr_intel.db
./run.sh serve
./run.sh refresh  # Repopulate data
```

## Review Job Issues

### Review Jobs Timeout

**Error:**
```
Job timeout after 1200s
```

**Solution:**
Increase timeout for large PRs:
```bash
export REVIEW_JOB_TIMEOUT_SEC=2400  # 40 minutes
```

### Review Jobs Stuck in Queue

**Check:**
1. Verify the server is running
2. Check worker count: `REVIEW_JOB_WORKERS` (default: 1)
3. Look for errors in server logs
4. Check if jobs are timing out

**Solution:**
Increase worker count for parallel processing:
```bash
export REVIEW_JOB_WORKERS=3
```

## LLM Provider Issues

### Claude Code / Codex Command Not Found

**Error:**
```
Command 'claude' not found
```
or
```
Command 'codex' not found
```

**Solution:**
1. Ensure Claude Code or Codex CLI is installed
2. Verify it's in your PATH:
   ```bash
   which claude
   which codex
   ```
3. Or specify the full path:
   ```bash
   export CLAUDE_CODE_CMD=/path/to/claude
   export CODEX_CMD=/path/to/codex
   ```

### LLM Provider Errors

**General Troubleshooting:**
1. Verify your API keys are set correctly
2. Check that CLI tools are in PATH for local providers
3. Review logs for detailed error messages
4. Try the rule-based provider for testing:
   ```bash
   export LLM_PROVIDER=heuristic
   ```

### Self-Review Taking Too Long

**Symptoms:**
```
Step 2/3: Critiquing initial findings for PR #123
(hangs for minutes)
```

**Explanation:**
Self-review makes 3 LLM calls instead of 1. Expected latency is ~3x baseline.

**Solutions:**
1. Increase timeout if needed:
   ```bash
   export REVIEW_JOB_TIMEOUT_SEC=2400
   ```
2. Or disable self-review:
   ```bash
   export ENABLE_SELF_REVIEW=false
   ```

### Self-Review Falling Back to Initial Findings

**Warning:**
```
[WARNING] Step 2 (critique) failed, using initial findings
```

**Explanation:**
This is expected behavior when critique/revision fails (LLM error, timeout, parse failure). The review still completes successfully with initial findings.

**What to check:**
1. Check logs for specific error details
2. If persistent, disable self-review or adjust timeout
3. Verify LLM provider is working correctly

## Webhook Issues

### Webhook Signature Verification Fails

**Error:**
```
Webhook signature verification failed
```

**Solution:**
Ensure `GITHUB_WEBHOOK_SECRET` matches your GitHub webhook configuration:
```bash
export GITHUB_WEBHOOK_SECRET=your_secret_here
```

### Webhooks Not Triggering

**Checklist:**
1. Verify webhook is configured in GitHub repository settings
2. Check that the URL points to your server: `http://your-server:8080/webhooks/github`
3. Ensure server is accessible from GitHub (not behind firewall)
4. Check GitHub webhook delivery logs for errors
5. Review server logs for incoming webhook requests

## Data Issues

### Stale Data

**Symptoms:**
Old PRs still showing as open, scores not updating

**Solution:**
Run a full refresh:
```bash
./run.sh refresh
```

This will:
- Sync open PRs/issues from GitHub
- Prune stale locally-open PRs
- Recompute all scores
- Generate fresh analysis

### Missing Data After Restart

**If using in-memory storage:**
Data is ephemeral and will be lost on restart. Switch to SQLite:
```bash
export STORE_BACKEND=sqlite
```

**If using SQLite:**
Check that the database file exists and is readable:
```bash
ls -la .data/polaris_pr_intel.db
```

## Debug Mode

### Enable Verbose Logging

The service automatically logs at INFO level. Check application logs when running the server.

**What's logged:**
- LLM provider at startup
- Each CLI LLM invocation
- Webhook events received
- Review job status changes
- Errors and warnings

**View logs:**
```bash
./run.sh serve 2>&1 | tee server.log
```

## Performance Issues

### Slow Response Times

**Common causes:**
1. **LLM calls** - Local CLI providers make synchronous LLM calls
   - Solution: Use async review jobs for deep analysis
2. **Large PRs** - Big diffs take longer to analyze
   - Solution: Increase timeouts or reduce scope
3. **Database locks** - Multiple concurrent writes
   - Solution: Ensure single server instance

### High Memory Usage

**Common causes:**
1. **In-memory storage** - All data kept in RAM
   - Solution: Switch to SQLite: `export STORE_BACKEND=sqlite`
2. **Large analysis results** - Many PRs with detailed reviews
   - Solution: Normal, but consider pruning old data periodically

## Getting Help

### Collect Debug Information

When reporting issues, include:
1. Error messages from logs
2. Environment variables (sanitize tokens!)
3. Python version: `python --version`
4. Installed packages: `pip list | grep -E '(fastapi|langgraph|httpx|pydantic)'`
5. Steps to reproduce

### Check Existing Issues

Before reporting, check if others have encountered the same problem:
- Review closed issues for solutions
- Search discussions for similar topics

### File a Bug Report

If you've found a bug, please file an issue with:
- Clear description of the problem
- Steps to reproduce
- Expected vs actual behavior
- Debug information (see above)
