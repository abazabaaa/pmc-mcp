"""Thin typed tools over the application services."""

import secrets
from typing import Annotated

from mcp.server import MCPServer
from mcp_types import ToolAnnotations
from pydantic import Field
from starlette.types import ASGIApp, Receive, Scope, Send

from pmc_mcp.application import Application
from pmc_mcp.domain.models import ArtifactKind, DownloadResult, SearchResult


def create_server(application: Application) -> MCPServer:
    server = MCPServer(
        "pmc-mcp",
        version="0.1.0",
        subscriptions=False,
        instructions=(
            "Search for exact paper and artifact choices, then download only selected tokens "
            "into a directory the user granted locally. Preserve manuscript, license, and "
            "retraction information. A failed artifact is not silently replaced by another "
            "source or format."
        ),
    )

    @server.tool(
        annotations=ToolAnnotations(
            read_only_hint=True, destructive_hint=False, idempotent_hint=True, open_world_hint=True
        )
    )
    async def search_artifacts(
        query: Annotated[str, Field(min_length=1, max_length=2000)],
        kinds: list[ArtifactKind] | None = None,
        limit: Annotated[int, Field(ge=1, le=5)] = 5,
        cursor: str | None = None,
    ) -> SearchResult:
        """Search PMC by title, DOI, PMID, or PMCID(.N), returning exact artifact choices.

        Search does not download files. Availability is metadata-advertised until validated.
        """
        return await application.search(
            query, tuple(kinds) if kinds is not None else ("pdf",), limit, cursor
        )

    @server.tool(
        annotations=ToolAnnotations(
            read_only_hint=False, destructive_hint=False, idempotent_hint=True, open_world_hint=True
        )
    )
    async def download_artifacts(
        selection_tokens: Annotated[list[str], Field(min_length=1, max_length=5)],
        output_dir: Annotated[str, Field(min_length=1, max_length=4096)],
    ) -> DownloadResult:
        """Save selected artifacts into an absolute directory within local configured grants.

        Return paths, hashes, and receipts; preserve conflicting files. Legacy sessionless
        calls may finish after Stop.
        """
        return await application.download(selection_tokens, output_dir)

    return server


class Bearer:
    def __init__(self, app: ASGIApp, token: str):
        if not token:
            raise ValueError("a bearer credential is required")
        self.app = app
        self.expected = f"Bearer {token}".encode("ascii")

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http":
            authorization = dict(scope.get("headers", [])).get(b"authorization", b"")
            if not secrets.compare_digest(authorization, self.expected):
                await send(
                    {
                        "type": "http.response.start",
                        "status": 401,
                        "headers": [(b"www-authenticate", b"Bearer")],
                    }
                )
                await send({"type": "http.response.body", "body": b"Unauthorized"})
                return
        await self.app(scope, receive, send)


def create_app(application: Application, token: str) -> ASGIApp:
    return Bearer(
        create_server(application).streamable_http_app(stateless_http=True, json_response=False),
        token,
    )
