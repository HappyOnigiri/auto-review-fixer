"""CodeRabbit との連携処理を行うモジュール。

レート制限検出、レビュー失敗ステータス確認、自動 resume 投稿などを担当する。
"""

import re
import sys
from datetime import datetime, timedelta, timezone
from typing import Any

from subprocess_helpers import SubprocessError, run_command

# --- 定数 ---
# REST API は "coderabbitai[bot]"、GraphQL は "coderabbitai" を返す
CODERABBIT_BOT_LOGIN = "coderabbitai"
CODERABBIT_PROCESSING_MARKER = "Currently processing new changes in this PR."
CODERABBIT_RATE_LIMIT_MARKER = "Rate limit exceeded"
CODERABBIT_REVIEW_FAILED_MARKER = "## Review failed"
CODERABBIT_REVIEW_FAILED_HEAD_CHANGED_MARKER = (
    "The head commit changed during the review"
)
CODERABBIT_RESUME_COMMENT = "@coderabbitai resume"


def is_coderabbit_login(login: str) -> bool:
    """ログイン名が CodeRabbit ボットかどうか判定する。"""
    return login in (CODERABBIT_BOT_LOGIN, f"{CODERABBIT_BOT_LOGIN}[bot]")


def _parse_github_timestamp(value: str | None) -> datetime | None:
    """GitHub のタイムスタンプ文字列を datetime に変換する。"""
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _comment_last_updated_at(comment: dict[str, Any]) -> datetime | None:
    """コメントの最終更新日時を取得する。"""
    return (
        _parse_github_timestamp(str(comment.get("updated_at") or ""))
        or _parse_github_timestamp(str(comment.get("updatedAt") or ""))
        or _parse_github_timestamp(str(comment.get("created_at") or ""))
        or _parse_github_timestamp(str(comment.get("createdAt") or ""))
    )


def _parse_wait_duration_seconds(text: str) -> int | None:
    """待機時間テキスト（例: "2 hours 30 minutes"）を秒数に変換する。"""
    unit_map = {
        "day": 86400,
        "days": 86400,
        "hour": 3600,
        "hours": 3600,
        "minute": 60,
        "minutes": 60,
        "second": 1,
        "seconds": 1,
    }
    matches = re.findall(
        r"(\d+)\s+(day|days|hour|hours|minute|minutes|second|seconds)",
        text,
        flags=re.IGNORECASE,
    )
    if not matches:
        return None
    total = 0
    for raw_value, raw_unit in matches:
        total += int(raw_value) * unit_map[raw_unit.lower()]
    return total


def _format_duration(seconds: int) -> str:
    """秒数を人間が読みやすい時間表記に変換する。"""
    seconds = max(0, seconds)
    minutes, sec = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    parts: list[str] = []
    if hours:
        parts.append(f"{hours}h")
    if minutes:
        parts.append(f"{minutes}m")
    if sec or not parts:
        parts.append(f"{sec}s")
    return " ".join(parts)


def _extract_coderabbit_rate_limit_status(
    comment: dict[str, Any],
) -> dict[str, Any] | None:
    """コメントから CodeRabbit のレート制限ステータスを抽出する。"""
    body = str(comment.get("body") or "")
    if CODERABBIT_RATE_LIMIT_MARKER.lower() not in body.lower():
        return None

    wait_match = re.search(
        r"Please wait\s+\*\*(?P<duration>[^*]+)\*\*\s+before requesting another review\.",
        body,
        flags=re.IGNORECASE,
    )
    if not wait_match:
        return None

    wait_text = wait_match.group("duration").strip()
    wait_seconds = _parse_wait_duration_seconds(wait_text)
    if wait_seconds is None:
        return None

    updated_at = _comment_last_updated_at(comment)
    if updated_at is None:
        return None

    return {
        "comment_id": comment.get("id"),
        "html_url": str(comment.get("html_url") or comment.get("url") or "").strip(),
        "wait_text": wait_text,
        "wait_seconds": wait_seconds,
        "updated_at": updated_at,
        "resume_after": updated_at + timedelta(seconds=wait_seconds),
    }


def _extract_coderabbit_review_failed_status(
    comment: dict[str, Any],
) -> dict[str, Any] | None:
    """コメントから CodeRabbit のレビュー失敗ステータスを抽出する。"""
    body = str(comment.get("body") or "")
    body_lower = body.lower()
    if CODERABBIT_REVIEW_FAILED_MARKER.lower() not in body_lower:
        return None
    if CODERABBIT_REVIEW_FAILED_HEAD_CHANGED_MARKER.lower() not in body_lower:
        return None

    updated_at = _comment_last_updated_at(comment)
    if updated_at is None:
        return None

    return {
        "comment_id": comment.get("id"),
        "html_url": str(comment.get("html_url") or comment.get("url") or "").strip(),
        "updated_at": updated_at,
    }


