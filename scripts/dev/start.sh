#!/usr/bin/env bash
# Starts the Insider Signal web dashboard in development.
#
# The Python pipeline runs on GitHub Actions, not locally. The only thing to run
# on your machine is the Next.js app in web/, which reads the same Neon database.
#
# Run with: ./scripts/dev/start.sh  (or: bash scripts/dev/start.sh)

set -uo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
web_dir="$root/web"
port=3000

port_up() {
    curl -sf -o /dev/null --max-time 1 "http://localhost:${port}" 2>/dev/null
}

open_url() {
    if command -v cmd.exe >/dev/null 2>&1; then cmd.exe /c start "" "$1" >/dev/null 2>&1
    elif command -v open >/dev/null 2>&1; then open "$1"
    elif command -v xdg-open >/dev/null 2>&1; then xdg-open "$1" >/dev/null 2>&1
    else echo "Open $1 in your browser."
    fi
}

echo "Insider Signal - web dashboard"
echo "------------------------------"

if ! command -v pnpm >/dev/null 2>&1; then
    echo "ERROR: 'pnpm' is not installed or not on PATH. Run 'corepack enable' or install from https://pnpm.io/" >&2
    exit 1
fi

if port_up; then
    echo "Something is already serving http://localhost:${port} - opening it and leaving it alone."
    open_url "http://localhost:${port}"
    exit 0
fi

# --- .env.local -------------------------------------------------------------
if [ ! -f "$web_dir/.env.local" ]; then
    if [ -f "$root/.env" ]; then
        echo "Creating web/.env.local with DATABASE_URL from the repo-root .env"
        grep '^DATABASE_URL=' "$root/.env" >"$web_dir/.env.local"
    else
        echo "ERROR: web/.env.local is missing and there is no repo-root .env to copy DATABASE_URL from." >&2
        echo "Create web/.env.local with a single line:  DATABASE_URL=<your Neon connection string>" >&2
        exit 1
    fi
fi

# --- dependencies ----------------------------------------------------------
if [ ! -d "$web_dir/node_modules" ]; then
    echo "Installing dependencies (first run only)..."
    (cd "$web_dir" && pnpm install)
fi

# --- dev server ----------------------------------------------------------
echo "Starting Next.js dev server (http://localhost:${port})..."
dev_pid=""
cleanup() { [ -n "$dev_pid" ] && kill "$dev_pid" 2>/dev/null; }
trap cleanup EXIT INT TERM

(cd "$web_dir" && exec pnpm dev) &
dev_pid=$!

echo "Waiting for the server to come up..."
ready=false
for _ in $(seq 1 60); do
    port_up && ready=true && break
    sleep 1
done

if [ "$ready" = true ]; then
    open_url "http://localhost:${port}"
    echo ""
    echo "Dashboard is up. Press Ctrl+C to stop the server."
    wait "$dev_pid"
else
    echo "Server didn't respond within 60s - check the output above for errors."
fi
