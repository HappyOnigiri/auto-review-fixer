"""CI logging helpers for GitHub Actions."""

import os
import sys

_IS_CI = os.environ.get("GITHUB_ACTIONS") == "true"


def _escape_annotation_message(value: str) -> str:
    """Escape special characters in GitHub Actions annotation message values."""
    return value.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")


def _escape_annotation_property(value: str) -> str:
    """Escape special characters in GitHub Actions annotation property values (e.g. title)."""
    return (
        value.replace("%", "%25")
        .replace("\r", "%0D")
        .replace("\n", "%0A")
        .replace(":", "%3A")
        .replace(",", "%2C")
    )


def log_group(title: str) -> None:
    if _IS_CI:
        print(f"::group::{title}")


def log_endgroup() -> None:
    if _IS_CI:
        print("::endgroup::")


def log_error(message: str, *, title: str = "") -> None:
    """CI では ::error:: アノテーション、ローカルでは stderr に出力。"""
    if _IS_CI:
        title_part = f" title={_escape_annotation_property(title)}" if title else ""
        print(f"::error{title_part}::{_escape_annotation_message(message)}")
    else:
        prefix = f"[{title}] " if title else ""
        print(f"ERROR: {prefix}{message}", file=sys.stderr)


def log_warning(message: str, *, title: str = "") -> None:
    """CI では ::warning:: アノテーション、ローカルでは stderr に出力。"""
    if _IS_CI:
        title_part = f" title={_escape_annotation_property(title)}" if title else ""
        print(f"::warning{title_part}::{_escape_annotation_message(message)}")
    else:
        prefix = f"[{title}] " if title else ""
        print(f"WARNING: {prefix}{message}", file=sys.stderr)
