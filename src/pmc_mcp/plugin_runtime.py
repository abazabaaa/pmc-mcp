"""Manage one owned loopback HTTP service, independently of MCP sessions."""

import fcntl
import hashlib
import json
import os
import secrets
import signal
import stat
import subprocess
import sys
import time
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import urlsplit

import httpx2

from pmc_mcp.domain.rules import DomainError
from pmc_mcp.state import secret

PORT = 47836


def endpoint_port(value: str | None, fallback: int = PORT) -> int:
    if value is None:
        return fallback
    parsed = urlsplit(value)
    try:
        port = parsed.port
    except ValueError as error:
        raise DomainError("invalid_plugin_endpoint") from error
    if (
        parsed.scheme != "http"
        or parsed.hostname != "127.0.0.1"
        or port is None
        or parsed.path != "/mcp"
        or parsed.query
        or parsed.fragment
        or parsed.username
        or parsed.password
        or not 1 <= port <= 65535
    ):
        raise DomainError("invalid_plugin_endpoint")
    return port


def build_id(project: Path) -> str:
    digest = hashlib.sha256()
    for path in [project / "uv.lock", *sorted((project / "src/pmc_mcp").rglob("*.py"))]:
        digest.update(str(path.relative_to(project)).encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()


@contextmanager
def ownership(state: Path):
    secret(state, "bearer.key", 32)  # Validate directory ownership/permissions first.
    fd = os.open(state / "service.lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise DomainError("unsafe_state_file")
        deadline = time.monotonic() + 7
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise DomainError("service_start_busy") from None
                time.sleep(0.05)
        yield
    finally:
        os.close(fd)


def health(client: httpx2.Client, port: int) -> dict | None:
    try:
        response = client.get(f"http://127.0.0.1:{port}/health")
        if response.status_code != 200:
            raise DomainError("service_port_conflict")
        if len(response.content) > 4096:
            raise DomainError("service_port_conflict")
        data = response.json()
        if not isinstance(data, dict) or data.get("name") != "pmc-mcp":
            raise DomainError("service_port_conflict")
        return data
    except (httpx2.ConnectError, httpx2.TimeoutException):
        return None
    except ValueError as error:
        raise DomainError("service_port_conflict") from error


def ensure(state: Path, project: Path, port: int) -> dict[str, str]:
    token = secret(state, "bearer.key", 32).hex()
    identity = secret(state, "service.key", 32).hex()
    build = build_id(project)
    with (
        ownership(state),
        httpx2.Client(
            headers={"Authorization": f"Bearer {token}"},
            timeout=0.3,
            trust_env=False,
            follow_redirects=False,
        ) as client,
    ):
        current = health(client, port)
        if current is not None:
            if current.get("instance_id") != identity:
                raise DomainError("service_port_conflict")
            if current.get("build_id") != build:
                raise DomainError("service_version_conflict")
        else:
            log_fd = os.open(
                state / "service.log", os.O_WRONLY | os.O_CREAT | os.O_APPEND | os.O_NOFOLLOW, 0o600
            )
            with os.fdopen(log_fd, "ab") as log:
                child = subprocess.Popen(
                    [
                        sys.executable,
                        "-m",
                        "pmc_mcp.cli",
                        "plugin-serve",
                        "--state-dir",
                        str(state),
                        "--port",
                        str(port),
                        "--instance-id",
                        identity,
                        "--build-id",
                        build,
                    ],
                    stdin=subprocess.DEVNULL,
                    stdout=log,
                    stderr=log,
                    start_new_session=True,
                )
            deadline = time.monotonic() + 6
            while current is None:
                if child.poll() is not None:
                    raise DomainError("service_start_failed")
                if time.monotonic() >= deadline:
                    child.terminate()
                    child.wait(timeout=2)
                    raise DomainError("service_start_timeout")
                time.sleep(0.05)
                current = health(client, port)
            if current.get("instance_id") != identity or current.get("pid") != child.pid:
                child.terminate()
                raise DomainError("service_port_conflict")
        return {"Authorization": f"Bearer {token}"}


def stop(state: Path, project: Path, port: int) -> None:
    token = secret(state, "bearer.key", 32).hex()
    with (
        ownership(state),
        httpx2.Client(
            headers={"Authorization": f"Bearer {token}"},
            timeout=0.3,
            trust_env=False,
            follow_redirects=False,
        ) as client,
    ):
        current = health(client, port)
        if current is None:
            return
        if current.get("instance_id") != secret(state, "service.key", 32).hex():
            raise DomainError("service_version_conflict")
        pid = current.get("pid")
        if not isinstance(pid, int) or pid <= 1:
            raise DomainError("service_port_conflict")
        os.kill(pid, signal.SIGTERM)
        deadline = time.monotonic() + 5
        while health(client, port) is not None:
            if time.monotonic() >= deadline:
                raise DomainError("service_stop_timeout")
            time.sleep(0.05)


def configure(state: Path, root: Path) -> None:
    from pmc_mcp.adapters.filesystem import LocalFiles

    files = LocalFiles([root])
    try:
        resolved = files.roots[0][1]
    finally:
        files.close()
    with ownership(state):
        temp = state / (".folder-" + secrets.token_hex(16))
        fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(json.dumps({"output_root": str(resolved)}).encode())
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temp, state / "folder.json")
        finally:
            temp.unlink(missing_ok=True)


def configured_root(state: Path) -> str:
    secret(state, "bearer.key", 32)
    try:
        fd = os.open(state / "folder.json", os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        try:
            info = os.fstat(fd)
            if (
                not stat.S_ISREG(info.st_mode)
                or info.st_uid != os.getuid()
                or info.st_mode & 0o077
                or info.st_size > 8192
            ):
                raise DomainError("unsafe_plugin_configuration")
            value = json.loads(os.read(fd, 8193)).get("output_root")
        finally:
            os.close(fd)
        if (
            not isinstance(value, str)
            or not Path(value).is_absolute()
            or ".." in Path(value).parts
            or "\x00" in value
        ):
            raise DomainError("invalid_plugin_configuration")
        return value
    except (OSError, ValueError, AttributeError) as error:
        raise DomainError("plugin_not_configured") from error
