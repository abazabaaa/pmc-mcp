#!/bin/sh
set -eu
umask 077
if [ "$#" -ne 1 ] || [ ! -d "$1" ]; then
    echo 'usage: install-codex.sh /absolute/existing/download-folder' >&2
    exit 1
fi
case "$1" in /*) ;; *) echo 'pmc-mcp: use an absolute folder' >&2; exit 1 ;; esac
plugin_root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
"$plugin_root/scripts/bootstrap.sh" prewarm
plugin_state=${PMCMCP_STATE_DIR:-$HOME/.local/state/pmc-mcp}
export UV_PROJECT_ENVIRONMENT="$plugin_state/runtime"
uv run --quiet --project "$plugin_root" --locked --no-dev pmc-mcp configure --state-dir "$plugin_state" --output-root "$1"
installed_root="$HOME/.local/share/pmc-mcp/plugin"
uv run --quiet --project "$plugin_root" --locked --no-dev python "$plugin_root/scripts/package_plugin.py" --directory "$installed_root"
mkdir -p "$HOME/.local/bin"
cat > "$HOME/.local/bin/pmc-mcp-plugin-headers" <<'WRAPPER'
#!/bin/sh
exec "$HOME/.local/share/pmc-mcp/plugin/scripts/bootstrap.sh" ensure --configured-root
WRAPPER
chmod 700 "$HOME/.local/bin/pmc-mcp-plugin-headers"
export PATH="$HOME/.local/bin:$PATH"
codex plugin marketplace add "$installed_root"
codex plugin add pmc-mcp@pmc-local --json
