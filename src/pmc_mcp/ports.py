"""Small interfaces used by application services."""

from collections.abc import AsyncGenerator, AsyncIterator
from typing import Protocol

from pmc_mcp.domain.models import Artifact, ArtifactKind, CatalogPage, Query, Receipt


class Provider(Protocol):
    async def search(
        self, query: Query, kinds: tuple[ArtifactKind, ...], limit: int, offset: int
    ) -> CatalogPage: ...

    async def refresh(self, artifact: Artifact) -> Artifact: ...

    def content(self, artifact: Artifact) -> AsyncGenerator[bytes, None]: ...


class Destination(Protocol):
    async def save(
        self, artifact: Artifact, chunks: AsyncIterator[bytes], output_dir: str
    ) -> Receipt: ...
