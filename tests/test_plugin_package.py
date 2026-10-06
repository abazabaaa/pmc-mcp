import json
import runpy
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def test_both_hosts_declare_http_and_no_embedded_credentials():
    assert not (ROOT / ".mcp.json").exists(), "project discovery shadows plugin authentication"
    manifest = json.loads((ROOT / ".claude-plugin/plugin.json").read_text())
    assert manifest["mcpServers"] == "./claude.mcp.json"
    claude = json.loads((ROOT / "claude.mcp.json").read_text())["mcpServers"]["pmc"]
    codex = json.loads((ROOT / "codex.mcp.json").read_text())["mcpServers"]["pmc"]
    assert claude["type"] == "http"
    assert claude["url"] == codex["url"] == "http://127.0.0.1:47836/mcp"
    assert "command" not in claude and "command" not in codex
    assert claude["headers"]["X-PMCMCP-Output-Root"] == "${user_config.output_root}"
    assert "Authorization" not in claude["headers"]
    assert codex["http_headers_helper"] == "pmc-mcp-plugin-headers"


def test_bundle_allowlist_excludes_private_and_generated_material():
    packager = runpy.run_path(str(ROOT / "scripts/package_plugin.py"))
    names = {str(p.relative_to(ROOT)) for p in packager["files"](ROOT)}
    assert all((ROOT / n).is_file() for n in names)
    assert not any(
        n.startswith((".git/", ".claude/", "downloads/", "reviews/", "docs-refresh/", ".venv/"))
        for n in names
    )
    assert not {"PLAN.md", "OBSERVATIONS.md", ".env", "service.key", "bearer.key"} & names
    assert {".claude-plugin/plugin.json", ".codex-plugin/plugin.json", "uv.lock"} <= names


@pytest.mark.skipif(shutil.which("claude") is None, reason="Claude Code is not installed in CI")
def test_installed_claude_authoritative_manifest_validation():
    for path in [ROOT / ".claude-plugin/plugin.json", ROOT]:
        result = subprocess.run(
            ["claude", "plugin", "validate", str(path), "--strict", "--json"],
            text=True,
            capture_output=True,
            check=True,
        )
        assert json.loads(result.stdout)["success"]
