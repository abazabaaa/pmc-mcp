"""An authenticated host's directory setting is an explicit per-request grant."""

from collections.abc import AsyncIterator
from contextvars import ContextVar
from pathlib import Path

from pmc_mcp.adapters.filesystem import LocalFiles
from pmc_mcp.domain.models import Artifact, Receipt
from pmc_mcp.domain.rules import DomainError

OUTPUT_ROOT: ContextVar[str | None] = ContextVar("pmc_output_root", default=None)


class HeaderFiles:
    async def save(
        self, artifact: Artifact, chunks: AsyncIterator[bytes], output_dir: str
    ) -> Receipt:
        configured = OUTPUT_ROOT.get()
        if not configured:
            raise DomainError("destination_not_configured")
        if (
            len(configured) > 4096
            or "\x00" in configured
            or not Path(configured).is_absolute()
            or ".." in Path(configured).parts
        ):
            raise DomainError("destination_denied")
        files = LocalFiles([Path(configured)])
        try:
            return await files.save(artifact, chunks, output_dir)
        finally:
            files.close()
