#!/bin/sh
# stdout is a credential channel for ensure; keep diagnostics on stderr.
set -eu
umask 077
plugin_root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
export PATH="$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:$PATH"
if ! command -v uv >/dev/null 2>&1; then
    echo 'pmc-mcp: install uv before setting up the local plugin' >&2
    exit 1
fi
plugin_state=${PMCMCP_STATE_DIR:-$HOME/.local/state/pmc-mcp}
mkdir -p "$plugin_state"
if [ -L "$plugin_state" ] || [ ! -O "$plugin_state" ]; then
    echo 'pmc-mcp: unsafe local state directory' >&2
    exit 1
fi
export UV_PROJECT_ENVIRONMENT="$plugin_state/runtime"
mode=${1:-ensure}
shift || true
case "$mode" in
    prewarm) exec uv sync --project "$plugin_root" --locked --no-dev "$@" >&2 ;;
    ensure|stop) exec uv run --quiet --project "$plugin_root" --locked --no-dev pmc-mcp "$mode" --state-dir "$plugin_state" --project-root "$plugin_root" "$@" ;;
    *) echo 'pmc-mcp: unsupported bootstrap action' >&2; exit 1 ;;
esac
