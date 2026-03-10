#!/usr/bin/env bash
set -euo pipefail

HOST="${HOST:-0.0.0.0}"
PORT="${PORT:-8080}"
BASE="http://127.0.0.1:${PORT}"

usage() {
    cat <<EOF
Usage: ./run.sh <command> [args]

Commands:
  serve             Start the API server
  run-daily         Generate one daily report via CLI
  sync              Sync recent open PRs/issues
  sync-all          Sync all open PRs/issues
  report            Generate and print daily report
  review <PR>       Run async review for a PR
  review-sync <PR>  Run sync review for a PR (wait for result)
  install           Install package in editable mode

Environment:
  HOST   Server bind address (default: 0.0.0.0)
  PORT   Server port (default: 8080)
EOF
}

case "${1:-}" in
    serve)
        polaris-pr-intel serve --host "$HOST" --port "$PORT"
        ;;
    run-daily)
        polaris-pr-intel run-daily
        ;;
    sync)
        curl -s -X POST "$BASE/sync/recent" | python -m json.tool
        ;;
    sync-all)
        curl -s -X POST "$BASE/sync/all-open?per_page=100&max_pages=20" | python -m json.tool
        ;;
    report)
        curl -s -X POST "$BASE/reports/daily/run" > /dev/null
        curl -s "$BASE/reports/daily/latest.md"
        ;;
    review)
        [[ -z "${2:-}" ]] && echo "Usage: ./run.sh review <PR_NUMBER>" && exit 1
        curl -s -X POST "$BASE/reviews/pr/$2/run" | python -m json.tool
        ;;
    review-sync)
        [[ -z "${2:-}" ]] && echo "Usage: ./run.sh review-sync <PR_NUMBER>" && exit 1
        curl -s -X POST "$BASE/reviews/pr/$2/run?wait=true" | python -m json.tool
        ;;
    install)
        pip install -e .
        ;;
    *)
        usage
        ;;
esac
