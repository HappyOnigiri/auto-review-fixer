"""PR ラベルの作成・設定・管理を行うモジュール。"""

import json
import sys
from typing import Any
from urllib.parse import quote

from subprocess_helpers import SubprocessError, run_command

from ci_check import are_all_ci_checks_successful
from coderabbit import contains_coderabbit_processing_marker

# --- ラベル定数 ---
REFIX_RUNNING_LABEL = "refix:running"
REFIX_DONE_LABEL = "refix:done"
REFIX_MERGED_LABEL = "refix:merged"
REFIX_AUTO_MERGE_REQUESTED_LABEL = "refix:auto-merge-requested"

PR_LABEL_KEY_TO_NAME: dict[str, str] = {
    "running": REFIX_RUNNING_LABEL,
    "done": REFIX_DONE_LABEL,
    "merged": REFIX_MERGED_LABEL,
    "auto_merge_requested": REFIX_AUTO_MERGE_REQUESTED_LABEL,
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
    repo: str, label: str, *, color: str, description: str
) -> bool:
    """リポジトリにラベルが存在しなければ作成する。"""
    encoded_label = quote(label, safe="")
    get_cmd = ["gh", "api", f"repos/{repo}/labels/{encoded_label}"]
    try:
        get_result = run_command(get_cmd, check=False)
    except SubprocessError as exc:
        print(
            f"Warning: failed to check label '{label}' on {repo}: {exc}",
            file=sys.stderr,
        )
        return False
    if get_result.returncode == 0:
        return True

    stderr_lower = (get_result.stderr or "").lower()
    not_found = "not found" in stderr_lower or "404" in stderr_lower
    if not not_found:
        print(
            f"Warning: failed to verify label '{label}' on {repo}: {(get_result.stderr or '').strip()}",
            file=sys.stderr,
        )
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
        print(
            f"Warning: failed to create label '{label}' in {repo}: {exc}",
            file=sys.stderr,
        )
        return False
    if create_result.returncode == 0:
        print(f"Created missing label '{label}' in {repo}")
        return True

    create_stderr = (create_result.stderr or "").lower()
    if "already_exists" in create_stderr or "already exists" in create_stderr:
        return True

    print(
        f"Warning: failed to create label '{label}' in {repo}: {(create_result.stderr or '').strip()}",
        file=sys.stderr,
    )
    return False


def _ensure_refix_labels(
    repo: str, *, enabled_pr_label_keys: set[str] | None = None
) -> None:
    """必要な refix ラベルをリポジトリに作成する。"""
    enabled = _resolve_enabled_pr_label_keys(enabled_pr_label_keys)
    if "running" in enabled:
        _ensure_repo_label_exists(
            repo,
            REFIX_RUNNING_LABEL,
            color=REFIX_RUNNING_LABEL_COLOR,
            description="Refix is currently processing review fixes.",
        )
    if "done" in enabled:
        _ensure_repo_label_exists(
            repo,
            REFIX_DONE_LABEL,
            color=REFIX_DONE_LABEL_COLOR,
            description="Refix finished review checks/fixes for now.",
        )
    if "merged" in enabled:
        _ensure_repo_label_exists(
            repo,
            REFIX_MERGED_LABEL,
            color=REFIX_MERGED_LABEL_COLOR,
            description="PR has been merged after Refix auto-merge.",
        )
    if "auto_merge_requested" in enabled:
        _ensure_repo_label_exists(
            repo,
            REFIX_AUTO_MERGE_REQUESTED_LABEL,
            color=REFIX_AUTO_MERGE_REQUESTED_LABEL_COLOR,
            description="Refix has requested auto-merge for this PR.",
        )


