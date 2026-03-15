"""issue_comment イベントのフィルタリング処理。

GitHub Actions の issue_comment トリガー時に、後続ステップをスキップするか判断する。
- PR コメントでない場合（issue コメント）: skip
- config の triggers.issue_comment.authors に含まれない author: skip
- config 未設定のデフォルト: coderabbitai[bot] のみ許可
"""

import json
import os
from pathlib import Path

from config import load_config_for_action

DEFAULT_ALLOWED_AUTHOR = "coderabbitai[bot]"


def filter_event(
    event_path: str,
    config_path: str | None,
    github_output: str | None,
) -> bool:
    """イベントをフィルタリングし、skip=true/false を GITHUB_OUTPUT に書き込む。

    Returns:
        True  = スキップすべき（後続ステップを実行しない）
        False = 続行すべき（後続ステップを実行する）
    """
    event = json.loads(Path(event_path).read_text(encoding="utf-8"))

    # PR コメントかチェック（issue コメントはスキップ）
    if not event.get("issue", {}).get("pull_request"):
        _write_output(github_output, skip=True)
        print("Skipping: comment is not on a PR")
        return True

    author: str = event.get("comment", {}).get("user", {}).get("login", "")

    cfg = load_config_for_action(config_path)
    allowed_authors: list[str] = (
        cfg.get("triggers", {}).get("issue_comment", {}).get("authors", [])
    )

    if allowed_authors:
        if author in allowed_authors:
            _write_output(github_output, skip=False)
            return False
        _write_output(github_output, skip=True)
        print(f"Skipping: {author} not in allowed authors")
        return True

    # config に triggers 設定がない場合のデフォルト: coderabbitai[bot] のみ許可
    if author == DEFAULT_ALLOWED_AUTHOR:
        _write_output(github_output, skip=False)
        return False
    _write_output(github_output, skip=True)
    print(f"Skipping: {author} (default: only {DEFAULT_ALLOWED_AUTHOR})")
    return True


def _write_output(github_output: str | None, *, skip: bool) -> None:
    """skip=true/false を GITHUB_OUTPUT ファイルに追記する。"""
    value = "true" if skip else "false"
    if github_output:
        with open(github_output, "a", encoding="utf-8") as f:
            f.write(f"skip={value}\n")


def main() -> None:
    event_path = os.environ["GITHUB_EVENT_PATH"]
    config_path = os.environ.get("REFIX_CONFIG_PATH") or None
    github_output = os.environ.get("GITHUB_OUTPUT")

    filter_event(event_path, config_path, github_output)


if __name__ == "__main__":
    main()
