"""Helpers for detecting Claude usage-limit failures."""


class ClaudeCommandFailedError(RuntimeError):
    """Raised when Claude command exits with a non-zero status."""

    def __init__(
        self,
        *,
        phase: str,
        returncode: int,
        stdout: str = "",
        stderr: str = "",
    ) -> None:
        super().__init__(f"Claude command failed during {phase} (exit {returncode})")
        self.phase = phase
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


class ClaudeUsageLimitError(ClaudeCommandFailedError):
    """Raised when Claude reports account usage limit exhaustion."""

    def __init__(
        self,
        *,
        phase: str,
        returncode: int = 1,
        stdout: str = "",
        stderr: str = "",
    ) -> None:
        super().__init__(
            phase=phase,
            returncode=returncode,
            stdout=stdout,
            stderr=stderr,
        )


_USAGE_LIMIT_MARKERS = (
    "you've hit your limit",
    "claude usage limit reached",
    "claude code usage limit reached",
    "usage limit reached",
)


def is_claude_usage_limit_error(*texts: str) -> bool:
    """Return True when command output indicates Claude usage limit."""
    combined = "\n".join(text for text in texts if text).lower()
    if not combined.strip():
        return False
    return any(marker in combined for marker in _USAGE_LIMIT_MARKERS)
