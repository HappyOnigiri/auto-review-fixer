"""PR ラベルの作成・設定・管理を行うモジュール。"""

import json
import sys
from typing import cast
from urllib.parse import quote

from subprocess_helpers import SubprocessError, run_command, run_gh_api

from ci_check import are_all_ci_checks_successful
from type_defs import GitHubComment, PRData
from error_collector import ErrorCollector
from coderabbit import contains_coderabbit_processing_marker, has_coderabbit_comments
from pr_reviewer import fetch_issue_comments, fetch_pr_review_comments

# --- ラベル定数 ---
REFIX_RUNNING_LABEL = "refix: running"
REFIX_DONE_LABEL = "refix: done"
REFIX_MERGED_LABEL = "refix: merged"
REFIX_AUTO_MERGE_REQUESTED_LABEL = "refix: auto-merge-requested"
REFIX_CI_PENDING_LABEL = "refix: ci-pending"

PR_LABEL_KEY_TO_NAME: dict[str, str] = {
    "running": REFIX_RUNNING_LABEL,
    "done": REFIX_DONE_LABEL,
    "merged": REFIX_MERGED_LABEL,
    "auto_merge_requested": REFIX_AUTO_MERGE_REQUESTED_LABEL,
    "ci_pending": REFIX_CI_PENDING_LABEL,
}
PR_LABEL_NAME_TO_KEY: dict[str, str] = {
    label_name: label_key for label_key, label_name in PR_LABEL_KEY_TO_NAME.items()
}
DEFAULT_ENABLED_PR_LABEL_KEYS: tuple[str, ...] = tuple(PR_LABEL_KEY_TO_NAME.keys())

# --- ラベルカラー ---
REFIX_RUNNING_LABEL_COLOR = "FBCA04"
REFIX_DONE_LABEL_COLOR = "0E8A16"
REFIX_MERGED_LABEL_COLOR = "5319E7"
REFIX_AUTO_MERGE_REQUESTED_LABEL_COLOR = "C2E0C6"
REFIX_CI_PENDING_LABEL_COLOR = "D4C5F9"  # 薄紫


def _pr_ref(repo: str, pr_number: int) -> str:
    """ログ向けの PR 識別子を返す。"""
    return f"{repo} PR #{pr_number}"


def _resolve_enabled_pr_label_keys(
    enabled_pr_label_keys: set[str] | None = None,
) -> set[str]:
    """有効な PR ラベルキーセットを解決する。None の場合はデフォルトを返す。"""
    if enabled_pr_label_keys is None:
        return set(DEFAULT_ENABLED_PR_LABEL_KEYS)
    return {
        label_key
        for label_key in enabled_pr_label_keys
        if label_key in PR_LABEL_KEY_TO_NAME
    }


def _ensure_repo_label_exists(
    repo: str,
    label: str,
    *,
    color: str,
    description: str,
    error_collector: ErrorCollector | None = None,
) -> bool:
    """リポジトリにラベルが存在しなければ作成する。"""
    encoded_label = quote(label, safe="")
    get_cmd = ["gh", "api", f"repos/{repo}/labels/{encoded_label}"]
    try:
        get_result = run_command(get_cmd, check=False)
    except SubprocessError as exc:
        msg = f"failed to check label '{label}' on {repo}: {exc}"
        print(f"Warning: {msg}", file=sys.stderr)
        if error_collector:
            error_collector.add_repo_error(repo, msg)
        return False
    if get_result.returncode == 0:
        return True

    stderr_lower = (get_result.stderr or "").lower()
    not_found = "not found" in stderr_lower or "404" in stderr_lower
    if not not_found:
        msg = f"failed to verify label '{label}' on {repo}: {(get_result.stderr or '').strip()}"
        print(f"Warning: {msg}", file=sys.stderr)
        if error_collector:
            error_collector.add_repo_error(repo, msg)
        return False

    create_cmd = [
        "gh",
        "api",
        f"repos/{repo}/labels",
        "-X",
        "POST",
        "-f",
        f"name={label}",
        "-f",
        f"color={color}",
        "-f",
        f"description={description}",
    ]
    try:
        create_result = run_command(create_cmd, check=False)
    except SubprocessError as exc:
        msg = f"failed to create label '{label}' in {repo}: {exc}"
        print(f"Warning: {msg}", file=sys.stderr)
        if error_collector:
            error_collector.add_repo_error(repo, msg)
        return False
    if create_result.returncode == 0:
        print(f"Created missing label '{label}' in {repo}")
        return True

    create_stderr = (create_result.stderr or "").lower()
    if "already_exists" in create_stderr or "already exists" in create_stderr:
        return True

    msg = f"failed to create label '{label}' in {repo}: {(create_result.stderr or '').strip()}"
    print(f"Warning: {msg}", file=sys.stderr)
    if error_collector:
        error_collector.add_repo_error(repo, msg)
    return False


