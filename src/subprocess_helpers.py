"""subprocess 呼び出しの共通ヘルパー。

すべての subprocess 呼び出しにデフォルトタイムアウトを付与し、
ボイラープレートを削減する。
"""

import json
import subprocess
from pathlib import Path
from subprocess import CompletedProcess
from typing import Any

from errors import SubprocessError


def run_command(
    cmd: list[str],
    *,
    cwd: str | Path | None = None,
    timeout: int = 60,
    check: bool = True,
) -> CompletedProcess[str]:
    """汎用コマンド実行ヘルパー。

    capture_output=True, text=True, encoding="utf-8" を常に設定する。
    check=True（デフォルト）かつ returncode != 0 の場合は SubprocessError を送出する。
    タイムアウト時も SubprocessError を送出する。
    """
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=False,
            encoding="utf-8",
            cwd=str(cwd) if cwd is not None else None,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        raise SubprocessError(
            f"Command timed out after {timeout}s: {cmd[0]}",
            returncode=-1,
            stderr="",
        ) from exc
    except (FileNotFoundError, OSError) as exc:
        raise SubprocessError(
            f"Command not found or failed to start: {cmd[0]}",
            returncode=-1,
            stderr=str(exc),
        ) from exc
    if check and result.returncode != 0:
        raise SubprocessError(
            f"Command failed (exit {result.returncode}): {' '.join(cmd[:3])}",
            returncode=result.returncode,
            stderr=result.stderr or "",
        )
    return result


def run_gh(*args: str, timeout: int = 60) -> CompletedProcess[str]:
    """gh コマンドを実行する。失敗時は SubprocessError を送出。"""
    return run_command(["gh", *args], timeout=timeout)


def run_gh_json(*args: str, timeout: int = 60) -> Any:
    """gh コマンドを実行して stdout を JSON パースして返す。"""
    result = run_gh(*args, timeout=timeout)
    try:
        return json.loads(result.stdout) if result.stdout else {}
    except json.JSONDecodeError as exc:
        raise SubprocessError(
            f"Failed to parse JSON from gh {args[0] if args else 'command'} output"
        ) from exc


def _flatten_pages(data: Any) -> list[Any]:
    """--paginate --slurp 応答（ページリスト）を1つのリストに flatten する。

    各ページが list の場合はその要素を extend し、dict の場合はそのまま append する。
    """
    if not isinstance(data, list):
        return []
    items: list[Any] = []
    for page in data:
        if isinstance(page, list):
            items.extend(page)
        elif page is not None:
            items.append(page)
    return items


def run_gh_api(
    endpoint: str,
    *extra_args: str,
    paginate: bool = False,
    timeout: int = 60,
) -> Any:
    """gh api エンドポイントを呼び出して JSON を返す。

    paginate=True の場合は --paginate --slurp を付与し、
    複数ページを flatten して返す。失敗時は SubprocessError を送出する。
    """
    args: list[str] = ["api", endpoint]
    if paginate:
        args += ["--paginate", "--slurp"]
    args += list(extra_args)
    result = run_gh(*args, timeout=timeout)
    try:
        data = json.loads(result.stdout) if result.stdout else ([] if paginate else {})
    except json.JSONDecodeError as exc:
        raise SubprocessError(f"Failed to parse JSON from gh api {endpoint}") from exc
    if not paginate:
        return data
    return _flatten_pages(data)


def run_git(
    *args: str,
    cwd: str | Path,
    timeout: int = 60,
    check: bool = True,
) -> CompletedProcess[str]:
    """git コマンドを実行する。"""
    return run_command(["git", *args], cwd=cwd, timeout=timeout, check=check)
