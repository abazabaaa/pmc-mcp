#!/bin/sh
set -eu
if [ "$#" -ne 1 ] || [ ! -d "$1" ]; then
    echo 'usage: install-claude.sh /absolute/existing/download-folder' >&2
    exit 1
fi
case "$1" in /*) ;; *) echo 'pmc-mcp: use an absolute folder' >&2; exit 1 ;; esac
plugin_root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
"$plugin_root/scripts/bootstrap.sh" prewarm
claude plugin marketplace add "$plugin_root" --scope user
claude plugin install pmc-mcp@pmc-local --scope user --config "output_root=$1"
