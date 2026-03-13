"""CI logging helpers for GitHub Actions."""

import os

_IS_CI = os.environ.get("GITHUB_ACTIONS") == "true"


def log_group(title: str) -> None:
    if _IS_CI:
        print(f"::group::{title}")


def log_endgroup() -> None:
    if _IS_CI:
        print("::endgroup::")
