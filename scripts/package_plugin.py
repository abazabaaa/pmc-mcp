"""Build a plugin ZIP from an explicit public-file allowlist."""

import argparse
import shutil
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FIXED = (
    "README.md",
    "pyproject.toml",
    "uv.lock",
    ".mcp.json",
    "codex.mcp.json",
    ".claude-plugin/plugin.json",
    ".claude-plugin/marketplace.json",
    ".codex-plugin/plugin.json",
    ".agents/plugins/marketplace.json",
    "scripts/bootstrap.sh",
    "scripts/install-claude.sh",
    "scripts/install-codex.sh",
    "scripts/package_plugin.py",
    "docs/plugins.md",
)


def files(root: Path) -> list[Path]:
    return [root / n for n in FIXED] + [
        *sorted((root / "src/pmc_mcp").rglob("*.py")),
        *sorted((root / "skills").rglob("SKILL.md")),
    ]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--directory", type=Path)
    parser.add_argument("--zip", type=Path, default=ROOT / "dist/pmc-mcp-plugin.zip")
    args = parser.parse_args()
    selected = files(ROOT)
    if args.directory:
        for source in selected:
            target = args.directory / source.relative_to(ROOT)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
    else:
        args.zip.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(args.zip, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for source in selected:
                archive.write(source, str(source.relative_to(ROOT)))
        print(f"Plugin ZIP built: {len(selected)} public files")


if __name__ == "__main__":
    main()
