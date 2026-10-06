"""POSIX directory confinement and atomic no-clobber artifact publication."""

import asyncio
import hashlib
import io
import os
import secrets
import stat
from collections.abc import AsyncIterator, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

from defusedxml import ElementTree
from pypdf import PdfReader

from pmc_mcp.domain.models import Artifact, Receipt
from pmc_mcp.domain.rules import DomainError, fingerprint, validate_artifact


def validate_media(body: bytes, artifact: Artifact) -> None:
    try:
        if artifact.kind == "pdf":
            reader = PdfReader(io.BytesIO(body), strict=True)
            if reader.is_encrypted or not reader.pages:
                raise DomainError("invalid_media")
        else:
            root = ElementTree.fromstring(body)
            if root.tag.rsplit("}", 1)[-1] != "article":
                raise DomainError("invalid_media")
            for node in root.iter():
                if node.tag.rsplit("}", 1)[-1] != "article-id":
                    continue
                identity = (node.text or "").strip()
                kind = node.get("pub-id-type")
                if kind == "doi" and artifact.doi and identity.lower() != artifact.doi.lower():
                    raise DomainError("artifact_identity_mismatch")
                if kind == "pmid" and artifact.pmid and identity != artifact.pmid:
                    raise DomainError("artifact_identity_mismatch")
                if kind == "pmc" and identity.upper().removeprefix("PMC") != artifact.pmcid[3:]:
                    raise DomainError("artifact_identity_mismatch")
    except DomainError:
        raise
    except Exception as error:
        raise DomainError("invalid_media") from error


class LocalFiles:
    """Owns explicit root handles; does not authorize model-supplied new roots."""

    def __init__(self, roots: list[Path], *, cap: int = 50 * 2**20):
        if not roots:
            raise DomainError("destination_not_configured")
        self.cap = cap
        self.roots: list[tuple[Path, Path, int]] = []
        try:
            for root in roots:
                original = root.absolute()
                resolved = root.resolve(strict=True)
                fd = os.open(resolved, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
                self.roots.append((original, resolved, fd))
        except OSError as error:
            self.close()
            raise DomainError("destination_unavailable") from error

    def close(self) -> None:
        for _, _, fd in self.roots:
            os.close(fd)
        self.roots.clear()

    @contextmanager
    def directory(self, output_dir: str) -> Iterator[tuple[int, Path]]:
        target = Path(output_dir)
        if not target.is_absolute() or ".." in target.parts or "\x00" in output_dir:
            raise DomainError("destination_denied")
        for original, resolved, root_fd in self.roots:
            for prefix in (original, resolved):
                try:
                    relative = target.relative_to(prefix)
                except ValueError:
                    continue
                fd = os.dup(root_fd)
                try:
                    for part in relative.parts:
                        child = os.open(
                            part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd
                        )
                        os.close(fd)
                        fd = child
                    yield fd, resolved / relative
                    return
                except OSError as error:
                    raise DomainError("destination_denied") from error
                finally:
                    os.close(fd)
        raise DomainError("destination_denied")

    def read(self, directory: int, name: str) -> bytes:
        fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_size > self.cap:
                raise DomainError("destination_conflict")
            data = bytearray()
            while chunk := os.read(fd, min(65536, self.cap + 1 - len(data))):
                data.extend(chunk)
                if len(data) > self.cap:
                    raise DomainError("destination_conflict")
            return bytes(data)
        finally:
            os.close(fd)

    def _existing(self, directory: int, artifact: Artifact) -> bytes | None:
        try:
            body = self.read(directory, artifact.filename)
        except FileNotFoundError:
            return None
        except OSError as error:
            raise DomainError("destination_conflict") from error
        if hashlib.md5(body, usedforsecurity=False).hexdigest() != artifact.md5:
            raise DomainError("destination_conflict")
        validate_media(body, artifact)
        return body

    def _receipt(self, directory: int, receipt: Receipt) -> Receipt:
        assert receipt.artifact is not None
        name = receipt.artifact.filename + ".receipt.json"
        temp = f".pmc-receipt-{secrets.token_hex(16)}"
        created = False
        try:
            fd = os.open(
                temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=directory
            )
            created = True
            with os.fdopen(fd, "wb") as stream:
                stream.write(receipt.model_dump_json().encode())
                stream.flush()
                os.fsync(stream.fileno())
            try:
                os.link(
                    temp, name, src_dir_fd=directory, dst_dir_fd=directory, follow_symlinks=False
                )
                os.fsync(directory)
            except FileExistsError:
                try:
                    prior = Receipt.model_validate_json(self.read(directory, name))
                    if (
                        prior.sha256 != receipt.sha256
                        or prior.artifact is None
                        or fingerprint(prior.artifact) != fingerprint(receipt.artifact)
                    ):
                        return receipt.model_copy(update={"code": "receipt_conflict"})
                except (ValueError, OSError, DomainError):
                    return receipt.model_copy(update={"code": "receipt_conflict"})
            return receipt
        except OSError:
            return receipt.model_copy(update={"code": "receipt_unavailable"})
        finally:
            if created:
                os.unlink(temp, dir_fd=directory)

    async def save(
        self, artifact: Artifact, chunks: AsyncIterator[bytes], output_dir: str
    ) -> Receipt:
        validate_artifact(artifact)
        try:
            with self.directory(output_dir) as (directory, target):
                existing = self._existing(directory, artifact)
                if existing is not None:
                    return self._receipt(
                        directory, self._result("already_present", artifact, target, existing)
                    )
                temp = f".pmc-download-{secrets.token_hex(16)}"
                fd = os.open(
                    temp,
                    os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                    0o600,
                    dir_fd=directory,
                )
                try:
                    with os.fdopen(fd, "w+b") as stream:
                        size = 0
                        async for chunk in chunks:
                            size += len(chunk)
                            if size > self.cap:
                                raise DomainError("too_large")
                            stream.write(chunk)
                        stream.flush()
                        os.fsync(stream.fileno())
                        stream.seek(0)
                        body = stream.read(self.cap + 1)
                        if hashlib.md5(body, usedforsecurity=False).hexdigest() != artifact.md5:
                            raise DomainError("md5_mismatch")
                        await asyncio.to_thread(validate_media, body, artifact)
                        # Deliver cancellation before the short synchronous publication boundary.
                        await asyncio.sleep(0)
                        try:
                            os.link(
                                temp,
                                artifact.filename,
                                src_dir_fd=directory,
                                dst_dir_fd=directory,
                                follow_symlinks=False,
                            )
                            status = "downloaded"
                        except FileExistsError:
                            raced = self._existing(directory, artifact)
                            if raced is None:
                                raise DomainError("destination_conflict") from None
                            status = "already_present"
                        os.fsync(directory)
                        receipt = self._result(status, artifact, target, body)
                        return self._receipt(directory, receipt)
                finally:
                    os.unlink(temp, dir_fd=directory)
        except OSError as error:
            raise DomainError("destination_unavailable") from error

    @staticmethod
    def _result(status, artifact: Artifact, target: Path, body: bytes) -> Receipt:
        return Receipt(
            status=status,
            artifact=artifact,
            path=str(target / artifact.filename),
            sha256=hashlib.sha256(body).hexdigest(),
            size=len(body),
            retrieved_at=datetime.now(UTC).isoformat(),
        )