def _latest_coderabbit_activity_at(
    pr_data: dict[str, Any],
    review_comments: list[dict[str, Any]],
    issue_comments: list[dict[str, Any]],
) -> datetime | None:
    """CodeRabbit の最新アクティビティ日時を取得する。"""
    latest: datetime | None = None

    def _update(candidate: datetime | None) -> None:
        nonlocal latest
        if candidate is None:
            return
        if latest is None or candidate > latest:
            latest = candidate

    for review in pr_data.get("reviews", []):
        login = str(review.get("author", {}).get("login", ""))
        if is_coderabbit_login(login):
            _update(
                _parse_github_timestamp(str(review.get("submittedAt") or ""))
                or _parse_github_timestamp(str(review.get("updatedAt") or ""))
            )

    for comment in review_comments:
        login = str(comment.get("user", {}).get("login", ""))
        if is_coderabbit_login(login):
            _update(_comment_last_updated_at(comment))

    for comment in issue_comments:
        login = str(comment.get("user", {}).get("login", ""))
        if is_coderabbit_login(login):
            _update(_comment_last_updated_at(comment))

    return latest


def _latest_coderabbit_review_submitted_at(pr_data: dict[str, Any]) -> datetime | None:
    """CodeRabbit の最新レビュー送信日時を取得する（レビューのみ、コメントは除外）。

    レート制限やレビュー失敗の通知が古いかどうかの判定に使用する。
    新しいレビュー送信のみがレート制限解除を意味する。
    """
    latest: datetime | None = None
    for review in pr_data.get("reviews", []):
        login = str(review.get("author", {}).get("login", ""))
        if not is_coderabbit_login(login):
            continue
        ts = _parse_github_timestamp(
            str(review.get("submittedAt") or "")
        ) or _parse_github_timestamp(str(review.get("updatedAt") or ""))
        if ts is not None and (latest is None or ts > latest):
            latest = ts
    return latest


def get_active_coderabbit_rate_limit(
    pr_data: dict[str, Any],
    review_comments: list[dict[str, Any]],
    issue_comments: list[dict[str, Any]],
) -> dict[str, Any] | None:
    """有効な CodeRabbit レート制限を取得する。"""
    latest_rate_limit: dict[str, Any] | None = None
    for comment in issue_comments:
        login = str(comment.get("user", {}).get("login", ""))
        if not is_coderabbit_login(login):
            continue
        rate_limit_status = _extract_coderabbit_rate_limit_status(comment)
        if rate_limit_status is None:
            continue
        if (
            latest_rate_limit is None
            or rate_limit_status["updated_at"] > latest_rate_limit["updated_at"]
        ):
            latest_rate_limit = rate_limit_status

    if latest_rate_limit is None:
        return None

    # レビュー送信があった場合のみレート制限を「解消済み」とみなす
    latest_review = _latest_coderabbit_review_submitted_at(pr_data)
    if latest_review is not None and latest_review > latest_rate_limit["updated_at"]:
        return None
    return latest_rate_limit


def get_active_coderabbit_review_failed(
    pr_data: dict[str, Any],
    review_comments: list[dict[str, Any]],
    issue_comments: list[dict[str, Any]],
) -> dict[str, Any] | None:
    """有効な CodeRabbit レビュー失敗ステータスを取得する。"""
    latest_review_failed: dict[str, Any] | None = None
    for comment in issue_comments:
        login = str(comment.get("user", {}).get("login", ""))
        if not is_coderabbit_login(login):
            continue
        review_failed_status = _extract_coderabbit_review_failed_status(comment)
        if review_failed_status is None:
            continue
        if (
            latest_review_failed is None
            or review_failed_status["updated_at"] > latest_review_failed["updated_at"]
        ):
            latest_review_failed = review_failed_status

    if latest_review_failed is None:
        return None

    latest_review = _latest_coderabbit_review_submitted_at(pr_data)
    if latest_review is not None and latest_review > latest_review_failed["updated_at"]:
        return None
    return latest_review_failed


def _has_resume_comment_after(
    issue_comments: list[dict[str, Any]], threshold: datetime
) -> bool:
    """指定日時以降に resume コメントが存在するか確認する。"""
    normalized_target = CODERABBIT_RESUME_COMMENT.strip().lower()
    for comment in issue_comments:
        body = str(comment.get("body") or "").strip().lower()
        if body != normalized_target:
            continue
        posted_at = _comment_last_updated_at(comment)
        if posted_at is not None and posted_at >= threshold:
            return True
    return False


def _post_issue_comment(repo: str, pr_number: int, body: str) -> bool:
    """PR にイシューコメントを投稿する。"""
    try:
        result = run_command(
            [
                "gh",
                "api",
                f"repos/{repo}/issues/{pr_number}/comments",
                "-X",
                "POST",
                "-f",
                f"body={body}",
            ],
            check=False,
        )
    except SubprocessError as exc:
        print(
            f"Warning: failed to post comment to PR #{pr_number}: {exc}",
            file=sys.stderr,
        )
        return False
    if result.returncode == 0:
        print(f"Posted comment to PR #{pr_number}: {body}")
        return True

    print(
        f"Warning: failed to post comment to PR #{pr_number}: {(result.stderr or result.stdout).strip()}",
        file=sys.stderr,
    )
    return False


