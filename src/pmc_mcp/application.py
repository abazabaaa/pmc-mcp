"""Application orchestration shared by MCP and command-line entry points."""

import asyncio
import time
from contextlib import aclosing

from pmc_mcp.domain.models import (
    ArtifactKind,
    Choice,
    DownloadResult,
    Notice,
    Receipt,
    SearchResult,
)
from pmc_mcp.domain.rules import DomainError, check_unchanged, normalize_query, permits_local_copy
from pmc_mcp.domain.tokens import seal, unseal
from pmc_mcp.ports import Destination, Provider


class Application:
    def __init__(self, provider: Provider, files: Destination, key: bytes, *, budget: float = 120):
        self.provider = provider
        self.files = files
        self.key = key
        self.budget = budget
        self._transfer = asyncio.Lock()

    async def search(
        self,
        query: str,
        kinds: tuple[ArtifactKind, ...] = ("pdf",),
        limit: int = 5,
        cursor: str | None = None,
    ) -> SearchResult:
        if not 1 <= limit <= 5 or not kinds or len(set(kinds)) != len(kinds):
            return SearchResult(status="invalid_query", notices=(Notice(code="invalid_query"),))
        try:
            normalized = normalize_query(query)
            offset = int(cursor) if cursor is not None else 0
            if offset < 0 or offset > 100 or str(offset) != (cursor or "0"):
                raise DomainError("invalid_cursor")
            async with asyncio.timeout(self.budget):
                page = await self.provider.search(normalized, kinds, limit, offset)
            artifacts = list({(a.pmcid, a.version, a.kind): a for a in page.artifacts}.values())
            notices = page.notices
            if len(artifacts) > 40:
                notices += (Notice(code="choices_truncated"),)
            choices = tuple(
                Choice(artifact=a, selection_token=seal(a, self.key, now=int(time.time())))
                for a in artifacts[:40]
            )
            return SearchResult(
                status="ok" if choices or notices else "no_matches",
                choices=choices,
                notices=notices,
                next_cursor=str(page.next_offset)
                if page.next_offset is not None and page.next_offset <= 100
                else None,
            )
        except (ValueError, TimeoutError) as error:
            code = error.code if isinstance(error, DomainError) else "source_unavailable"
            status = (
                "invalid_query"
                if code in {"invalid_query", "invalid_identifier", "invalid_cursor"}
                else "unavailable"
            )
            return SearchResult(status=status, notices=(Notice(code=code),))

    async def download(self, tokens: list[str], output_dir: str) -> DownloadResult:
        if not 1 <= len(tokens) <= 5:
            return DownloadResult(
                receipts=(Receipt(status="failed", code="invalid_selection_count"),)
            )
        receipts = []
        deadline = time.monotonic() + self.budget
        for token in tokens:
            artifact = None
            try:
                async with asyncio.timeout(max(0.0, deadline - time.monotonic())):
                    async with self._transfer:
                        artifact = unseal(token, self.key, now=int(time.time()))
                        fresh = await self.provider.refresh(artifact)
                        check_unchanged(artifact, fresh)
                        if not permits_local_copy(fresh.license_code):
                            raise DomainError("rights_not_configured")
                        async with aclosing(self.provider.content(fresh)) as chunks:
                            receipt = await self.files.save(fresh, chunks, output_dir)
                receipts.append(receipt)
            except (DomainError, TimeoutError) as error:
                code = error.code if isinstance(error, DomainError) else "time_budget_exhausted"
                receipts.append(Receipt(status="failed", code=code, artifact=artifact))
        return DownloadResult(receipts=tuple(receipts))