def _ensure_refix_labels(
    repo: str,
    *,
    enabled_pr_label_keys: set[str] | None = None,
    error_collector: ErrorCollector | None = None,
) -> None:
    """必要な refix ラベルをリポジトリに作成する。"""
    enabled = _resolve_enabled_pr_label_keys(enabled_pr_label_keys)
    if "running" in enabled:
        _ensure_repo_label_exists(
            repo,
            REFIX_RUNNING_LABEL,
            color=REFIX_RUNNING_LABEL_COLOR,
            description="Refix is currently processing review fixes.",
            error_collector=error_collector,
        )
    if "done" in enabled:
        _ensure_repo_label_exists(
            repo,
            REFIX_DONE_LABEL,
            color=REFIX_DONE_LABEL_COLOR,
            description="Refix finished review checks/fixes for now.",
            error_collector=error_collector,
        )
    if "merged" in enabled:
        _ensure_repo_label_exists(
            repo,
            REFIX_MERGED_LABEL,
            color=REFIX_MERGED_LABEL_COLOR,
            description="PR has been merged after Refix auto-merge.",
            error_collector=error_collector,
        )
    if "auto_merge_requested" in enabled:
        _ensure_repo_label_exists(
            repo,
            REFIX_AUTO_MERGE_REQUESTED_LABEL,
            color=REFIX_AUTO_MERGE_REQUESTED_LABEL_COLOR,
            description="Refix has requested auto-merge for this PR.",
            error_collector=error_collector,
        )
    if "ci_pending" in enabled:
        _ensure_repo_label_exists(
            repo,
            REFIX_CI_PENDING_LABEL,
            color=REFIX_CI_PENDING_LABEL_COLOR,
            description="Refix is waiting for CI checks to complete.",
            error_collector=error_collector,
        )


def edit_pr_label(
    repo: str,
    pr_number: int,
    *,
    add: bool,
    label: str,
    enabled_pr_label_keys: set[str] | None = None,
    error_collector: ErrorCollector | None = None,
) -> bool:
    """PR にラベルを追加または削除する。"""
    enabled = _resolve_enabled_pr_label_keys(enabled_pr_label_keys)
    label_key = PR_LABEL_NAME_TO_KEY.get(label)
    if label_key is not None and label_key not in enabled:
        return False

    label_arg = "--add-label" if add else "--remove-label"
    cmd = [
        "gh",
        "pr",
        "edit",
        str(pr_number),
        "--repo",
        repo,
        label_arg,
        label,
    ]
    try:
        result = run_command(cmd, check=False)
    except SubprocessError as exc:
        action = "add" if add else "remove"
        msg = f"failed to {action} label '{label}' on {_pr_ref(repo, pr_number)}: {exc}"
        print(f"Warning: {msg}", file=sys.stderr)
        if error_collector:
            error_collector.add_pr_error(repo, pr_number, msg)
        return False
    if result.returncode == 0:
        return True

    stderr_lower = (result.stderr or "").lower()
    if (
        not add
        and "label" in stderr_lower
        and ("not found" in stderr_lower or "does not have" in stderr_lower)
    ):
        return True

    action = "add" if add else "remove"
    msg = (
        f"failed to {action} label '{label}' on {_pr_ref(repo, pr_number)}: "
        f"{(result.stderr or '').strip()}"
    )
    print(f"Warning: {msg}", file=sys.stderr)
    if error_collector:
        error_collector.add_pr_error(repo, pr_number, msg)
    return False


def _pr_has_label(pr_data: PRData, label_name: str) -> bool:
    """PR に指定ラベルが付いているか判定する。"""
    labels = pr_data.get("labels", [])
    if not isinstance(labels, list):
        return False
    for label in labels:
        if isinstance(label, dict) and str(label.get("name", "")).strip() == label_name:
            return True
    return False


