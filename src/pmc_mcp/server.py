"""Thin typed tools over the application services."""

import os
import secrets
from typing import Annotated

from mcp.server import MCPServer
from mcp.server.transport_security import TransportSecurityMiddleware, TransportSecuritySettings
from mcp_types import ToolAnnotations
from pydantic import Field
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from pmc_mcp.application import Application
from pmc_mcp.domain.models import ArtifactKind, DownloadResult, SearchResult
from pmc_mcp.plugin import OUTPUT_ROOT


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
    def __init__(
        self, app: ASGIApp, token: str, instance_id: str | None = None, build_id: str | None = None
    ):
        if not token:
            raise ValueError("a bearer credential is required")
        self.build_id = build_id
        self.instance_id = instance_id
        self.security = TransportSecurityMiddleware(
            TransportSecuritySettings(
                allowed_hosts=["127.0.0.1:*", "localhost:*"],
                allowed_origins=["http://127.0.0.1:*", "http://localhost:*"],
            )
        )
        self.app = app
        self.expected = f"Bearer {token}".encode("ascii")

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http":
            auth_values = [v for k, v in scope.get("headers", []) if k == b"authorization"]
            authorization = auth_values[0] if len(auth_values) == 1 else b""
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
            if scope["path"] == "/health" and scope["method"] == "GET":
                rejection = await self.security.validate_request(Request(scope, receive))
                response = rejection or JSONResponse(
                    {
                        "name": "pmc-mcp",
                        "instance_id": self.instance_id,
                        "build_id": self.build_id,
                        "pid": os.getpid(),
                    }
                )
                await response(scope, receive, send)
                return
            if self.instance_id is not None:
                values = [v for k, v in scope.get("headers", []) if k == b"x-pmcmcp-output-root"]
                if len(values) > 1:
                    await JSONResponse({"code": "duplicate_output_grant"}, status_code=400)(
                        scope, receive, send
                    )
                    return
                raw = values[0] if values else b""
                # The host expands its human-owned setting; tool arguments cannot set this.
                grant = OUTPUT_ROOT.set(raw.decode("latin-1") or None)
                try:
                    await self.app(scope, receive, send)
                finally:
                    OUTPUT_ROOT.reset(grant)
                return
        await self.app(scope, receive, send)


def create_app(
    application: Application,
    token: str,
    *,
    instance_id: str | None = None,
    build_id: str | None = None,
) -> ASGIApp:
    return Bearer(
        create_server(application).streamable_http_app(stateless_http=True, json_response=False),
        token,
        instance_id,
        build_id,
    )
