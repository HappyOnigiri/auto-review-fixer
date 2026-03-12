#!/usr/bin/env python3
"""Detect non-ASCII chars in infrastructure files."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


ASCII_ENFORCED_FILES = {
    "requirements.txt",
    "scripts/ci.py",
    "scripts/check_non_ascii.py",
    "scripts/fix_newlines.py",
}


def _is_target_file(path: str) -> bool:
    if path.startswith(".github/workflows/"):
        return True
    if path in ASCII_ENFORCED_FILES:
        return True
    return False


def _tracked_files() -> list[Path]:
    rev_parse = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        capture_output=True,
        text=True,
        check=False,
        encoding="utf-8",
    )
    if rev_parse.returncode != 0:
        raise RuntimeError(rev_parse.stderr.strip() or "git rev-parse failed")
    repo_root = Path(rev_parse.stdout.strip())

    result = subprocess.run(
        ["git", "-C", str(repo_root), "ls-files", "--full-name"],
        capture_output=True,
        text=True,
        check=False,
        encoding="utf-8",
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "git ls-files failed")
    return [
        repo_root / line
        for line in result.stdout.splitlines()
        if line and _is_target_file(line)
    ]


def main() -> None:
    violations: list[str] = []
    for rel_path in _tracked_files():
        if not rel_path.is_file():
            continue
        try:
            text = rel_path.read_bytes().decode("utf-8")
        except UnicodeDecodeError:
            violations.append(f"{rel_path}: invalid UTF-8")
            continue
        for line_no, line in enumerate(text.splitlines(), start=1):
            for col_no, ch in enumerate(line, start=1):
                if ord(ch) > 127:
                    violations.append(f"{rel_path}:{line_no}:{col_no}: {repr(ch)}")
                    break

    if violations:
        print("Non-ASCII characters detected:")
        for violation in violations:
            print(f"  {violation}")
        sys.exit(1)

    print("No non-ASCII issues detected.")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # pragma: no cover
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)
