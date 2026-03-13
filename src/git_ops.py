"""Git リポジトリ操作（クローン、チェックアウト、ブランチ比較、マージ）を行うモジュール。"""

import json
import subprocess
from pathlib import Path
from urllib.parse import quote

from claude_runner import setup_claude_settings


def prepare_repository(
    repo: str,
    branch_name: str,
    user_name: str | None = None,
    user_email: str | None = None,
) -> Path:
    """リポジトリをクローンまたは更新し、対象ブランチにチェックアウトする。

    オプションで git config の user.name と user.email をローカルに設定する。
    """
    owner, repo_name = repo.split("/", 1)
    _project_root = Path(__file__).resolve().parent.parent
    works_dir = _project_root / "works" / f"{owner}__{repo_name}"
    works_dir.parent.mkdir(parents=True, exist_ok=True)

    if not works_dir.exists():
        print(f"Cloning {repo}...")
        subprocess.run(
            ["git", "clone", f"https://github.com/{repo}.git", str(works_dir)],
            check=True,
        )
    else:
        print(f"Updating {repo}...")
        # 保留中のマージ/コンフリクトをクリア
        subprocess.run(
            ["git", "reset", "--hard"],
            cwd=works_dir,
            check=True,
        )
        # 前回の PR からのアントラックファイルを除去
        subprocess.run(
            ["git", "clean", "-fd"],
            cwd=works_dir,
            check=True,
        )
        subprocess.run(
            ["git", "fetch", "--all"],
            cwd=works_dir,
            check=True,
        )

    # 以前設定されたローカル ID をクリアし、必要に応じて再設定
    subprocess.run(
        ["git", "config", "--unset-all", "user.name"], cwd=works_dir, check=False
    )
    subprocess.run(
        ["git", "config", "--unset-all", "user.email"], cwd=works_dir, check=False
    )
    if user_name:
        print(f"Setting git user.name to '{user_name}'...")
        subprocess.run(
            ["git", "config", "user.name", user_name],
            cwd=works_dir,
            check=True,
        )
    if user_email:
        print(f"Setting git user.email to '{user_email}'...")
        subprocess.run(
            ["git", "config", "user.email", user_email],
            cwd=works_dir,
            check=True,
        )

    print(f"Checking out branch {branch_name}...")
    subprocess.run(
        ["git", "checkout", branch_name],
        cwd=works_dir,
        check=True,
    )
    # pull 前にクリーンな状態にリセット
    subprocess.run(
        ["git", "reset", "--hard", f"origin/{branch_name}"],
        cwd=works_dir,
        check=True,
    )

    setup_claude_settings(works_dir)

    return works_dir


def get_branch_compare_status(
    repo: str, base_branch: str, current_branch: str
) -> tuple[str, int]:
    """compare API の (status, behind_by) を base...current で返す。"""
    basehead = f"{quote(base_branch, safe='')}...{quote(current_branch, safe='')}"
    result = subprocess.run(
        [
            "gh",
            "api",
            f"repos/{repo}/compare/{basehead}",
        ],
        capture_output=True,
        text=True,
        check=False,
        encoding="utf-8",
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"Error fetching compare status for {repo} ({base_branch}...{current_branch}): "
            f"{result.stderr.strip()}"
        )
    try:
        data = json.loads(result.stdout) if result.stdout else {}
    except json.JSONDecodeError as e:
        raise RuntimeError(
            f"Failed to parse compare status for {repo} ({base_branch}...{current_branch})"
        ) from e
    status = data.get("status")
    behind_by = data.get("behind_by")
    if not isinstance(status, str) or not isinstance(behind_by, int):
        raise RuntimeError(
            f"Unexpected compare payload for {repo} ({base_branch}...{current_branch})"
        )
    return status, behind_by


def needs_base_merge(compare_status: str, behind_by: int) -> bool:
    """ベースブランチのマージが必要かどうか返す。"""
    return behind_by >= 1 or compare_status in {"behind", "diverged"}


def _has_merge_conflicts(works_dir: Path) -> bool:
    """ワーキングツリーにマージコンフリクトが残っているか確認する。"""
    result = subprocess.run(
        ["git", "diff", "--name-only", "--diff-filter=U"],
        cwd=str(works_dir),
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError("failed to detect merge conflicts")
    return bool(result.stdout.strip())


def _merge_base_branch(works_dir: Path, base_branch: str) -> tuple[bool, bool]:
    """origin/<base_branch> を現在のブランチにマージする。

    Returns:
        (merged_changes, has_conflicts)
    """
    subprocess.run(
        ["git", "fetch", "origin", base_branch],
        cwd=str(works_dir),
        check=True,
    )
    # マージ前の HEAD SHA を記録（ロケール非依存の変更検出用）
    pre_merge_head = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=str(works_dir),
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    merge_result = subprocess.run(
        ["git", "merge", "--no-edit", f"origin/{base_branch}"],
        cwd=str(works_dir),
        capture_output=True,
        text=True,
        check=False,
    )
    if merge_result.returncode == 0:
        post_merge_head = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=str(works_dir),
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        merged_changes = pre_merge_head != post_merge_head
        return (merged_changes, False)
    has_conflicts = _has_merge_conflicts(works_dir)
    if has_conflicts:
        return (False, True)
    raise RuntimeError(
        "git merge failed without conflict markers: "
        f"{(merge_result.stderr or merge_result.stdout).strip()}"
    )
