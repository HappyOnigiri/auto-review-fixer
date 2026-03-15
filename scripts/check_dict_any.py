#!/usr/bin/env python3
"""Detect dict[str, Any] / Dict[str, Any] usage in src/ Python files."""

from __future__ import annotations

import io
import subprocess
import sys
import tokenize
from pathlib import Path


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


def _check_file(path: Path) -> list[str]:
    """Return violation strings for dict[str, Any] found in real code tokens."""
    try:
        source = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return []

    violations: list[str] = []
    try:
        tokens = list(tokenize.generate_tokens(io.StringIO(source).readline))
    except tokenize.TokenError:
        return []

    # Lines that carry a "# dict-any: ok" in a COMMENT token
    ok_lines: set[int] = set()
    for tok in tokens:
        if tok.type == tokenize.COMMENT and "# dict-any: ok" in tok.string:
            ok_lines.add(tok.start[0])

    # Detect NAME/OP/NAME/OP/NAME/OP token sequences for dict[str, Any]
    # Sequence: NAME('dict'|'Dict') OP('[') NAME('str') OP(',') NAME('Any') OP(']')
    for i, tok in enumerate(tokens):
        if tok.type != tokenize.NAME or tok.string not in ("dict", "Dict"):
            continue
        rest = tokens[i + 1 : i + 6]
        if len(rest) < 5:
            continue
        types = [t.type for t in rest]
        strings = [t.string for t in rest]
        if types == [
            tokenize.OP,
            tokenize.NAME,
            tokenize.OP,
            tokenize.NAME,
            tokenize.OP,
        ] and strings == ["[", "str", ",", "Any", "]"]:
            line_no = tok.start[0]
            if line_no not in ok_lines:
                matched = tok.string + "".join(strings)
                violations.append(
                    f"{path}:{line_no}: found `{matched}` — consider TypedDict or dataclass"
                )
    return violations


def main() -> None:
    warn_only = "--warn-only" in sys.argv
    violations: list[str] = []
    for path in _tracked_src_files():
        if not path.is_file():
            continue
        violations.extend(_check_file(path))

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
