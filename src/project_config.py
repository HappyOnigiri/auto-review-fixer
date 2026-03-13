"""対象リポジトリの .refix-project.yaml を読み込み、セットアップコマンドを実行するモジュール。"""

from pathlib import Path

import yaml

from errors import ProjectConfigError
from subprocess_helpers import run_command

CONFIG_FILENAME = ".refix-project.yaml"
SUPPORTED_VERSIONS = {1}
SETUP_COMMAND_TIMEOUT = 300
VALID_WHEN_VALUES = {"always", "clone_only"}


def load_project_config(repo_root: Path) -> dict | None:
    """リポジトリルートの .refix-project.yaml を読み込む。

    ファイルが存在しない場合は None を返す。
    パースエラーや検証エラーの場合は ProjectConfigError を送出する。
    """
    config_path = repo_root / CONFIG_FILENAME
    if not config_path.exists():
        return None

    try:
        raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ProjectConfigError(
            f"{CONFIG_FILENAME}: YAML パースエラー: {exc}"
        ) from exc

    if not isinstance(raw, dict):
        raise ProjectConfigError(
            f"{CONFIG_FILENAME}: ルートはマッピング（dict）でなければなりません"
        )

    version = raw.get("version", 1)
    if version not in SUPPORTED_VERSIONS:
        raise ProjectConfigError(
            f"{CONFIG_FILENAME}: サポートされていない version: {version!r}"
        )

    setup = _parse_setup(raw)
    return {"version": version, "setup": setup}


def _parse_setup(raw: dict) -> dict:
    """setup セクションを検証して正規化した dict を返す。"""
    setup = raw.get("setup")
    if setup is None:
        return {"when": "always", "commands": []}

    if not isinstance(setup, dict):
        raise ProjectConfigError(
            f"{CONFIG_FILENAME}: setup はマッピングでなければなりません"
        )

    when = setup.get("when", "always")
    if when not in VALID_WHEN_VALUES:
        raise ProjectConfigError(
            f"{CONFIG_FILENAME}: setup.when の値が不正です: {when!r}"
            f" (有効な値: {sorted(VALID_WHEN_VALUES)})"
        )

    commands = setup.get("commands")
    if commands is None:
        return {"when": when, "commands": []}

    if not isinstance(commands, list):
        raise ProjectConfigError(
            f"{CONFIG_FILENAME}: setup.commands はリストでなければなりません"
        )

    result: list[dict] = []
    for i, entry in enumerate(commands):
        if not isinstance(entry, dict):
            raise ProjectConfigError(
                f"{CONFIG_FILENAME}: setup.commands[{i}] はマッピングでなければなりません"
            )
        run_str = entry.get("run")
        if not isinstance(run_str, str) or not run_str.strip():
            raise ProjectConfigError(
                f"{CONFIG_FILENAME}: setup.commands[{i}] には非空の run フィールドが必要です"
            )
        normalized: dict = {"run": run_str}
        name = entry.get("name")
        if name is not None:
            if not isinstance(name, str):
                raise ProjectConfigError(
                    f"{CONFIG_FILENAME}: setup.commands[{i}].name は文字列でなければなりません"
                )
            normalized["name"] = name
        result.append(normalized)

    return {"when": when, "commands": result}


def run_project_setup(repo_root: Path, *, is_first_clone: bool) -> None:
    """リポジトリルートの .refix-project.yaml に定義されたセットアップコマンドを実行する。

    is_first_clone=True の場合は初回クローン直後、False の場合は既存リポジトリの更新後。
    setup.when が "clone_only" のときは is_first_clone=True のときのみ実行する。
    ファイルが存在しない場合や commands が空の場合は何もしない。
    コマンドが失敗した場合は SubprocessError を送出する。
    """
    config = load_project_config(repo_root)
    if config is None:
        return

    setup = config["setup"]
    if setup["when"] == "clone_only" and not is_first_clone:
        return

    commands = setup["commands"]
    if not commands:
        return

    for cmd in commands:
        run_str = cmd["run"]
        name = cmd.get("name")
        if name:
            print(f"Running setup command: {name} ({run_str})")
        else:
            print(f"Running setup command: {run_str}")
        run_command(["sh", "-c", run_str], cwd=repo_root, timeout=SETUP_COMMAND_TIMEOUT)
