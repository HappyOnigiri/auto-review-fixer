"""Git リポジトリ操作（クローン、チェックアウト、ブランチ比較、マージ）を行うモジュール。"""

import json
import sys
from pathlib import Path
from urllib.parse import quote

from claude_runner import setup_claude_settings
from project_config import run_project_setup
from subprocess_helpers import run_command, run_git


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

    is_first_clone = not works_dir.exists()
    if is_first_clone:
        print(f"Cloning {repo}...")
        run_git(
            "clone",
            f"https://github.com/{repo}.git",
            str(works_dir),
            cwd=works_dir.parent,
            timeout=300,
        )
    else:
        print(f"Updating {repo}...")
        # 保留中のマージ/コンフリクトをクリア
        run_git("reset", "--hard", cwd=works_dir, timeout=30)
        # 前回の PR からのアントラックファイルを除去
        run_git("clean", "-fd", cwd=works_dir, timeout=30)
        run_git("fetch", "--all", cwd=works_dir, timeout=120)

    # 以前設定されたローカル ID をクリアし、必要に応じて再設定
    run_git(
        "config", "--unset-all", "user.name", cwd=works_dir, check=False, timeout=10
    )
    run_git(
        "config", "--unset-all", "user.email", cwd=works_dir, check=False, timeout=10
    )
    if user_name:
        print(f"Setting git user.name to '{user_name}'...")
        run_git("config", "user.name", user_name, cwd=works_dir, timeout=10)
    if user_email:
        print(f"Setting git user.email to '{user_email}'...")
        run_git("config", "user.email", user_email, cwd=works_dir, timeout=10)

    print(f"Checking out branch {branch_name}...")
    run_git("checkout", branch_name, cwd=works_dir, timeout=30)
    # pull 前にクリーンな状態にリセット
    run_git("reset", "--hard", f"origin/{branch_name}", cwd=works_dir, timeout=30)

    setup_claude_settings(works_dir)
    run_project_setup(works_dir, is_first_clone=is_first_clone)

    # setup コマンドが tracked ファイルを変更していないか確認
    setup_dirty = run_git(
        "status",
        "--porcelain",
        cwd=works_dir,
        check=False,
        timeout=10,
    )
    if setup_dirty.stdout.strip():
        dirty_files = setup_dirty.stdout.strip()
        diff_output = ""
        try:
            diff_result = run_git("diff", cwd=works_dir, check=False, timeout=10)
            if diff_result.returncode == 0 and diff_result.stdout.strip():
                diff_output = f"\n{diff_result.stdout.strip()}"
        except Exception:
            pass
        msg = (
            f"Setup commands left tracked files dirty:\n{dirty_files}"
            f"{diff_output}\n"
            "Fix the setup commands in .refix-project.yaml so they do not modify tracked files."
        )
        print(f"Error: {msg}", file=sys.stderr)
        raise RuntimeError(msg)

    return works_dir


def get_branch_compare_status(
    repo: str, base_branch: str, current_branch: str
) -> tuple[str, int]:
    """compare API の (status, behind_by) を base...current で返す。"""
    basehead = f"{quote(base_branch, safe='')}...{quote(current_branch, safe='')}"
    result = run_command(
        [
            "gh",
            "api",
            f"repos/{repo}/compare/{basehead}",
        ],
        check=False,
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


def has_merge_conflicts(works_dir: Path) -> bool:
    """ワーキングツリーにマージコンフリクトが残っているか確認する。"""
    result = run_git(
        "diff", "--name-only", "--diff-filter=U", cwd=works_dir, check=False, timeout=30
    )
    if result.returncode != 0:
        raise RuntimeError("failed to detect merge conflicts")
    return bool(result.stdout.strip())


def merge_base_branch(works_dir: Path, base_branch: str) -> tuple[bool, bool]:
    """origin/<base_branch> を現在のブランチにマージする。

    Returns:
        (merged_changes, has_conflicts)
    """
    run_git("fetch", "origin", base_branch, cwd=works_dir, timeout=120)
    # マージ前の HEAD SHA を記録（ロケール非依存の変更検出用）
    pre_merge_head = run_git(
        "rev-parse", "HEAD", cwd=works_dir, timeout=10
    ).stdout.strip()
    merge_result = run_git(
        "merge",
        "--no-edit",
        f"origin/{base_branch}",
        cwd=works_dir,
        check=False,
        timeout=60,
    )
    if merge_result.returncode == 0:
        post_merge_head = run_git(
            "rev-parse", "HEAD", cwd=works_dir, timeout=10
        ).stdout.strip()
        merged_changes = pre_merge_head != post_merge_head
        return (merged_changes, False)
    has_conflicts = has_merge_conflicts(works_dir)
    if has_conflicts:
        return (False, True)
    raise RuntimeError(
        "git merge failed without conflict markers: "
        f"{(merge_result.stderr or merge_result.stdout).strip()}"
    )