def edit_pr_label(
    repo: str,
    pr_number: int,
    *,
    add: bool,
    label: str,
    enabled_pr_label_keys: set[str] | None = None,
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
        print(
            f"Warning: failed to {action} label '{label}' on PR #{pr_number}: {exc}",
            file=sys.stderr,
        )
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
    print(
        f"Warning: failed to {action} label '{label}' on PR #{pr_number}: {(result.stderr or '').strip()}",
        file=sys.stderr,
    )
    return False


def _pr_has_label(pr_data: dict[str, Any], label_name: str) -> bool:
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
    pr_data: dict[str, Any] | None = None,
    enabled_pr_label_keys: set[str] | None = None,
) -> bool:
    """refix:running を設定し、refix:done を削除する。"""
    enabled = _resolve_enabled_pr_label_keys(enabled_pr_label_keys)
    running_enabled = "running" in enabled
    done_enabled = "done" in enabled
    if not running_enabled and not done_enabled:
        return False
    if (
        pr_data
        and (not running_enabled or _pr_has_label(pr_data, REFIX_RUNNING_LABEL))
        and (not done_enabled or not _pr_has_label(pr_data, REFIX_DONE_LABEL))
    ):
        return False
    if enabled_pr_label_keys is None:
        _ensure_refix_labels(repo)
    else:
        _ensure_refix_labels(repo, enabled_pr_label_keys=enabled)
    changed = False
    if done_enabled and (pr_data is None or _pr_has_label(pr_data, REFIX_DONE_LABEL)):
        if enabled_pr_label_keys is None:
            if edit_pr_label(repo, pr_number, add=False, label=REFIX_DONE_LABEL):
                changed = True
        else:
            if edit_pr_label(
                repo,
                pr_number,
                add=False,
                label=REFIX_DONE_LABEL,
                enabled_pr_label_keys=enabled,
            ):
                changed = True
    if running_enabled and (
        pr_data is None or not _pr_has_label(pr_data, REFIX_RUNNING_LABEL)
    ):
        if enabled_pr_label_keys is None:
            if edit_pr_label(repo, pr_number, add=True, label=REFIX_RUNNING_LABEL):
                changed = True
        else:
            if edit_pr_label(
                repo,
                pr_number,
                add=True,
                label=REFIX_RUNNING_LABEL,
                enabled_pr_label_keys=enabled,
            ):
                changed = True
    return changed


def _set_pr_done_label(
    repo: str,
    pr_number: int,
    *,
    pr_data: dict[str, Any] | None = None,
    enabled_pr_label_keys: set[str] | None = None,
) -> bool:
    """refix:done を設定し、refix:running を削除する。"""
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
        _ensure_refix_labels(repo)
    else:
        _ensure_refix_labels(repo, enabled_pr_label_keys=enabled)
    changed = False
    if running_enabled and (
        pr_data is None or _pr_has_label(pr_data, REFIX_RUNNING_LABEL)
    ):
        if enabled_pr_label_keys is None:
            if edit_pr_label(repo, pr_number, add=False, label=REFIX_RUNNING_LABEL):
                changed = True
        else:
            if edit_pr_label(
                repo,
                pr_number,
                add=False,
                label=REFIX_RUNNING_LABEL,
                enabled_pr_label_keys=enabled,
            ):
                changed = True
    if done_enabled and (
        pr_data is None or not _pr_has_label(pr_data, REFIX_DONE_LABEL)
    ):
        if enabled_pr_label_keys is None:
            if edit_pr_label(repo, pr_number, add=True, label=REFIX_DONE_LABEL):
                changed = True
        else:
            if edit_pr_label(
                repo,
                pr_number,
                add=True,
                label=REFIX_DONE_LABEL,
                enabled_pr_label_keys=enabled,
            ):
                changed = True
    return changed


def _set_pr_merged_label(
    repo: str, pr_number: int, *, enabled_pr_label_keys: set[str] | None = None
) -> bool:
    """refix:merged を設定し、refix:running と refix:auto-merge-requested を削除する。"""
    enabled = _resolve_enabled_pr_label_keys(enabled_pr_label_keys)
    if not (
        "running" in enabled or "auto_merge_requested" in enabled or "merged" in enabled
    ):
        return False
    changed = False
    if enabled_pr_label_keys is None:
        _ensure_refix_labels(repo)
        if edit_pr_label(repo, pr_number, add=False, label=REFIX_RUNNING_LABEL):
            changed = True
        if edit_pr_label(
            repo, pr_number, add=False, label=REFIX_AUTO_MERGE_REQUESTED_LABEL
        ):
            changed = True
        if edit_pr_label(repo, pr_number, add=True, label=REFIX_MERGED_LABEL):
            changed = True
    else:
        _ensure_refix_labels(repo, enabled_pr_label_keys=enabled)
        if edit_pr_label(
            repo,
            pr_number,
            add=False,
            label=REFIX_RUNNING_LABEL,
            enabled_pr_label_keys=enabled,
        ):
            changed = True
        if edit_pr_label(
            repo,
            pr_number,
            add=False,
            label=REFIX_AUTO_MERGE_REQUESTED_LABEL,
            enabled_pr_label_keys=enabled,
        ):
            changed = True
        if edit_pr_label(
            repo,
            pr_number,
            add=True,
            label=REFIX_MERGED_LABEL,
            enabled_pr_label_keys=enabled,
        ):
            changed = True
    return changed