def set_pr_running_label(
    repo: str,
    pr_number: int,
    *,
    pr_data: PRData | None = None,
    enabled_pr_label_keys: set[str] | None = None,
    error_collector: ErrorCollector | None = None,
) -> bool:
    """refix: running を設定し、refix: done を削除する。"""
    enabled = _resolve_enabled_pr_label_keys(enabled_pr_label_keys)
    running_enabled = "running" in enabled
    done_enabled = "done" in enabled
    if not running_enabled and not done_enabled:
        if "ci_pending" in enabled:
            _ensure_refix_labels(
                repo, enabled_pr_label_keys=enabled, error_collector=error_collector
            )
        return False
    if (
        pr_data
        and (not running_enabled or _pr_has_label(pr_data, REFIX_RUNNING_LABEL))
        and (not done_enabled or not _pr_has_label(pr_data, REFIX_DONE_LABEL))
    ):
        return False
    if enabled_pr_label_keys is None:
        _ensure_refix_labels(repo, error_collector=error_collector)
    else:
        _ensure_refix_labels(
            repo, enabled_pr_label_keys=enabled, error_collector=error_collector
        )
    changed = False
    if done_enabled and (pr_data is None or _pr_has_label(pr_data, REFIX_DONE_LABEL)):
        if enabled_pr_label_keys is None:
            if edit_pr_label(
                repo,
                pr_number,
                add=False,
                label=REFIX_DONE_LABEL,
                error_collector=error_collector,
            ):
                changed = True
        else:
            if edit_pr_label(
                repo,
                pr_number,
                add=False,
                label=REFIX_DONE_LABEL,
                enabled_pr_label_keys=enabled,
                error_collector=error_collector,
            ):
                changed = True
    if running_enabled and (
        pr_data is None or not _pr_has_label(pr_data, REFIX_RUNNING_LABEL)
    ):
        if enabled_pr_label_keys is None:
            if edit_pr_label(
                repo,
                pr_number,
                add=True,
                label=REFIX_RUNNING_LABEL,
                error_collector=error_collector,
            ):
                changed = True
        else:
            if edit_pr_label(
                repo,
                pr_number,
                add=True,
                label=REFIX_RUNNING_LABEL,
                enabled_pr_label_keys=enabled,
                error_collector=error_collector,
            ):
                changed = True
    return changed


def _set_pr_done_label(
    repo: str,
    pr_number: int,
    *,
    pr_data: PRData | None = None,
    enabled_pr_label_keys: set[str] | None = None,
    error_collector: ErrorCollector | None = None,
) -> bool:
    """refix: done を設定し、refix: running を削除する。"""
    enabled = _resolve_enabled_pr_label_keys(enabled_pr_label_keys)
    done_enabled = "done" in enabled
    running_enabled = "running" in enabled
    if not done_enabled and not running_enabled:
        return False
    if (
        pr_data
        and (not done_enabled or _pr_has_label(pr_data, REFIX_DONE_LABEL))
        and (not running_enabled or not _pr_has_label(pr_data, REFIX_RUNNING_LABEL))
    ):
        return False
    if enabled_pr_label_keys is None:
        _ensure_refix_labels(repo, error_collector=error_collector)
    else:
        _ensure_refix_labels(
            repo, enabled_pr_label_keys=enabled, error_collector=error_collector
        )
    changed = False
    if running_enabled and (
        pr_data is None or _pr_has_label(pr_data, REFIX_RUNNING_LABEL)
    ):
        if enabled_pr_label_keys is None:
            if edit_pr_label(
                repo,
                pr_number,
                add=False,
                label=REFIX_RUNNING_LABEL,
                error_collector=error_collector,
            ):
                changed = True
        else:
            if edit_pr_label(
                repo,
                pr_number,
                add=False,
                label=REFIX_RUNNING_LABEL,
                enabled_pr_label_keys=enabled,
                error_collector=error_collector,
            ):
                changed = True
    if done_enabled and (
        pr_data is None or not _pr_has_label(pr_data, REFIX_DONE_LABEL)
    ):
        if enabled_pr_label_keys is None:
            if edit_pr_label(
                repo,
                pr_number,
                add=True,
                label=REFIX_DONE_LABEL,
                error_collector=error_collector,
            ):
                changed = True
        else:
            if edit_pr_label(
                repo,
                pr_number,
                add=True,
                label=REFIX_DONE_LABEL,
                enabled_pr_label_keys=enabled,
                error_collector=error_collector,
            ):
                changed = True
    return changed


