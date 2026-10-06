import asyncio
import hashlib

import pytest

from pmc_mcp.adapters.filesystem import LocalFiles
from pmc_mcp.application import Application
from pmc_mcp.domain.models import CatalogPage, Query
from pmc_mcp.domain.rules import DomainError
from pmc_mcp.domain.tokens import seal

KEY = b"synthetic-fixture-key-32-bytes-0000"


async def chunks(body):
    yield body[:12]
    await asyncio.sleep(0)
    yield body[12:]


@pytest.fixture
def files(tmp_path):
    result = LocalFiles([tmp_path])
    yield result
    result.close()


async def test_verified_file_and_lost_receipt_recovery(files, tmp_path, file_artifact, pdf_body):
    result = await files.save(file_artifact, chunks(pdf_body), str(tmp_path))
    assert result.status == "downloaded"
    final = tmp_path / "PMC123.2-main.pdf"
    assert final.read_bytes() == pdf_body
    assert result.sha256 == hashlib.sha256(pdf_body).hexdigest()
    sidecar = tmp_path / "PMC123.2-main.pdf.receipt.json"
    sidecar.unlink()  # Lost sidecar/response after the artifact publication point.
    again = await files.save(file_artifact, chunks(b"must not be fetched"), str(tmp_path))
    assert again.status == "already_present"
    assert sidecar.is_file()
    assert not list(tmp_path.glob(".pmc-*"))


async def test_conflicting_file_is_preserved(files, tmp_path, file_artifact, pdf_body):
    final = tmp_path / file_artifact.filename
    final.write_bytes(b"preserve me")
    with pytest.raises(DomainError, match="destination_conflict"):
        await files.save(file_artifact, chunks(pdf_body), str(tmp_path))
    assert final.read_bytes() == b"preserve me"


async def test_concurrent_publication_is_no_clobber(files, tmp_path, file_artifact, pdf_body):
    results = await asyncio.gather(
        *(files.save(file_artifact, chunks(pdf_body), str(tmp_path)) for _ in range(2))
    )
    assert {r.status for r in results} == {"downloaded", "already_present"}
    assert (tmp_path / file_artifact.filename).read_bytes() == pdf_body
    assert not list(tmp_path.glob(".pmc-*"))


@pytest.mark.parametrize("case", ["checksum", "format", "size"])
async def test_invalid_content_never_becomes_a_final_file(files, tmp_path, file_artifact, case):
    body = b"<html>not an article</html>"
    artifact = file_artifact
    if case == "format":
        artifact = artifact.model_copy(update={"md5": hashlib.md5(body).hexdigest()})
    if case == "size":
        files.cap = 4
    with pytest.raises(DomainError):
        await files.save(artifact, chunks(body), str(tmp_path))
    assert list(tmp_path.iterdir()) == []


async def test_cancellation_removes_partial_file(files, tmp_path, file_artifact):
    started = asyncio.Event()
    closed = asyncio.Event()

    async def slow():
        try:
            yield b"prefix"
            started.set()
            await asyncio.sleep(30)
        finally:
            closed.set()

    task = asyncio.create_task(files.save(file_artifact, slow(), str(tmp_path)))
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert closed.is_set()
    assert list(tmp_path.iterdir()) == []


async def test_symlink_and_traversal_escape_is_rejected(files, tmp_path, file_artifact, pdf_body):
    outside = tmp_path.parent / "outside-fixture"
    outside.mkdir()
    (tmp_path / "escape").symlink_to(outside, target_is_directory=True)
    for destination in [str(tmp_path / "escape"), str(tmp_path / ".."), str(outside), "relative"]:
        with pytest.raises(DomainError, match="destination_denied"):
            await files.save(file_artifact, chunks(pdf_body), destination)
    assert list(outside.iterdir()) == []


async def test_final_symlink_is_never_followed(files, tmp_path, file_artifact, pdf_body):
    outside = tmp_path / "other"
    outside.write_bytes(b"preserve")
    (tmp_path / file_artifact.filename).symlink_to(outside)
    with pytest.raises(DomainError, match="destination_conflict"):
        await files.save(file_artifact, chunks(pdf_body), str(tmp_path))
    assert outside.read_bytes() == b"preserve"


async def test_jats_contradictory_identity_is_rejected(files, tmp_path, file_artifact):
    body = b'<article><article-id pub-id-type="doi">10.9999/wrong</article-id></article>'
    artifact = file_artifact.model_copy(
        update={
            "kind": "jats",
            "key": "PMC123.2/PMC123.2.xml",
            "md5": hashlib.md5(body).hexdigest(),
        }
    )
    with pytest.raises(DomainError, match="artifact_identity_mismatch"):
        await files.save(artifact, chunks(body), str(tmp_path))
    assert list(tmp_path.iterdir()) == []


class FakeProvider:
    def __init__(self, artifact, body):
        self.artifact = artifact
        self.body = body
        self.fetches = 0

    async def search(self, query: Query, kinds, limit, offset) -> CatalogPage:
        return CatalogPage(artifacts=(self.artifact,))

    async def refresh(self, artifact):
        return self.artifact

    async def content(self, artifact):
        self.fetches += 1
        yield self.body


async def test_choice_works_after_application_restart(files, tmp_path, file_artifact, pdf_body):
    first = Application(FakeProvider(file_artifact, pdf_body), files, KEY)
    search = await first.search("PMC123.2")
    assert search.choices[0].artifact.retracted is True
    second = Application(FakeProvider(file_artifact, pdf_body), files, KEY)
    result = await second.download([search.choices[0].selection_token], str(tmp_path))
    assert result.receipts[0].status == "downloaded"
    assert result.receipts[0].artifact is not None
    assert result.receipts[0].artifact.retracted is True


async def test_changed_choice_is_rejected_before_any_byte_effect(
    files, tmp_path, file_artifact, pdf_body
):
    import time

    provider = FakeProvider(file_artifact.model_copy(update={"retracted": False}), pdf_body)
    app = Application(provider, files, KEY)
    token = seal(file_artifact, KEY, now=int(time.time()))
    result = await app.download([token], str(tmp_path))
    assert result.receipts[0].code == "choice_changed"
    assert provider.fetches == 0
    assert list(tmp_path.iterdir()) == []


async def test_conflicting_receipt_does_not_hide_valid_publication(
    files, tmp_path, file_artifact, pdf_body
):
    sidecar = tmp_path / (file_artifact.filename + ".receipt.json")
    sidecar.write_text("preserve this unrelated file")
    receipt = await files.save(file_artifact, chunks(pdf_body), str(tmp_path))
    assert receipt.status == "downloaded"
    assert receipt.code == "receipt_conflict"
    assert sidecar.read_text() == "preserve this unrelated file"
    assert (tmp_path / file_artifact.filename).read_bytes() == pdf_body
