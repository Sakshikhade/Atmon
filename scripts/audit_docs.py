#!/usr/bin/env python3
"""Audit relative markdown links under docs/ and key root markdown files."""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ROOT_FILES = [
    ROOT / "README.md",
    ROOT / "AGENTS.md",
    ROOT / "STATUS.md",
    ROOT / "DESIGN.md",
    ROOT / "CHANGELOG.md",
]
LINK_RE = re.compile(r"\[([^\]]*)\]\(([^)]+)\)")


def iter_md_files() -> list[Path]:
    files = list(ROOT_FILES)
    docs = ROOT / "docs"
    if docs.is_dir():
        files.extend(sorted(docs.rglob("*.md")))
    agents = ROOT / ".agents"
    if agents.is_dir():
        files.extend(sorted(agents.rglob("*.md")))
    return [f for f in files if f.is_file()]


def check_file(path: Path) -> list[str]:
    errors: list[str] = []
    text = path.read_text(encoding="utf-8")
    for match in LINK_RE.finditer(text):
        target = match.group(2).strip()
        if not target or target.startswith(("#", "http://", "https://", "mailto:")):
            continue
        # strip optional title and anchors
        href = target.split()[0].strip("<>")
        href, _, _ = href.partition("#")
        if not href:
            continue
        resolved = (path.parent / href).resolve()
        try:
            resolved.relative_to(ROOT.resolve())
        except ValueError:
            # allow links outside repo? treat as error
            errors.append(f"{path.relative_to(ROOT)}: out-of-repo link → {target}")
            continue
        if not resolved.exists():
            errors.append(f"{path.relative_to(ROOT)}: broken link → {target}")
    return errors


def main() -> int:
    all_errors: list[str] = []
    files = iter_md_files()
    for f in files:
        all_errors.extend(check_file(f))
    if all_errors:
        print(f"audit_docs: {len(all_errors)} broken link(s) in {len(files)} file(s)")
        for err in all_errors:
            print(f"  - {err}")
        return 1
    print(f"audit_docs: OK — {len(files)} markdown files, 0 broken relative links")
    return 0


if __name__ == "__main__":
    sys.exit(main())