def _set_pr_merged_label(
    repo: str,
    pr_number: int,
    *,
    enabled_pr_label_keys: set[str] | None = None,
    error_collector: ErrorCollector | None = None,
) -> bool:
    """refix: merged を設定し、refix: running と refix: auto-merge-requested を削除する。"""
    enabled = _resolve_enabled_pr_label_keys(enabled_pr_label_keys)
    if not (
        "running" in enabled or "auto_merge_requested" in enabled or "merged" in enabled
    ):
        return False
    changed = False
    if enabled_pr_label_keys is None:
        _ensure_refix_labels(repo, error_collector=error_collector)
        if edit_pr_label(
            repo,
            pr_number,
            add=False,
            label=REFIX_RUNNING_LABEL,
            error_collector=error_collector,
        ):
            changed = True
        if edit_pr_label(
            repo,
            pr_number,
            add=False,
            label=REFIX_AUTO_MERGE_REQUESTED_LABEL,
            error_collector=error_collector,
        ):
            changed = True
        if edit_pr_label(
            repo,
            pr_number,
            add=True,
            label=REFIX_MERGED_LABEL,
            error_collector=error_collector,
        ):
            changed = True
    else:
        _ensure_refix_labels(
            repo, enabled_pr_label_keys=enabled, error_collector=error_collector
        )
        if edit_pr_label(
            repo,
            pr_number,
            add=False,
            label=REFIX_RUNNING_LABEL,
            enabled_pr_label_keys=enabled,
            error_collector=error_collector,
        ):
            changed = True
        if edit_pr_label(
            repo,
            pr_number,
            add=False,
            label=REFIX_AUTO_MERGE_REQUESTED_LABEL,
            enabled_pr_label_keys=enabled,
            error_collector=error_collector,
        ):
            changed = True
        if edit_pr_label(
            repo,
            pr_number,
            add=True,
            label=REFIX_MERGED_LABEL,
            enabled_pr_label_keys=enabled,
            error_collector=error_collector,
        ):
            changed = True
    return changed


def _mark_pr_merged_label_if_needed(
    repo: str,
    pr_number: int,
    *,
    enabled_pr_label_keys: set[str] | None = None,
    error_collector: ErrorCollector | None = None,
) -> bool:
    """マージ済みの PR に refix: merged ラベルを追加する。"""
    enabled = _resolve_enabled_pr_label_keys(enabled_pr_label_keys)
    if not ({"running", "auto_merge_requested", "merged"} & enabled):
        return False
    cmd = [
        "gh",
        "pr",
        "view",
        str(pr_number),
        "--repo",
        repo,
        "--json",
        "mergedAt,labels",
    ]
    try:
        result = run_command(cmd, check=False)
    except SubprocessError as exc:
        msg = f"failed to inspect merge state for {_pr_ref(repo, pr_number)}: {exc}"
        print(f"Warning: {msg}", file=sys.stderr)
        if error_collector:
            error_collector.add_pr_error(repo, pr_number, msg)
        return False
    if result.returncode != 0:
        msg = (
            f"failed to inspect merge state for {_pr_ref(repo, pr_number)}: "
            f"{(result.stderr or '').strip()}"
        )
        print(f"Warning: {msg}", file=sys.stderr)
        if error_collector:
            error_collector.add_pr_error(repo, pr_number, msg)
        return False
    try:
        pr_data = json.loads(result.stdout) if result.stdout else {}
    except json.JSONDecodeError:
        msg = f"failed to parse merge state for {_pr_ref(repo, pr_number)}"
        print(f"Warning: {msg}", file=sys.stderr)
        if error_collector:
            error_collector.add_pr_error(repo, pr_number, msg)
        return False
    if not isinstance(pr_data, dict):
        return False

    pr_data_typed = cast(PRData, pr_data)
    merged_at = str(pr_data_typed.get("mergedAt") or "").strip()
    if not merged_at:
        return False
    if "done" in enabled and not _pr_has_label(pr_data_typed, REFIX_DONE_LABEL):
        return False
    if "auto_merge_requested" in enabled and not _pr_has_label(
        pr_data_typed, REFIX_AUTO_MERGE_REQUESTED_LABEL
    ):
        return False
    if _pr_has_label(pr_data_typed, REFIX_MERGED_LABEL):
        return False

    print(f"{_pr_ref(repo, pr_number)} is merged; adding {REFIX_MERGED_LABEL} label.")
    if enabled_pr_label_keys is None:
        return _set_pr_merged_label(repo, pr_number, error_collector=error_collector)
    return _set_pr_merged_label(
        repo, pr_number, enabled_pr_label_keys=enabled, error_collector=error_collector
    )


