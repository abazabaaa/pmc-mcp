"""Independent HTTP process with a synthetic provider; never contacts PMC."""

import argparse
import asyncio
import hashlib
import io
from pathlib import Path

import uvicorn
from pypdf import PdfWriter

from pmc_mcp.adapters.filesystem import LocalFiles
from pmc_mcp.application import Application
from pmc_mcp.domain.models import Artifact, CatalogPage, Query
from pmc_mcp.plugin import HeaderFiles
from pmc_mcp.server import create_app

KEY = b"synthetic-fixture-key-32-bytes-0000"
BEARER = "fixture-only-credential"


class FixtureProvider:
    def __init__(self, root: Path, slow: bool):
        writer = PdfWriter()
        writer.add_blank_page(width=72, height=72)
        stream = io.BytesIO()
        writer.write(stream)
        self.body = stream.getvalue()
        self.root = root
        self.slow = slow
        self.artifact = Artifact(
            pmcid="PMC123",
            version=2,
            kind="pdf",
            key="PMC123.2/PMC123.2.pdf",
            md5=hashlib.md5(self.body).hexdigest(),
            title="Synthetic article",
            license_code="CC BY",
            retracted=True,
        )

    async def search(self, query: Query, kinds, limit: int, offset: int) -> CatalogPage:
        return CatalogPage(artifacts=(self.artifact,))

    async def refresh(self, artifact: Artifact) -> Artifact:
        return self.artifact

    async def content(self, artifact: Artifact):
        try:
            yield self.body[:12]
            if self.slow:
                (self.root / "started").touch()
                await asyncio.sleep(2)
                (self.root / "finished").touch()
            yield self.body[12:]
        finally:
            if self.slow:
                (self.root / "closed").touch()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--slow", action="store_true")
    parser.add_argument("--header-grants", action="store_true")
    args = parser.parse_args()
    files = LocalFiles([args.root])
    try:
        app = Application(
            FixtureProvider(args.root, args.slow),
            HeaderFiles() if args.header_grants else files,
            KEY,
        )
        uvicorn.run(
            create_app(app, BEARER, instance_id="fixture" if args.header_grants else None),
            host="127.0.0.1",
            port=args.port,
            log_level="error",
        )
    finally:
        files.close()


if __name__ == "__main__":
    main()