def maybe_auto_resume_coderabbit_review(
    *,
    repo: str,
    pr_number: int,
    issue_comments: list[dict[str, Any]],
    rate_limit_status: dict[str, Any] | None,
    auto_resume_enabled: bool,
    remaining_resume_posts: int,
    dry_run: bool,
    summarize_only: bool,
) -> bool:
    """レート制限解除後に CodeRabbit の resume コメントを自動投稿する。"""
    if rate_limit_status is None:
        return False
    if not auto_resume_enabled:
        print(
            f"CodeRabbit rate limit detected for PR #{pr_number}; auto resume is disabled."
        )
        return False
    if remaining_resume_posts <= 0:
        print(
            f"CodeRabbit rate limit detected for PR #{pr_number}; "
            "auto resume per-run limit reached."
        )
        return False

    resume_after = rate_limit_status["resume_after"]
    now = datetime.now(timezone.utc)
    if now < resume_after:
        remaining = int((resume_after - now).total_seconds())
        print(
            f"CodeRabbit rate limit detected for PR #{pr_number}; auto resume available in {_format_duration(remaining)}."
        )
        return False

    threshold = rate_limit_status["updated_at"]
    if _has_resume_comment_after(issue_comments, threshold):
        print(
            f"Resume comment already exists after the latest CodeRabbit rate-limit notice on PR #{pr_number}."
        )
        return False

    if dry_run:
        print(
            f"[DRY RUN] Would post CodeRabbit resume comment to PR #{pr_number}: {CODERABBIT_RESUME_COMMENT}"
        )
        return False
    if summarize_only:
        print(
            f"Summarize-only mode: skip posting CodeRabbit resume comment to PR #{pr_number}."
        )
        return False

    return _post_issue_comment(repo, pr_number, CODERABBIT_RESUME_COMMENT)


def maybe_auto_resume_coderabbit_review_failed(
    *,
    repo: str,
    pr_number: int,
    issue_comments: list[dict[str, Any]],
    review_failed_status: dict[str, Any] | None,
    auto_resume_enabled: bool,
    remaining_resume_posts: int,
    dry_run: bool,
    summarize_only: bool,
) -> bool:
    """レビュー失敗後に CodeRabbit の resume コメントを自動投稿する。"""
    if review_failed_status is None:
        return False
    if not auto_resume_enabled:
        print(
            f"CodeRabbit review failure detected for PR #{pr_number}; auto resume is disabled."
        )
        return False
    if remaining_resume_posts <= 0:
        print(
            f"CodeRabbit review failure detected for PR #{pr_number}; "
            "auto resume per-run limit reached."
        )
        return False

    threshold = review_failed_status["updated_at"]
    if _has_resume_comment_after(issue_comments, threshold):
        print(
            f"Resume comment already exists after the latest CodeRabbit review-failed notice on PR #{pr_number}."
        )
        return False

    if dry_run:
        print(
            f"[DRY RUN] Would post CodeRabbit resume comment to PR #{pr_number}: {CODERABBIT_RESUME_COMMENT}"
        )
        return False
    if summarize_only:
        print(
            f"Summarize-only mode: skip posting CodeRabbit resume comment to PR #{pr_number}."
        )
        return False

    return _post_issue_comment(repo, pr_number, CODERABBIT_RESUME_COMMENT)


def contains_coderabbit_processing_marker(
    pr_data: dict[str, Any],
    review_comments: list[dict[str, Any]],
    issue_comments: list[dict[str, Any]] | None = None,
) -> bool:
    """CodeRabbit の処理中マーカーが存在するか確認する。"""
    for review in pr_data.get("reviews", []):
        login = review.get("author", {}).get("login", "")
        body = review.get("body", "") or ""
        if is_coderabbit_login(login) and CODERABBIT_PROCESSING_MARKER in body:
            return True

    for comment in pr_data.get("comments", []):
        login = comment.get("author", {}).get("login", "")
        body = comment.get("body", "") or ""
        if is_coderabbit_login(login) and CODERABBIT_PROCESSING_MARKER in body:
            return True

    for comment in review_comments:
        login = comment.get("user", {}).get("login", "")
        body = comment.get("body", "") or ""
        if is_coderabbit_login(login) and CODERABBIT_PROCESSING_MARKER in body:
            return True

    for comment in issue_comments or []:
        login = comment.get("user", {}).get("login", "")
        body = comment.get("body", "") or ""
        if is_coderabbit_login(login) and CODERABBIT_PROCESSING_MARKER in body:
            return True

    return False