def backfill_merged_labels(
    repo: str,
    *,
    limit: int = 100,
    enabled_pr_label_keys: set[str] | None = None,
    error_collector: ErrorCollector | None = None,
) -> int:
    """マージ済みで refix: done が付いている PR に refix: merged ラベルをバックフィルする。"""
    enabled = _resolve_enabled_pr_label_keys(enabled_pr_label_keys)
    if "merged" not in enabled:
        return 0
    if (
        "done" not in enabled
        and "auto_merge_requested" not in enabled
        and "running" not in enabled
    ):
        return 0
    search_parts = []
    if "done" in enabled:
        search_parts.append(f'label:"{REFIX_DONE_LABEL}"')
    if "auto_merge_requested" in enabled:
        search_parts.append(f'label:"{REFIX_AUTO_MERGE_REQUESTED_LABEL}"')
    if not search_parts and "running" in enabled:
        search_parts.append(f'label:"{REFIX_RUNNING_LABEL}"')
    search_parts.append(f'-label:"{REFIX_MERGED_LABEL}"')
    search_query = " ".join(search_parts)
    cmd = [
        "gh",
        "pr",
        "list",
        "--repo",
        repo,
        "--state",
        "merged",
        "--search",
        search_query,
        "--json",
        "number",
        "--limit",
        str(limit),
    ]
    try:
        result = run_command(cmd, check=False)
    except SubprocessError as exc:
        msg = f"failed to list merged PRs for {repo}: {exc}"
        print(f"Warning: {msg}", file=sys.stderr)
        if error_collector:
            error_collector.add_repo_error(repo, msg)
        return 0
    if result.returncode != 0:
        msg = f"failed to list merged PRs for {repo}: {(result.stderr or '').strip()}"
        print(f"Warning: {msg}", file=sys.stderr)
        if error_collector:
            error_collector.add_repo_error(repo, msg)
        return 0
    try:
        prs = json.loads(result.stdout) if result.stdout else []
    except json.JSONDecodeError:
        msg = f"failed to parse merged PR list for {repo}"
        print(f"Warning: {msg}", file=sys.stderr)
        if error_collector:
            error_collector.add_repo_error(repo, msg)
        return 0
    if not isinstance(prs, list):
        return 0

    count = 0
    for pr in prs:
        if not isinstance(pr, dict):
            continue
        pr_number = pr.get("number")
        if not isinstance(pr_number, int):
            continue
        if enabled_pr_label_keys is None:
            marked = _mark_pr_merged_label_if_needed(
                repo, pr_number, error_collector=error_collector
            )
        else:
            marked = _mark_pr_merged_label_if_needed(
                repo,
                pr_number,
                enabled_pr_label_keys=enabled,
                error_collector=error_collector,
            )
        if marked:
            count += 1
    if count:
        print(f"Backfilled {REFIX_MERGED_LABEL} on {count} merged PR(s) in {repo}.")
    return count


_MERGE_METHOD_FLAG: dict[str, str] = {
    "merge": "--merge",
    "squash": "--squash",
    "rebase": "--rebase",
}
_MERGE_METHOD_PRIORITY = ("merge", "squash", "rebase")


def _get_allowed_merge_methods(repo: str) -> list[str] | None:
    """リポジトリの許可マージメソッドを API から取得する。

    成功時は許可されたメソッド名のリスト、失敗時は None を返す。
    """
    try:
        data = run_gh_api(f"repos/{repo}")
    except SubprocessError:
        return None
    if not isinstance(data, dict):
        return None
    allowed = []
    for method, key in [
        ("merge", "allow_merge_commit"),
        ("squash", "allow_squash_merge"),
        ("rebase", "allow_rebase_merge"),
    ]:
        if data.get(key):
            allowed.append(method)
    return allowed if allowed else None


