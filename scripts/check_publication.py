"""Fail before publication if tracked files or Git history contain private material."""

import argparse
import os
import re
import runpy
import subprocess
import sys
import tarfile
import zipfile
from pathlib import Path

PRIVATE_AREAS = {
    "reviews",
    "docs-refresh",
    ".pmc-mcp",
    ".venv",
    ".claude",
    ".codex",
    "downloads",
    "plugin-evidence",
}
PRIVATE_FILES = {"PLAN.md", "OBSERVATIONS.md", ".env"}
PATTERNS = (
    re.compile(r"/(?:Users|home)/[A-Za-z0-9_.-]+/"),
    re.compile(r"\b(?:ghp_|github_pat_)[A-Za-z0-9_]{20,}"),
    re.compile(r"\bAKIA[A-Z0-9]{16}\b"),
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
)
EMAIL = re.compile(r"\b[A-Za-z0-9_.+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b")
MARKERS = tuple(x.lower() for x in os.environ.get("PMCMCP_PRIVATE_MARKERS", "").split("|") if x)


def git(*args: str) -> str:
    return subprocess.check_output(["git", *args], text=True)


def safe_text(text: str) -> bool:
    if any(pattern.search(text) for pattern in PATTERNS):
        return False
    if any(marker in text.lower() for marker in MARKERS):
        return False
    return all(
        address.endswith("@users.noreply.github.com")
        or address == "noreply@github.com"
        or address.rsplit("@", 1)[1]
        in {"example.com", "example.org", "example.test", "example.invalid"}
        for address in EMAIL.findall(text)
    )


def allowed_member(name: str, *, wheel: bool) -> bool:
    path = Path(name)
    if path.is_absolute() or ".." in path.parts:
        return False
    if wheel:
        return (name.startswith("pmc_mcp/") and name.endswith(".py")) or (
            len(path.parts) == 2 and path.parts[0].endswith(".dist-info")
        )
    relative = str(Path(*path.parts[1:]))
    return relative in {"pyproject.toml", "README.md", "PKG-INFO"} or (
        relative.startswith("src/pmc_mcp/") and name.endswith(".py")
    )


def check_archives(errors: list[str]) -> None:
    archives = [
        *Path("dist").glob("*.whl"),
        *Path("dist").glob("*.tar.gz"),
        *Path("dist").glob("*.zip"),
    ]
    packager = runpy.run_path(str(Path(__file__).with_name("package_plugin.py")))
    plugin_names = {
        str(p.relative_to(packager["ROOT"])) for p in packager["files"](packager["ROOT"])
    }
    if not any(a.name == "pmc-mcp-plugin.zip" for a in archives):
        errors.append("build the plugin ZIP before archive scanning")
    if not any(a.suffix == ".whl" for a in archives) or not any(
        a.name.endswith(".tar.gz") for a in archives
    ):
        errors.append("build both wheel and sdist before archive scanning")
    for archive in archives:
        if archive.suffix in {".whl", ".zip"}:
            with zipfile.ZipFile(archive) as wheel:
                entries = [(n, wheel.read(n)) for n in wheel.namelist() if not n.endswith("/")]
        else:
            with tarfile.open(archive) as sdist:
                entries = []
                for member in sdist.getmembers():
                    if member.isdir():
                        continue
                    file = sdist.extractfile(member) if member.isfile() else None
                    if file is None:
                        errors.append(f"non-regular archive member: {archive.name}")
                        continue
                    entries.append((member.name, file.read()))
        for name, body in entries:
            allowed = (
                name in plugin_names
                if archive.suffix == ".zip"
                else allowed_member(name, wheel=archive.suffix == ".whl")
            )
            if not allowed or not safe_text(name):
                errors.append(f"unexpected archive member: {archive.name}: {name}")
            try:
                content = body.decode("utf-8")
            except UnicodeError:
                errors.append(f"binary archive member: {archive.name}: {name}")
                continue
            if not safe_text(content):
                errors.append(f"private archive content: {archive.name}: {name}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--archives", action="store_true")
    args = parser.parse_args()
    errors: list[str] = []
    for name in git("ls-files").splitlines():
        path = Path(name)
        if path.parts[0] in PRIVATE_AREAS or path.name in PRIVATE_FILES:
            errors.append(f"private file tracked: {name}")
        if path.is_file() and not safe_text(path.read_text(errors="replace")):
            errors.append(f"private content: {name}")
    for line in git("rev-list", "--objects", "--all").splitlines():
        oid, _, name = line.partition(" ")
        if git("cat-file", "-t", oid).strip() != "blob":
            continue
        if not safe_text(git("cat-file", "-p", oid)):
            errors.append(f"private content in history: {name or oid}")
    for identity in git("log", "--all", "--format=%an <%ae>%n%cn <%ce>").splitlines():
        if not safe_text(identity) or not identity.endswith(
            ("@users.noreply.github.com>", "<noreply@github.com>")
        ):
            errors.append("non-anonymous commit identity")
    if args.archives:
        check_archives(errors)
    for error in errors:
        print(error, file=sys.stderr)
    if errors:
        return 1
    print("Publication scan passed: content, history, identities, and requested archives")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
