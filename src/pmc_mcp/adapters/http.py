"""Bounded official-origin HTTP access; no caller-provided URL forwarding."""

import asyncio
import time
from collections.abc import AsyncGenerator
from urllib.parse import urlsplit

import httpx2

from pmc_mcp.domain.rules import DomainError, retry_delay

HOSTS = {"pmc-oa-opendata.s3.amazonaws.com", "eutils.ncbi.nlm.nih.gov", "pmc.ncbi.nlm.nih.gov"}


class Reader:
    def __init__(
        self,
        client: httpx2.AsyncClient,
        *,
        metadata_cap: int = 2**20,
        budget: float = 30,
        ncbi_spacing: float = 1,
    ):
        self.client = client
        self.metadata_cap = metadata_cap
        self.budget = budget
        self.ncbi_spacing = ncbi_spacing
        self._gate = asyncio.Lock()
        self._last_ncbi = 0.0

    async def _pace(self, host: str) -> None:
        if host == "pmc-oa-opendata.s3.amazonaws.com":
            return
        async with self._gate:
            wait = self.ncbi_spacing - (time.monotonic() - self._last_ncbi)
            if wait > 0:
                await asyncio.sleep(wait)
            self._last_ncbi = time.monotonic()

    async def stream(self, url: str, *, cap: int) -> AsyncGenerator[bytes, None]:
        parsed = urlsplit(url)
        if (
            parsed.scheme != "https"
            or parsed.hostname not in HOSTS
            or parsed.port not in (None, 443)
            or parsed.username
            or parsed.password
            or parsed.fragment
        ):
            raise DomainError("blocked_source_url")
        host = parsed.hostname
        deadline = time.monotonic() + self.budget
        for attempt in range(3):
            await self._pace(host)
            emitted = False
            try:
                async with self.client.stream("GET", url, follow_redirects=False) as response:
                    if response.is_redirect:
                        raise DomainError("blocked_redirect")
                    if response.status_code == 404:
                        raise DomainError("source_missing")
                    if response.status_code != 200:
                        delay = retry_delay(
                            response.status_code,
                            response.headers.get("retry-after"),
                            attempt=attempt,
                            now=time.time(),
                            remaining=deadline - time.monotonic(),
                        )
                        if delay is None:
                            raise DomainError(
                                "rate_limited"
                                if response.status_code == 429
                                else "source_unavailable"
                            )
                    else:
                        try:
                            declared = int(response.headers.get("content-length", "0"))
                        except ValueError as error:
                            raise DomainError("invalid_source_response") from error
                        if declared < 0 or declared > cap:
                            raise DomainError("too_large")
                        size = 0
                        async for chunk in response.aiter_bytes():
                            size += len(chunk)
                            if size > cap:
                                raise DomainError("too_large")
                            emitted = True
                            yield chunk
                        return
                await asyncio.sleep(delay)
            except httpx2.HTTPError as error:
                # Retry only before any bytes have entered the caller's staged file.
                delay = retry_delay(
                    503,
                    None,
                    attempt=attempt,
                    now=time.time(),
                    remaining=deadline - time.monotonic(),
                )
                if emitted or delay is None:
                    raise DomainError("source_unavailable") from error
                await asyncio.sleep(delay)
        raise DomainError("source_unavailable")

    async def get(self, url: str) -> bytes:
        async with asyncio.timeout(self.budget):
            return b"".join([chunk async for chunk in self.stream(url, cap=self.metadata_cap)])
