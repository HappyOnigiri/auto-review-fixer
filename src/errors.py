"""Refix 固有の例外クラス。"""


class RefixError(RuntimeError):
    """Refix の基底例外クラス。"""


class ConfigError(RefixError):
    """設定ファイルのエラー。"""


class SubprocessError(RefixError):
    """subprocess 呼び出しが失敗した際に送出される例外。"""

    def __init__(self, message: str, *, returncode: int = -1, stderr: str = "") -> None:
        super().__init__(message)
        self.returncode = returncode
        self.stderr = stderr

    def __str__(self) -> str:
        base = super().__str__()
        stderr = self.stderr.strip()
        if stderr and stderr not in base:
            return f"{base}\nstderr: {stderr}"
        return base


class GitHubAPIError(SubprocessError):
    """GitHub API 呼び出しのエラー。"""


class GitError(SubprocessError):
    """Git 操作のエラー。"""


class ProjectConfigError(RefixError):
    """プロジェクト設定ファイル（.refix.yaml）のエラー。"""
