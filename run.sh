#!/usr/bin/env bash
set -euo pipefail

HOST="${HOST:-0.0.0.0}"
PORT="${PORT:-8080}"
BASE="http://127.0.0.1:${PORT}"
VENV_DIR="${VENV_DIR:-.venv}"
PY_BIN="${VENV_DIR}/bin/python"
PIP_BIN="${VENV_DIR}/bin/pip"
CLI_BIN="${VENV_DIR}/bin/polaris-pr-intel"
USE_UV="${USE_UV:-auto}"

have_uv() {
    command -v uv >/dev/null 2>&1
}

use_uv_runtime() {
    case "${USE_UV}" in
        1|true|yes|on) return 0 ;;
        0|false|no|off) return 1 ;;
        *) have_uv ;;
    esac
}

run_cli() {
    if use_uv_runtime; then
        uv run polaris-pr-intel "$@"
    elif [[ -x "${CLI_BIN}" ]]; then
        "${CLI_BIN}" "$@"
    else
        polaris-pr-intel "$@"
    fi
}

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
  bootstrap         Install dependencies (uv if available, else .venv)
  install           Install/sync project dependencies

Environment:
  HOST   Server bind address (default: 0.0.0.0)
  PORT   Server port (default: 8080)
  VENV_DIR  Virtual environment directory (default: .venv)
  USE_UV   auto|true|false (default: auto)
EOF
}

case "${1:-}" in
    serve)
        run_cli serve --host "$HOST" --port "$PORT"
        ;;
    run-daily)
        run_cli run-daily
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
    bootstrap)
        if use_uv_runtime; then
            uv sync
            echo "Bootstrapped environment with uv"
            echo "Run server with: uv run polaris-pr-intel serve --host ${HOST} --port ${PORT}"
        else
            python -m venv "${VENV_DIR}"
            "${PIP_BIN}" install -e .
            echo "Bootstrapped environment in ${VENV_DIR}"
            echo "Run server with: ${CLI_BIN} serve --host ${HOST} --port ${PORT}"
        fi
        ;;
    install)
        if use_uv_runtime; then
            uv sync
        else
            if [[ ! -x "${PIP_BIN}" ]]; then
                echo "Missing ${PIP_BIN}. Run './run.sh bootstrap' first."
                exit 1
            fi
            "${PIP_BIN}" install -e .
        fi
        ;;
    *)
        usage
        ;;
esac
