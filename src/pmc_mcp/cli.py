"""Run local MCP or use the same search/download application directly."""

import argparse
import asyncio
import json
import os
import sys
from collections.abc import AsyncIterator
from pathlib import Path

import httpx2
import uvicorn

from pmc_mcp.adapters.filesystem import LocalFiles
from pmc_mcp.adapters.http import Reader
from pmc_mcp.adapters.pmc import PMC
from pmc_mcp.application import Application
from pmc_mcp.domain.models import Artifact, Receipt
from pmc_mcp.domain.rules import DomainError
from pmc_mcp.plugin import HeaderFiles
from pmc_mcp.plugin_runtime import PORT, configure, configured_root, endpoint_port, ensure, stop
from pmc_mcp.server import create_app
from pmc_mcp.state import secret


class NoDestination:
    async def save(
        self, artifact: Artifact, chunks: AsyncIterator[bytes], output_dir: str
    ) -> Receipt:
        raise DomainError("destination_not_configured")


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(
        prog="pmc-mcp", description="Local PMC search and verified artifact downloads"
    )
    commands = result.add_subparsers(dest="command", required=True)
    for command in (
        "serve",
        "plugin-serve",
        "ensure",
        "stop",
        "configure",
        "search",
        "download",
        "init",
    ):
        item = commands.add_parser(command)
        item.add_argument("--state-dir", type=Path, default=Path.home() / ".local/state/pmc-mcp")
        item.add_argument(
            "--contact-email",
            default=None,
            help="Optional NCBI contact address; retained only in local requests",
        )
        if command in {"serve", "download"}:
            item.add_argument("--allow-output-root", type=Path, action="append", required=True)
        if command in {"serve", "plugin-serve", "ensure", "stop"}:
            item.add_argument("--port", type=int, default=8000 if command == "serve" else PORT)
        if command == "configure":
            item.add_argument("--output-root", type=Path, required=True)
        if command == "ensure":
            item.add_argument("--configured-root", action="store_true")
        if command == "plugin-serve":
            item.add_argument("--instance-id", required=True)
            item.add_argument("--build-id", required=True)
        if command in {"ensure", "stop"}:
            item.add_argument("--project-root", type=Path, required=True)
        if command == "search":
            item.add_argument("query")
            item.add_argument("--kind", choices=["pdf", "jats"], action="append")
            item.add_argument("--limit", type=int, default=5)
            item.add_argument("--cursor")
        if command == "download":
            item.add_argument("selection_tokens", nargs="+")
            item.add_argument("--output-dir", required=True)
    return result


async def run(args: argparse.Namespace) -> int:
    if args.command == "configure":
        configure(args.state_dir, args.output_root)
        return 0
    if args.command in {"ensure", "stop"}:
        port = endpoint_port(os.environ.get("CLAUDE_CODE_MCP_SERVER_URL"), args.port)
        if args.command == "ensure":
            headers = ensure(args.state_dir, args.project_root, port)
            if args.configured_root:
                headers["X-PMCMCP-Output-Root"] = configured_root(args.state_dir)
            print(json.dumps(headers))
        else:
            stop(args.state_dir, args.project_root, port)
        return 0
    key = secret(args.state_dir, "signing.key", 32)
    bearer = secret(args.state_dir, "bearer.key", 32).hex()
    if args.command == "init":
        print(
            json.dumps(
                {
                    "state_dir": str(args.state_dir),
                    "credential_file": "bearer.key",
                    "credential_encoding": "hex",
                    "signing_key_file": "signing.key",
                }
            )
        )
        return 0
    roots = getattr(args, "allow_output_root", None)
    files = LocalFiles(roots) if roots else None
    try:
        async with httpx2.AsyncClient(
            timeout=httpx2.Timeout(20, connect=5), trust_env=False, follow_redirects=False
        ) as client:
            app = Application(
                PMC(Reader(client), contact_email=args.contact_email),
                HeaderFiles() if args.command == "plugin-serve" else (files or NoDestination()),
                key,
            )
            if args.command in {"serve", "plugin-serve"}:
                if not 1 <= args.port <= 65535:
                    raise DomainError("invalid_port")
                config = uvicorn.Config(
                    create_app(
                        app,
                        bearer,
                        instance_id=getattr(args, "instance_id", None),
                        build_id=getattr(args, "build_id", None),
                    ),
                    host="127.0.0.1",
                    port=args.port,
                    log_level="warning",
                )
                await uvicorn.Server(config).serve()
                return 0
            if args.command == "search":
                result = await app.search(
                    args.query, tuple(args.kind or ["pdf"]), args.limit, args.cursor
                )
                print(result.model_dump_json(indent=2))
                return 0 if result.status in {"ok", "no_matches"} else 1
            result = await app.download(args.selection_tokens, args.output_dir)
            print(result.model_dump_json(indent=2))
            return 0 if all(r.status != "failed" for r in result.receipts) else 1
    finally:
        if files:
            files.close()


def main() -> None:
    args = parser().parse_args()
    try:
        code = asyncio.run(run(args))
    except DomainError as error:
        print(
            json.dumps({"status": "failed", "code": error.code}),
            file=sys.stderr if args.command in {"ensure", "stop", "plugin-serve"} else sys.stdout,
        )
        code = 1
    except KeyboardInterrupt:
        code = 130
    raise SystemExit(code)


if __name__ == "__main__":
    main()
