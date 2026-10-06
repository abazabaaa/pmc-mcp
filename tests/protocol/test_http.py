import asyncio
import hashlib
import socket
import subprocess
import sys
from contextlib import asynccontextmanager
from pathlib import Path

import httpx2
import pytest
from mcp import Client
from mcp.client.streamable_http import streamable_http_client

BEARER = "fixture-only-credential"


@asynccontextmanager
async def process(root, *, slow=False, header_grants=False):
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    command = [
        sys.executable,
        str(Path(__file__).with_name("fixture_server.py")),
        "--port",
        str(port),
        "--root",
        str(root),
    ]
    if slow:
        command.append("--slow")
    if header_grants:
        command.append("--header-grants")
    with (root / "server.log").open("wb") as log:
        child = subprocess.Popen(command, stdout=log, stderr=log)
        try:
            url = f"http://127.0.0.1:{port}/mcp"
            async with httpx2.AsyncClient(trust_env=False) as http:
                for _ in range(100):
                    if child.poll() is not None:
                        pytest.fail((root / "server.log").read_text())
                    try:
                        response = await http.get(url)
                        if response.status_code == 401:
                            break
                    except httpx2.TransportError:
                        pass
                    await asyncio.sleep(0.05)
                else:
                    pytest.fail("fixture failed to start")
            yield url
        finally:
            child.terminate()
            try:
                child.wait(timeout=5)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait(timeout=5)


@asynccontextmanager
async def connect(url, *, mode="auto", observed=None, headers=None):
    async def observe(response):
        if observed is not None:
            observed.append((response.status_code, dict(response.headers)))

    async with (
        httpx2.AsyncClient(
            headers={"Authorization": f"Bearer {BEARER}", **(headers or {})},
            trust_env=False,
            event_hooks={"response": [observe]},
        ) as http,
        Client(streamable_http_client(url, http_client=http), mode=mode) as client,
    ):
        yield client


@pytest.mark.parametrize("mode", ["auto", "legacy"])
async def test_wire_schemas_and_verified_download(tmp_path, mode):
    observed = []
    async with process(tmp_path) as url, connect(url, mode=mode, observed=observed) as client:
        assert client.session.protocol_version == ("2026-07-28" if mode == "auto" else "2025-11-25")
        tools = (await client.list_tools()).tools
        assert {t.name for t in tools} == {"search_artifacts", "download_artifacts"}
        search_tool = next(t for t in tools if t.name == "search_artifacts")
        assert search_tool.annotations.read_only_hint is True
        download_tool = next(t for t in tools if t.name == "download_artifacts")
        assert download_tool.annotations.read_only_hint is False
        assert download_tool.annotations.destructive_hint is False
        assert download_tool.output_schema is not None
        result = await client.call_tool("search_artifacts", {"query": "PMC123.2"})
        assert not result.is_error
        choice = result.structured_content["choices"][0]
        assert choice["artifact"]["retracted"] is True
        assert choice["availability"] == "advertised"
        assert not list(tmp_path.glob("*.pdf"))
        downloaded = await client.call_tool(
            "download_artifacts",
            {
                "selection_tokens": [choice["selection_token"]],
                "output_dir": str(tmp_path),
            },
        )
        assert not downloaded.is_error
        receipt = downloaded.structured_content["receipts"][0]
        assert receipt["status"] == "downloaded"
        body = Path(receipt["path"]).read_bytes()
        assert hashlib.sha256(body).hexdigest() == receipt["sha256"]
        assert Path(receipt["path"] + ".receipt.json").is_file()
    assert observed
    assert all("mcp-session-id" not in headers for _, headers in observed)


async def test_selection_survives_process_restart(tmp_path):
    async with process(tmp_path) as url, connect(url) as client:
        result = await client.call_tool("search_artifacts", {"query": "PMC123.2"})
        token = result.structured_content["choices"][0]["selection_token"]
    assert not list(tmp_path.glob("*.pdf"))
    async with process(tmp_path) as url, connect(url) as client:
        arguments = {"selection_tokens": [token], "output_dir": str(tmp_path)}
        first = await client.call_tool("download_artifacts", arguments)
        assert first.structured_content["receipts"][0]["status"] == "downloaded"
    async with process(tmp_path) as url, connect(url) as client:
        again = await client.call_tool("download_artifacts", arguments)
        assert again.structured_content["receipts"][0]["status"] == "already_present"