def _try_gh_merge(
    repo: str,
    pr_number: int,
    method: str,
) -> tuple[bool, str]:
    """指定メソッドで gh pr merge を実行する。(success, combined_lower) を返す。"""
    flag = _MERGE_METHOD_FLAG[method]
    cmd = ["gh", "pr", "merge", str(pr_number), "--repo", repo, "--auto", flag]
    try:
        result = run_command(cmd, check=False)
    except SubprocessError as exc:
        return False, str(exc).lower()
    stderr_text = (result.stderr or "").strip()
    stdout_text = (result.stdout or "").strip()
    combined_lower = f"{stdout_text}\n{stderr_text}".lower()
    if result.returncode == 0:
        return True, combined_lower
    return False, combined_lower


def _trigger_pr_auto_merge(
    repo: str,
    pr_number: int,
    *,
    merge_method: str = "auto",
    enabled_pr_label_keys: set[str] | None = None,
    error_collector: ErrorCollector | None = None,
) -> tuple[bool, bool]:
    """auto-merge を要求する。(merge_state_reached, modified) を返す。"""
    enabled = _resolve_enabled_pr_label_keys(enabled_pr_label_keys)

    def _on_success() -> tuple[bool, bool]:
        print(f"Auto-merge requested for {_pr_ref(repo, pr_number)}.")
        _ensure_refix_labels(
            repo, enabled_pr_label_keys=enabled, error_collector=error_collector
        )
        modified = edit_pr_label(
            repo,
            pr_number,
            add=True,
            label=REFIX_AUTO_MERGE_REQUESTED_LABEL,
            enabled_pr_label_keys=enabled_pr_label_keys,
            error_collector=error_collector,
        )
        return True, modified

    def _on_already_merged() -> tuple[bool, bool]:
        print(f"{_pr_ref(repo, pr_number)} is already merged.")
        _ensure_refix_labels(
            repo, enabled_pr_label_keys=enabled, error_collector=error_collector
        )
        modified = edit_pr_label(
            repo,
            pr_number,
            add=True,
            label=REFIX_AUTO_MERGE_REQUESTED_LABEL,
            enabled_pr_label_keys=enabled_pr_label_keys,
            error_collector=error_collector,
        )
        return True, modified

    if merge_method == "auto":
        # API から許可メソッドを取得して優先順で試行
        allowed = _get_allowed_merge_methods(repo)
        if allowed is not None:
            methods_to_try = [m for m in _MERGE_METHOD_PRIORITY if m in allowed]
            if not methods_to_try:
                methods_to_try = list(_MERGE_METHOD_PRIORITY)
        else:
            methods_to_try = list(_MERGE_METHOD_PRIORITY)

        last_combined_lower = ""
        for method in methods_to_try:
            success, combined_lower = _try_gh_merge(repo, pr_number, method)
            if success:
                return _on_success()
            last_combined_lower = combined_lower
            if "already merged" in combined_lower:
                return _on_already_merged()
            # メソッド非対応エラーなら次を試みる
            if "merge method" in combined_lower or "not allowed" in combined_lower:
                continue
            # その他のエラーは即時失敗
            break

        details = last_combined_lower or "unknown error"
        msg = f"failed to auto-merge {_pr_ref(repo, pr_number)}: {details}"
        print(f"Warning: {msg}", file=sys.stderr)
        if error_collector:
            error_collector.add_pr_error(repo, pr_number, msg)
        return False, False
    else:
        # 明示指定: 指定メソッドのみ使用
        success, combined_lower = _try_gh_merge(repo, pr_number, merge_method)
        if success:
            return _on_success()
        if "already merged" in combined_lower:
            return _on_already_merged()
        details = combined_lower or "unknown error"
        msg = f"failed to auto-merge {_pr_ref(repo, pr_number)}: {details}"
        print(f"Warning: {msg}", file=sys.stderr)
        if error_collector:
            error_collector.add_pr_error(repo, pr_number, msg)
        return False, False


