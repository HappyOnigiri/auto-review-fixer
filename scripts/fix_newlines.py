#!/usr/bin/env python3
"""Normalize text file newlines to LF and ensure trailing newline."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

SKIP_PREFIXES = (".git/", ".venv/", "venv/", "works/", "tmp/")


def _is_text_file(raw: bytes) -> bool:
    if b"\x00" in raw:
        return False
    try:
        raw.decode("utf-8")
        return True
    except UnicodeDecodeError:
        return False


def _normalize_newlines(text: str) -> str:
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    if normalized and not normalized.endswith("\n"):
        normalized += "\n"
    return normalized


def _tracked_files() -> list[Path]:
    result = subprocess.run(
        ["git", "ls-files"],
        capture_output=True,
        text=True,
        check=False,
        encoding="utf-8",
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "git ls-files failed")
    paths: list[Path] = []
    for line in result.stdout.splitlines():
        if not line:
            continue
        if line.startswith(SKIP_PREFIXES):
            continue
        paths.append(Path(line))
    return paths


def main() -> None:
    updated: list[str] = []
    for rel_path in _tracked_files():
        if not rel_path.is_file():
            continue
        raw = rel_path.read_bytes()
        if not _is_text_file(raw):
            continue
        text = raw.decode("utf-8")
        normalized = _normalize_newlines(text)
        if normalized == text:
            continue
        rel_path.write_text(normalized, encoding="utf-8", newline="\n")
        updated.append(str(rel_path))

    if updated:
        print(f"Normalized newlines in {len(updated)} file(s):")
        for path in updated:
            print(f"  - {path}")
    else:
        print("No newline fixes needed.")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # pragma: no cover
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)