async def test_auth_origin_host_and_tampered_selection(tmp_path):
    async with process(tmp_path) as url:
        async with httpx2.AsyncClient(trust_env=False) as http:
            assert (await http.post(url, json={})).status_code == 401
            headers = {"Authorization": f"Bearer {BEARER}"}
            hostile_origin = await http.post(
                url, headers={**headers, "Origin": "https://example.org"}, json={}
            )
            assert hostile_origin.status_code == 403
            hostile_host = await http.post(url, headers={**headers, "Host": "example.org"}, json={})
            assert hostile_host.status_code == 421
        async with connect(url) as client:
            result = await client.call_tool(
                "download_artifacts",
                {
                    "selection_tokens": ["tampered"],
                    "output_dir": str(tmp_path),
                },
            )
            assert result.structured_content["receipts"][0]["status"] == "failed"
            assert not list(tmp_path.glob("*.pdf"))


async def wait_for(path, seconds=5):
    async with asyncio.timeout(seconds):
        while not path.exists():
            await asyncio.sleep(0.02)


@pytest.mark.parametrize("mode", ["auto", "legacy"])
async def test_modern_cancellation_and_legacy_completion(tmp_path, mode):
    async with process(tmp_path, slow=True) as url, connect(url, mode=mode) as client:
        choices = await client.call_tool("search_artifacts", {"query": "PMC123.2"})
        token = choices.structured_content["choices"][0]["selection_token"]
        task = asyncio.create_task(
            client.call_tool(
                "download_artifacts",
                {
                    "selection_tokens": [token],
                    "output_dir": str(tmp_path),
                },
            )
        )
        await wait_for(tmp_path / "started")
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        await wait_for(tmp_path / "closed")
        if mode == "auto":
            assert not (tmp_path / "finished").exists()
            assert not list(tmp_path.glob("*.pdf"))
        else:
            assert (tmp_path / "finished").exists()
            assert len(list(tmp_path.glob("*.pdf"))) == 1
        assert not list(tmp_path.glob(".pmc-*"))


@pytest.mark.parametrize("mode", ["auto", "legacy"])
async def test_human_header_grants_remain_request_scoped(tmp_path, mode):
    a = tmp_path / "folder A"
    b = tmp_path / "folder B"
    a.mkdir()
    b.mkdir()
    async with process(tmp_path, slow=True, header_grants=True) as url:

        async def download(grant, destination):
            async with connect(
                url, mode=mode, headers={"X-PMCMCP-Output-Root": str(grant)}
            ) as client:
                result = await client.call_tool("search_artifacts", {"query": "PMC123.2"})
                return await client.call_tool(
                    "download_artifacts",
                    {
                        "selection_tokens": [
                            result.structured_content["choices"][0]["selection_token"]
                        ],
                        "output_dir": str(destination),
                    },
                )

        outcomes = await asyncio.gather(download(a, a), download(b, b))
        assert all(r.structured_content["receipts"][0]["status"] == "downloaded" for r in outcomes)
        assert (a / "PMC123.2-main.pdf").is_file()
        assert (b / "PMC123.2-main.pdf").is_file()
        denied = await download(a, b)
        assert denied.structured_content["receipts"][0]["code"] == "destination_denied"
        async with connect(url, mode=mode) as client:
            choices = await client.call_tool("search_artifacts", {"query": "PMC123.2"})
            missing = await client.call_tool(
                "download_artifacts",
                {
                    "selection_tokens": [
                        choices.structured_content["choices"][0]["selection_token"]
                    ],
                    "output_dir": str(a),
                },
            )
            assert missing.structured_content["receipts"][0]["code"] == "destination_not_configured"
        async with httpx2.AsyncClient(trust_env=False) as http:
            duplicate = await http.post(
                url,
                headers=[
                    ("Authorization", f"Bearer {BEARER}"),
                    ("X-PMCMCP-Output-Root", str(a)),
                    ("X-PMCMCP-Output-Root", str(b)),
                ],
                json={},
            )
            assert duplicate.status_code == 400
