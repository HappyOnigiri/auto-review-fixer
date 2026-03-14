"""Error accumulator for aggregating errors across multiple repositories and PRs."""

from __future__ import annotations

from dataclasses import dataclass

from ci_log import log_error


@dataclass
class ErrorRecord:
    scope: str  # "owner/repo" or "owner/repo#42"
    message: str


class ErrorCollector:
    def __init__(self) -> None:
        self._errors: list[ErrorRecord] = []

    def add_repo_error(self, repo: str, message: str) -> None:
        self._errors.append(ErrorRecord(scope=repo, message=message))

    def add_pr_error(self, repo: str, pr_number: int, message: str) -> None:
        self._errors.append(ErrorRecord(scope=f"{repo}#{pr_number}", message=message))

    @property
    def has_errors(self) -> bool:
        return len(self._errors) > 0

    def print_summary(self) -> None:
        if not self._errors:
            return
        print(f"\n{'=' * 60}")
        print(f"Error summary ({len(self._errors)} error(s)):")
        for rec in self._errors:
            log_error(rec.message, title=rec.scope)
        print("=" * 60)