def _mark_pr_merged_label_if_needed(
    repo: str, pr_number: int, *, enabled_pr_label_keys: set[str] | None = None
) -> bool:
    """マージ済みの PR に refix:merged ラベルを追加する。"""
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
        print(
            f"Warning: failed to inspect merge state for PR #{pr_number}: {exc}",
            file=sys.stderr,
        )
        return False
    if result.returncode != 0:
        print(
            f"Warning: failed to inspect merge state for PR #{pr_number}: {(result.stderr or '').strip()}",
            file=sys.stderr,
        )
        return False
    try:
        pr_data = json.loads(result.stdout) if result.stdout else {}
    except json.JSONDecodeError:
        print(
            f"Warning: failed to parse merge state for PR #{pr_number}",
            file=sys.stderr,
        )
        return False
    if not isinstance(pr_data, dict):
        return False

    merged_at = str(pr_data.get("mergedAt") or "").strip()
    if not merged_at:
        return False
    if "done" in enabled and not _pr_has_label(pr_data, REFIX_DONE_LABEL):
        return False
    if "auto_merge_requested" in enabled and not _pr_has_label(
        pr_data, REFIX_AUTO_MERGE_REQUESTED_LABEL
    ):
        return False
    if _pr_has_label(pr_data, REFIX_MERGED_LABEL):
        return False

    print(f"PR #{pr_number} is merged; adding {REFIX_MERGED_LABEL} label.")
    if enabled_pr_label_keys is None:
        return _set_pr_merged_label(repo, pr_number)
    return _set_pr_merged_label(repo, pr_number, enabled_pr_label_keys=enabled)


def backfill_merged_labels(
    repo: str,
    *,
    limit: int = 100,
    enabled_pr_label_keys: set[str] | None = None,
) -> int:
    """マージ済みで refix:done が付いている PR に refix:merged ラベルをバックフィルする。"""
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
        print(
            f"Warning: failed to list merged PRs for {repo}: {exc}",
            file=sys.stderr,
        )
        return 0
    if result.returncode != 0:
        print(
            f"Warning: failed to list merged PRs for {repo}: {(result.stderr or '').strip()}",
            file=sys.stderr,
        )
        return 0
    try:
        prs = json.loads(result.stdout) if result.stdout else []
    except json.JSONDecodeError:
        print(
            f"Warning: failed to parse merged PR list for {repo}",
            file=sys.stderr,
        )
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
            marked = _mark_pr_merged_label_if_needed(repo, pr_number)
        else:
            marked = _mark_pr_merged_label_if_needed(
                repo, pr_number, enabled_pr_label_keys=enabled
            )
        if marked:
            count += 1
    if count:
        print(f"Backfilled {REFIX_MERGED_LABEL} on {count} merged PR(s) in {repo}.")
    return count


