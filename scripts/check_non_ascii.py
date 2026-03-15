#!/usr/bin/env python3
"""Detect non-ASCII chars in selected documentation and sample config files."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


ASCII_ENFORCED_FILES = {
    "README.md",
    ".env.sample",
    ".refix.sample.yaml",
    ".refix-batch.sample.yaml",
}


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
        if line and line in ASCII_ENFORCED_FILES
    ]


def main() -> None:
    violations: list[str] = []
    for path in _tracked_files():
        if not path.is_file():
            continue
        try:
            text = path.read_bytes().decode("utf-8")
        except UnicodeDecodeError:
            violations.append(f"{path}: invalid UTF-8")
            continue

        for line_no, line in enumerate(text.splitlines(), start=1):
            for col_no, char in enumerate(line, start=1):
                if ord(char) > 127:
                    violations.append(f"{path}:{line_no}:{col_no}: {repr(char)}")
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
