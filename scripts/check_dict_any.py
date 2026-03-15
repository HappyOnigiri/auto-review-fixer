#!/usr/bin/env python3
"""Detect dict[str, Any] / Dict[str, Any] usage in src/ Python files."""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

# Matches dict[str, Any] or Dict[str, Any] (with optional spaces)
_PATTERN = re.compile(
    r"\bdict\s*\[\s*str\s*,\s*Any\s*\]|\bDict\s*\[\s*str\s*,\s*Any\s*\]"
)


def _tracked_src_files() -> list[Path]:
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
        ["git", "-C", str(repo_root), "ls-files", "--full-name", "src/"],
        capture_output=True,
        text=True,
        check=False,
        encoding="utf-8",
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "git ls-files failed")

    return [
        repo_root / line for line in result.stdout.splitlines() if line.endswith(".py")
    ]


def _is_comment_or_string(line: str, match_start: int) -> bool:
    """Return True if the match position is inside a comment or string literal."""
    before = line[:match_start]
    # Comment: anything after '#' (outside strings — simple heuristic)
    stripped = before.lstrip()
    if stripped.startswith("#"):
        return True
    # Inline comment: count quotes to approximate whether we're in a string
    single_quotes = before.count("'") - before.count("\\'")
    double_quotes = before.count('"') - before.count('\\"')
    if single_quotes % 2 == 1 or double_quotes % 2 == 1:
        return True
    # Hash after code: if '#' appears and quote count is even up to that point
    hash_pos = before.find("#")
    if hash_pos != -1:
        pre_hash = before[:hash_pos]
        sq = pre_hash.count("'") - pre_hash.count("\\'")
        dq = pre_hash.count('"') - pre_hash.count('\\"')
        if sq % 2 == 0 and dq % 2 == 0:
            return True
    return False


def main() -> None:
    warn_only = "--warn-only" in sys.argv
    violations: list[str] = []
    for path in _tracked_src_files():
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue

        for line_no, line in enumerate(text.splitlines(), start=1):
            for match in _PATTERN.finditer(line):
                if not _is_comment_or_string(line, match.start()):
                    violations.append(
                        f"{path}:{line_no}: found `{match.group()}` — consider TypedDict or dataclass"
                    )

    if violations:
        print("dict[str, Any] usage detected:")
        for violation in violations:
            print(f"  {violation}")
        if warn_only:
            print("(warn-only: not failing CI)")
            sys.exit(0)
        sys.exit(1)

    print("No dict[str, Any] issues detected.")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # pragma: no cover
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)
