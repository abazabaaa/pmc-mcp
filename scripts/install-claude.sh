#!/bin/sh
set -eu
if [ "$#" -ne 1 ] || [ ! -d "$1" ]; then
    echo 'usage: install-claude.sh /absolute/existing/download-folder' >&2
    exit 1
fi
case "$1" in /*) ;; *) echo 'pmc-mcp: use an absolute folder' >&2; exit 1 ;; esac
plugin_root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
"$plugin_root/scripts/bootstrap.sh" prewarm
export UV_PROJECT_ENVIRONMENT="${PMCMCP_STATE_DIR:-$HOME/.local/state/pmc-mcp}/runtime"
claude plugin marketplace add "$plugin_root" --scope user
installed_plugins=$(claude plugin list --json)
if printf '%s' "$installed_plugins" | uv run --quiet --project "$plugin_root" --locked --no-dev python -c 'import json,sys; sys.exit(0 if any(p.get("id") == "pmc-mcp@pmc-local" and p.get("scope") == "user" for p in json.load(sys.stdin)) else 1)'; then
    claude plugin update pmc-mcp@pmc-local --scope user
    uv run --quiet --project "$plugin_root" --locked --no-dev python -c 'import json,sys; print(json.dumps({"output_root": sys.argv[1]}))' "$1" | claude plugin configure pmc-mcp@pmc-local --values-stdin
else
    claude plugin install pmc-mcp@pmc-local --scope user --config "output_root=$1"
fi
