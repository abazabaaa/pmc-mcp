# pmc-mcp

Search PMC, choose an exact paper artifact, and save verified files into a folder on the same computer. Two typed MCP tools handle the workflow; deterministic domain rules and local adapters handle identifiers, versions, checksums, retries, and file publication.

No hosted server, cloud credentials, object-store cache, or external state service is needed. The provider reads public NCBI services and the official anonymous PMC content bucket. The first version supports main-article PDF and JATS XML on macOS/Linux.

## Run locally

Install [uv](https://docs.astral.sh/uv/getting-started/installation/), then from this checkout:

```sh
uv sync --locked
mkdir -p downloads
uv run pmc-mcp init
uv run pmc-mcp serve --allow-output-root "$PWD/downloads"
```

The server listens on `http://127.0.0.1:8000/mcp`. Configure your MCP host to use Streamable HTTP with an `Authorization: Bearer …` header. Its credential is the **hex encoding** of the 32 bytes in `~/.local/state/pmc-mcp/bearer.key`; load it into your host's local secret storage. The server does not print it. Protect this credential: possession permits searches and writes within the configured roots. Host/Origin checks also apply.

Use `--state-dir` consistently to choose a different private state folder, and `--port` to change the port. The state folder must be owned by the current user with no group/other access. The signing key is installation configuration; retaining it makes choices survive restarts. Losing or rotating it invalidates earlier choices. Run one server process. Separate concurrent instances do not share a rate limiter.

For a Python MCP client, read the credential locally rather than embedding it in source:

```python
import asyncio
from pathlib import Path

import httpx2
from mcp import Client
from mcp.client.streamable_http import streamable_http_client


async def main():
    credential = (Path.home() / ".local/state/pmc-mcp/bearer.key").read_bytes().hex()
    async with httpx2.AsyncClient(
        headers={"Authorization": f"Bearer {credential}"}, trust_env=False
    ) as http:
        async with Client(
            streamable_http_client("http://127.0.0.1:8000/mcp", http_client=http)
        ) as client:
            result = await client.call_tool("search_artifacts", {"query": "PMC6404399.1"})
            print(result.structured_content)


asyncio.run(main())
```

## Search, choose, download

`search_artifacts(query, kinds=["pdf"], limit=5, cursor=None)` accepts a title/query, DOI, PMID, PMCID, or explicit `PMCID.N`. It returns exact version/format choices with title, identifiers, citation when supplied, license, manuscript/retraction flags, and a signed `selection_token`. Missing status information stays unknown. Search reads metadata; `advertised` availability is confirmed only after downloading and validating bytes. An unversioned search lists deposited expressions without assuming the largest suffix is preferred.

`download_artifacts(selection_tokens, output_dir)` accepts up to five selected tokens and an absolute directory within a root granted at launch. It returns individual receipts with status, path, SHA-256, size, retrieval time, and selected artifact metadata. The directory must already exist. Tokens expire after 24 hours; metadata changes produce `choice_changed`, requiring a fresh search and selection. A failed PDF never silently becomes XML, a supplement, or another version.

The CLI uses exactly the same application services:

```sh
uv run pmc-mcp search 'PMC6404399.1' --kind pdf --kind jats
uv run pmc-mcp download '<selection_token>' \
  --allow-output-root "$PWD/downloads" --output-dir "$PWD/downloads"
```

The model cannot expand folder grants. Titles never become filenames. Traversal, symlink destinations, replaced roots, and conflicting files are rejected. A validated file is atomically linked into place without overwriting another file. A matching existing artifact returns `already_present`. Files and receipt sidecars are separate commits: retry repairs a missing receipt; an incompatible receipt is preserved with `receipt_conflict`. `receipt_unavailable` or `durability_unconfirmed` means bytes committed but the accompanying guarantee could not be completed.

## Operating limits

- One active transfer per process, 50 MiB per artifact, a 120-second application budget, and at most three network attempts. Metadata responses are bounded separately.
- Only fixed official HTTPS origins and exact main-article object keys are accepted. Redirects are rejected. MD5 must agree with provider metadata; SHA-256 identifies local bytes. PDF parsing and safe JATS parsing run before publication.
- Local-copy policy admits recognized CC0/CC BY license variants. Unknown, custom, and TDM-only rights produce `rights_not_configured`. Retraction flags remain visible; a newly changed flag invalidates an old selection. Downloads do not establish scientific validity or permission for later redistribution.
- Modern MCP revision `2026-07-28` uses request-scoped SSE without session IDs. Cancellation before publication cleans partial files. Legacy `2025-11-25` clients can stop waiting while a handler finishes; the server deadline still applies. Once valid bytes commit, cancellation does not roll them back.
- `--contact-email` is optional NCBI request configuration. It is sent to NCBI, never included in artifact tokens or receipts. Keep real contact information and local keys out of source control.

## Development and evidence

```sh
uv run ruff check .
uv run ruff format --check .
uv run ty check
uv run pytest -q
uv build
uv run python scripts/check_publication.py --archives
```

The suite includes pure contract tests, injected HTTP failures, filesystem negative controls, independent loopback server processes, modern/legacy wire tests, and cross-process selections. Local publication checks and CI scan source/history identities and both distribution archives. Live PMC calls are excluded from automated tests.

On October 6, 2026, a separate CLI smoke check retrieved PDF and JATS for `PMC6404399.1`, read back their SHA-256 values and receipts, and verified restart-safe `already_present` retries. This is one live work, not a broad corpus qualification. Actual desktop-host registration and interoperability remain a separate acceptance gate.

See [design and remaining acceptance work](docs/design.md). Dependencies are locked, including MCP Python SDK 2.3.0; protocol or provider changes require re-running their respective gates.