def _trigger_pr_auto_merge(
    repo: str, pr_number: int, *, enabled_pr_label_keys: set[str] | None = None
) -> tuple[bool, bool]:
    """auto-merge を要求する。(merge_state_reached, modified) を返す。"""
    enabled = _resolve_enabled_pr_label_keys(enabled_pr_label_keys)
    cmd = ["gh", "pr", "merge", str(pr_number), "--repo", repo, "--auto", "--merge"]
    try:
        result = run_command(cmd, check=False)
    except SubprocessError as exc:
        print(
            f"Warning: failed to auto-merge PR #{pr_number}: {exc}",
            file=sys.stderr,
        )
        return False, False
    if result.returncode == 0:
        print(f"Auto-merge requested for PR #{pr_number}.")
        _ensure_refix_labels(repo, enabled_pr_label_keys=enabled)
        modified = edit_pr_label(
            repo,
            pr_number,
            add=True,
            label=REFIX_AUTO_MERGE_REQUESTED_LABEL,
            enabled_pr_label_keys=enabled_pr_label_keys,
        )
        return True, modified

    stderr_text = (result.stderr or "").strip()
    stdout_text = (result.stdout or "").strip()
    combined_lower = f"{stdout_text}\n{stderr_text}".lower()
    if "already merged" in combined_lower:
        print(f"PR #{pr_number} is already merged.")
        _ensure_refix_labels(repo, enabled_pr_label_keys=enabled)
        modified = edit_pr_label(
            repo,
            pr_number,
            add=True,
            label=REFIX_AUTO_MERGE_REQUESTED_LABEL,
            enabled_pr_label_keys=enabled_pr_label_keys,
        )
        return True, modified

    details = stderr_text or stdout_text or "unknown error"
    print(
        f"Warning: failed to auto-merge PR #{pr_number}: {details}",
        file=sys.stderr,
    )
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
    pr_data: dict[str, Any],
    review_comments: list[dict[str, Any]],
    issue_comments: list[dict[str, Any]],
    dry_run: bool,
    summarize_only: bool,
    auto_merge_enabled: bool = False,
    coderabbit_rate_limit_active: bool = False,
    coderabbit_review_failed_active: bool = False,
    enabled_pr_label_keys: set[str] | None = None,
    ci_empty_as_success: bool = True,
    ci_empty_grace_minutes: int = 5,
) -> tuple[bool, bool]:
    """完了条件を満たした場合に refix:done ラベルを設定する。

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

    if is_completed and contains_coderabbit_processing_marker(
        pr_data, review_comments, issue_comments
    ):
        print(
            f"CodeRabbit is still processing PR #{pr_number}; mark as {REFIX_RUNNING_LABEL}."
        )
        is_completed = False
        block_reasons.append("CodeRabbit still processing")

    if is_completed and coderabbit_rate_limit_active:
        print(
            f"CodeRabbit rate limit is active on PR #{pr_number}; keep {REFIX_RUNNING_LABEL}."
        )
        is_completed = False
        block_reasons.append("CodeRabbit rate limited")

    if is_completed and coderabbit_review_failed_active:
        print(
            f"CodeRabbit review failed status is active on PR #{pr_number}; keep {REFIX_RUNNING_LABEL}."
        )
        is_completed = False
        block_reasons.append("CodeRabbit review failed")

    ci_grace_pending = False
    if is_completed:
        ci_check_result = are_all_ci_checks_successful(
            repo,
            pr_number,
            ci_empty_as_success=ci_empty_as_success,
            ci_empty_grace_minutes=ci_empty_grace_minutes,
        )
        if ci_check_result is None:
            ci_grace_pending = True
            is_completed = False
            block_reasons.append("CI grace period (checks not yet available)")
        elif not ci_check_result:
            is_completed = False
            block_reasons.append("CI checks not all successful")

    if is_completed:
        print(
            f"PR #{pr_number} meets completion conditions; switching label to {REFIX_DONE_LABEL}."
        )
        current_pr_data = None if review_fix_started else pr_data
        if enabled_pr_label_keys is None:
            done_changed = _set_pr_done_label(repo, pr_number, pr_data=current_pr_data)
        else:
            done_changed = _set_pr_done_label(
                repo,
                pr_number,
                pr_data=current_pr_data,
                enabled_pr_label_keys=enabled_pr_label_keys,
            )
        merge_triggered = False
        if auto_merge_enabled:
            if enabled_pr_label_keys is None:
                merge_state_reached, label_modified = _trigger_pr_auto_merge(
                    repo, pr_number
                )
            else:
                merge_state_reached, label_modified = _trigger_pr_auto_merge(
                    repo,
                    pr_number,
                    enabled_pr_label_keys=enabled_pr_label_keys,
                )
            if merge_state_reached:
                if enabled_pr_label_keys is None:
                    _mark_pr_merged_label_if_needed(repo, pr_number)
                else:
                    _mark_pr_merged_label_if_needed(
                        repo,
                        pr_number,
                        enabled_pr_label_keys=enabled_pr_label_keys,
                    )
            merge_triggered = label_modified
        return done_changed or merge_triggered, ci_grace_pending

    if block_reasons:
        print(
            f"PR #{pr_number} is not completed yet ({', '.join(block_reasons)}); "
            f"switching label to {REFIX_RUNNING_LABEL}."
        )
    else:
        print(
            f"PR #{pr_number} is not completed yet; switching label to {REFIX_RUNNING_LABEL}."
        )
    if enabled_pr_label_keys is None:
        return set_pr_running_label(repo, pr_number, pr_data=pr_data), ci_grace_pending
    return (
        set_pr_running_label(
            repo,
            pr_number,
            pr_data=pr_data,
            enabled_pr_label_keys=enabled_pr_label_keys,
        ),
        ci_grace_pending,
    )
