"""Local installation secrets; never part of tool results or published files."""

import os
import secrets
import stat
from pathlib import Path

from pmc_mcp.domain.rules import DomainError


def secret(directory: Path, name: str, size: int) -> bytes:
    try:
        directory.mkdir(parents=True, mode=0o700, exist_ok=True)
        info = directory.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise DomainError("unsafe_state_directory")
        root = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        temp = f".key-{secrets.token_hex(16)}"
        created = False
        try:
            # Publish a complete key atomically so simultaneous processes share one value.
            try:
                fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=root)
            except FileNotFoundError:
                fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600, dir_fd=root)
                created = True
                with os.fdopen(fd, "wb") as stream:
                    stream.write(secrets.token_bytes(size))
                    stream.flush()
                    os.fsync(stream.fileno())
                try:
                    os.link(temp, name, src_dir_fd=root, dst_dir_fd=root, follow_symlinks=False)
                    os.fsync(root)
                except FileExistsError:
                    pass
                fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=root)
            try:
                info = os.fstat(fd)
                if (
                    not stat.S_ISREG(info.st_mode)
                    or info.st_uid != os.getuid()
                    or info.st_mode & 0o077
                ):
                    raise DomainError("unsafe_state_file")
                value = os.read(fd, size + 1)
            finally:
                os.close(fd)
            if len(value) != size:
                raise DomainError("invalid_state_file")
            return value
        finally:
            if created:
                os.unlink(temp, dir_fd=root)
            os.close(root)
    except OSError as error:
        raise DomainError("state_unavailable") from error
