"""CI logging helpers for GitHub Actions."""

import os
import sys

_IS_CI = os.environ.get("GITHUB_ACTIONS") == "true"


def log_group(title: str) -> None:
    if _IS_CI:
        print(f"::group::{title}")


def log_endgroup() -> None:
    if _IS_CI:
        print("::endgroup::")


def log_error(message: str, *, title: str = "") -> None:
    """CI では ::error:: アノテーション、ローカルでは stderr に出力。"""
    if _IS_CI:
        title_part = f" title={title}" if title else ""
        print(f"::error{title_part}::{message}")
    else:
        prefix = f"[{title}] " if title else ""
        print(f"ERROR: {prefix}{message}", file=sys.stderr)


def log_warning(message: str, *, title: str = "") -> None:
    """CI では ::warning:: アノテーション、ローカルでは stderr に出力。"""
    if _IS_CI:
        title_part = f" title={title}" if title else ""
        print(f"::warning{title_part}::{message}")
    else:
        prefix = f"[{title}] " if title else ""
        print(f"WARNING: {prefix}{message}", file=sys.stderr)
