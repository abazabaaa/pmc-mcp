import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor

import pytest

from pmc_mcp.domain.rules import DomainError
from pmc_mcp.state import secret


def test_persistent_secret_and_simultaneous_initialization(tmp_path):
    directory = tmp_path / "state"
    with ThreadPoolExecutor(max_workers=8) as pool:
        values = list(pool.map(lambda _: secret(directory, "signing.key", 32), range(20)))
    assert len(set(values)) == 1
    assert secret(directory, "signing.key", 32) == values[0]
    assert directory.stat().st_mode & 0o077 == 0
    assert (directory / "signing.key").stat().st_mode & 0o077 == 0
    assert not list(directory.glob(".key-*"))


@pytest.mark.parametrize("case", ["directory", "permissions", "symlink", "length", "fifo"])
def test_unsafe_state_is_rejected(tmp_path, case):
    directory = tmp_path / "state"
    directory.mkdir(mode=0o700)
    name = directory / "signing.key"
    if case == "directory":
        directory.chmod(0o755)
    elif case == "permissions":
        name.write_bytes(b"x" * 32)
        name.chmod(0o644)
    elif case == "symlink":
        name.symlink_to(tmp_path / "other")
    elif case == "length":
        name.write_bytes(b"short")
        name.chmod(0o600)
    else:
        os.mkfifo(name, mode=0o600)
    with pytest.raises(DomainError):
        secret(directory, "signing.key", 32)


def test_cli_help_and_init_do_not_print_credentials(tmp_path):
    help_result = subprocess.run(
        [sys.executable, "-m", "pmc_mcp.cli", "--help"], capture_output=True, text=True, check=True
    )
    assert "serve" in help_result.stdout
    result = subprocess.run(
        [sys.executable, "-m", "pmc_mcp.cli", "init", "--state-dir", str(tmp_path / "state")],
        capture_output=True,
        text=True,
        check=True,
    )
    key = (tmp_path / "state/bearer.key").read_bytes()
    assert key.hex() not in result.stdout + result.stderr
    assert "credential_encoding" in result.stdout