def update_done_label_if_completed(
    *,
    repo: str,
    pr_number: int,
    has_review_targets: bool,
    review_fix_started: bool,
    review_fix_added_commits: bool,
    review_fix_failed: bool,
    state_saved: bool,
    commits_by_phase: list[str],
    pr_data: PRData,
    review_comments: list[GitHubComment],
    issue_comments: list[GitHubComment],
    dry_run: bool,
    summarize_only: bool,
    auto_merge_enabled: bool = False,
    merge_method: str = "auto",
    coderabbit_rate_limit_active: bool = False,
    coderabbit_review_failed_active: bool = False,
    coderabbit_review_skipped_active: bool = False,
    coderabbit_require_review: bool = True,
    coderabbit_block_while_processing: bool = True,
    enabled_pr_label_keys: set[str] | None = None,
    ci_empty_as_success: bool = True,
    ci_empty_grace_minutes: int = 5,
    error_collector: ErrorCollector | None = None,
) -> tuple[bool, bool]:
    """完了条件を満たした場合に refix: done ラベルを設定する。

    Returns:
        (label_was_updated, ci_grace_pending)
    """
    if dry_run or summarize_only:
        return False, False

    is_completed = True
    block_reasons: list[str] = []

    if review_fix_failed:
        is_completed = False
        block_reasons.append("review fix failed")
    if not state_saved:
        is_completed = False
        block_reasons.append("state not saved")
    if commits_by_phase:
        is_completed = False
        block_reasons.append(
            f"commits pushed this run: {len(commits_by_phase)} phase(s)"
        )
    if has_review_targets and (not review_fix_started or review_fix_added_commits):
        is_completed = False
        block_reasons.append("review fix pending or added commits")

    # CodeRabbit チェック用にコメントを再取得（stale data 対策）
    if is_completed and (
        coderabbit_require_review or coderabbit_block_while_processing
    ):
        try:
            review_comments = fetch_pr_review_comments(repo, pr_number)
            issue_comments = fetch_issue_comments(repo, pr_number)
        except Exception as exc:
            print(
                f"Warning: failed to re-fetch comments for "
                f"{_pr_ref(repo, pr_number)}: {exc}",
                file=sys.stderr,
            )

    if is_completed:
        if coderabbit_require_review and not has_coderabbit_comments(
            pr_data, review_comments, issue_comments
        ):
            print(
                f"CodeRabbit has not reviewed {_pr_ref(repo, pr_number)} yet; "
                f"mark as {REFIX_RUNNING_LABEL}."
            )
            is_completed = False
            block_reasons.append("CodeRabbit review not yet received")

        if coderabbit_block_while_processing and contains_coderabbit_processing_marker(
            pr_data, review_comments, issue_comments
        ):
            print(
                f"CodeRabbit is still processing {_pr_ref(repo, pr_number)}; "
                f"mark as {REFIX_RUNNING_LABEL}."
            )
            is_completed = False
            block_reasons.append("CodeRabbit still processing")

        if coderabbit_rate_limit_active:
            print(
                f"CodeRabbit rate limit is active on {_pr_ref(repo, pr_number)}; "
                f"keep {REFIX_RUNNING_LABEL}."
            )
            is_completed = False
            block_reasons.append("CodeRabbit rate limited")

        if coderabbit_review_failed_active:
            print(
                "CodeRabbit review failed status is active on "
                f"{_pr_ref(repo, pr_number)}; keep {REFIX_RUNNING_LABEL}."
            )
            is_completed = False
            block_reasons.append("CodeRabbit review failed")

        if coderabbit_review_skipped_active:
            print(
                "CodeRabbit review skipped status is active on "
                f"{_pr_ref(repo, pr_number)}; keep {REFIX_RUNNING_LABEL}."
            )
            is_completed = False
            block_reasons.append("CodeRabbit review skipped")

    ci_grace_pending = False
    # ci_is_blocking: CI 以外の全ブロック理由がクリアされた上で
    # CI のみが完了を阻んでいる場合にのみ True。
    # review_fix_failed 等が残っている場合は ci-pending を付与しない
    # （review 修正が必要な状態で CI 完了を待っても意味がないため）。
    ci_is_blocking = False
    if is_completed:
        ci_check_result = are_all_ci_checks_successful(
            repo,
            pr_number,
            ci_empty_as_success=ci_empty_as_success,
            ci_empty_grace_minutes=ci_empty_grace_minutes,
            error_collector=error_collector,
        )
        if ci_check_result is None:
            ci_grace_pending = True
            is_completed = False
            ci_is_blocking = True
            block_reasons.append("CI checks unavailable")
        elif not ci_check_result:
            is_completed = False
            ci_is_blocking = True
            block_reasons.append("CI checks not all successful")

    if is_completed:
        print(
            f"{_pr_ref(repo, pr_number)} meets completion conditions; "
            f"switching label to {REFIX_DONE_LABEL}."
        )
        current_pr_data = None if review_fix_started else pr_data
        if enabled_pr_label_keys is None:
            done_changed = _set_pr_done_label(
                repo,
                pr_number,
                pr_data=current_pr_data,
                error_collector=error_collector,
            )
        else:
            done_changed = _set_pr_done_label(
                repo,
                pr_number,
                pr_data=current_pr_data,
                enabled_pr_label_keys=enabled_pr_label_keys,
                error_collector=error_collector,
            )
        merge_triggered = False
        if auto_merge_enabled:
            if enabled_pr_label_keys is None:
                merge_state_reached, label_modified = _trigger_pr_auto_merge(
                    repo,
                    pr_number,
                    merge_method=merge_method,
                    error_collector=error_collector,
                )
            else:
                merge_state_reached, label_modified = _trigger_pr_auto_merge(
                    repo,
                    pr_number,
                    merge_method=merge_method,
                    enabled_pr_label_keys=enabled_pr_label_keys,
                    error_collector=error_collector,
                )
            if merge_state_reached:
                if enabled_pr_label_keys is None:
                    _mark_pr_merged_label_if_needed(
                        repo, pr_number, error_collector=error_collector
                    )
                else:
                    _mark_pr_merged_label_if_needed(
                        repo,
                        pr_number,
                        enabled_pr_label_keys=enabled_pr_label_keys,
                        error_collector=error_collector,
                    )
            merge_triggered = label_modified
        # 完了時: ci-pending ラベルを除去（付いている場合のみ）
        ci_pending_changed = False
        if _pr_has_label(pr_data, REFIX_CI_PENDING_LABEL):
            ci_pending_changed = edit_pr_label(
                repo,
                pr_number,
                add=False,
                label=REFIX_CI_PENDING_LABEL,
                enabled_pr_label_keys=enabled_pr_label_keys,
                error_collector=error_collector,
            )
        return done_changed or merge_triggered or ci_pending_changed, ci_grace_pending

    if block_reasons:
        print(
            f"{_pr_ref(repo, pr_number)} is not completed yet "
            f"({', '.join(block_reasons)}); "
            f"switching label to {REFIX_RUNNING_LABEL}."
        )
    else:
        print(
            f"{_pr_ref(repo, pr_number)} is not completed yet; "
            f"switching label to {REFIX_RUNNING_LABEL}."
        )
    # set_pr_running_label は内部で _ensure_refix_labels を呼び出す（ci-pending 含む）
    if enabled_pr_label_keys is None:
        running_changed = set_pr_running_label(
            repo, pr_number, pr_data=pr_data, error_collector=error_collector
        )
    else:
        running_changed = set_pr_running_label(
            repo,
            pr_number,
            pr_data=pr_data,
            enabled_pr_label_keys=enabled_pr_label_keys,
            error_collector=error_collector,
        )
    # commits のみがブロック理由の場合も ci-pending を付与する。
    # push 後に CI が再実行されるため、check_suite: completed で拾えるようにするため。
    commits_only_blocking = (
        not is_completed
        and bool(commits_by_phase)
        and not review_fix_failed
        and state_saved
        and not (
            has_review_targets and (not review_fix_started or review_fix_added_commits)
        )
    )
    # CI ブロック時 または commits のみがブロック理由の場合: ci-pending を付与
    # それ以外: ci-pending を除去（状態が変わる場合のみ呼び出す）
    target_add = ci_is_blocking or commits_only_blocking
    ci_pending_changed = False
    if target_add != _pr_has_label(pr_data, REFIX_CI_PENDING_LABEL):
        ci_pending_changed = edit_pr_label(
            repo,
            pr_number,
            add=target_add,
            label=REFIX_CI_PENDING_LABEL,
            enabled_pr_label_keys=enabled_pr_label_keys,
            error_collector=error_collector,
        )
    return running_changed or ci_pending_changed, ci_grace_pending
