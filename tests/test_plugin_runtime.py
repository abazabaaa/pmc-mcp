import json
import os
import socket
import subprocess
import sys
from pathlib import Path

import httpx2
import pytest

from pmc_mcp.domain.rules import DomainError
from pmc_mcp.plugin_runtime import endpoint_port


@pytest.mark.parametrize(
    "url",
    [
        "https://127.0.0.1:47836/mcp",
        "http://example.org:47836/mcp",
        "http://127.0.0.1:47836/other",
        "http://u:p@127.0.0.1:47836/mcp",
        "http://127.0.0.1:47836/mcp?x=1",
    ],
)
def test_foreign_helper_endpoints_are_rejected(url):
    with pytest.raises(DomainError, match="invalid_plugin_endpoint"):
        endpoint_port(url)


def test_owned_http_daemon_readiness_auth_restart_and_stop(tmp_path):
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    project = Path(__file__).resolve().parents[1]
    common = [
        "--state-dir",
        str(tmp_path / "state"),
        "--project-root",
        str(project),
        "--port",
        str(port),
    ]
    env = {k: v for k, v in os.environ.items() if k != "CLAUDE_CODE_MCP_SERVER_URL"}

    def run(command):
        return subprocess.run(
            [sys.executable, "-m", "pmc_mcp.cli", command, *common],
            capture_output=True,
            text=True,
            env=env,
            timeout=12,
        )

    try:
        first = run("ensure")
        assert first.returncode == 0, first.stderr
        headers = json.loads(first.stdout)
        with httpx2.Client(trust_env=False) as http:
            url = f"http://127.0.0.1:{port}/health"
            assert http.get(url).status_code == 401
            before = http.get(url, headers=headers).json()
            second = run("ensure")
            assert second.returncode == 0, second.stderr
            assert json.loads(second.stdout) == headers
            assert http.get(url, headers=headers).json()["pid"] == before["pid"]
            assert (
                http.get(url, headers={**headers, "Origin": "https://example.org"}).status_code
                == 403
            )
    finally:
        stopped = run("stop")
        assert stopped.returncode == 0, stopped.stderr
    with httpx2.Client(trust_env=False) as http, pytest.raises(httpx2.ConnectError):
        http.get(url)
    restarted = run("ensure")
    try:
        assert restarted.returncode == 0, restarted.stderr
        assert json.loads(restarted.stdout) == headers
    finally:
        assert run("stop").returncode == 0


def test_private_folder_configuration_rejects_tampering(tmp_path):
    from pmc_mcp.plugin_runtime import configure, configured_root

    state = tmp_path / "state"
    root = tmp_path / "downloads"
    root.mkdir()
    configure(state, root)
    assert configured_root(state) == str(root)
    settings = state / "folder.json"
    assert settings.stat().st_mode & 0o077 == 0
    settings.chmod(0o644)
    with pytest.raises(DomainError, match="plugin_not_configured"):
        configured_root(state)
    settings.unlink()
    settings.symlink_to(root)
    with pytest.raises(DomainError, match="plugin_not_configured"):
        configured_root(state)
