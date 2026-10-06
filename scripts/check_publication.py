"""Fail before publication if tracked files or Git history contain private material."""

import os
import re
import subprocess
import sys
from pathlib import Path

PRIVATE_AREAS = {"reviews", "docs-refresh", ".pmc-mcp", ".venv"}
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
        or address.rsplit("@", 1)[1]
        in {"example.com", "example.org", "example.test", "example.invalid"}
        for address in EMAIL.findall(text)
    )


def main() -> int:
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
        if not safe_text(identity) or "@users.noreply.github.com>" not in identity:
            errors.append("non-anonymous commit identity")
    for error in errors:
        print(error, file=sys.stderr)
    if errors:
        return 1
    print("Publication scan passed: tracked content, history, and commit identities")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
