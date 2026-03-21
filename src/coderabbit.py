"""CodeRabbit との連携処理を行うモジュール。

レート制限検出、レビュー失敗ステータス確認、自動 resume 投稿などを担当する。
"""

import re
import sys
from datetime import datetime, timedelta, timezone
from typing import TypedDict

from subprocess_helpers import SubprocessError, run_command
from error_collector import ErrorCollector
from type_defs import GitHubComment, PRData

_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)


class _CodeRabbitStatusBase(TypedDict, total=False):
    """CodeRabbit ステータスの共通フィールド。"""

    comment_id: int | str | None
    html_url: str
    updated_at: datetime


class RateLimitStatus(_CodeRabbitStatusBase, total=False):
    """CodeRabbit レート制限ステータス。"""

    wait_text: str
    wait_seconds: int
    resume_after: datetime


class ReviewFailedStatus(_CodeRabbitStatusBase):
    """CodeRabbit レビュー失敗ステータス。"""


class ReviewSkippedStatus(_CodeRabbitStatusBase, total=False):
    """CodeRabbit レビュースキップステータス。"""

    reason: str
    reason_label: str


# --- 定数 ---
# REST API は "coderabbitai[bot]"、GraphQL は "coderabbitai" を返す
CODERABBIT_BOT_LOGIN = "coderabbitai"
CODERABBIT_PROCESSING_MARKER = "Currently processing new changes in this PR."
CODERABBIT_RATE_LIMIT_MARKER = "Rate limit exceeded"
CODERABBIT_REVIEW_FAILED_MARKER = "## Review failed"
CODERABBIT_REVIEW_SKIPPED_MARKER = "## Review skipped"
CODERABBIT_REVIEW_FAILED_HEAD_CHANGED_MARKER = (
    "The head commit changed during the review"
)
CODERABBIT_REVIEW_SKIPPED_REASON_RATE_LIMIT = "rate_limit"
CODERABBIT_REVIEW_SKIPPED_REASON_DRAFT_DETECTED = "draft_detected"
CODERABBIT_REVIEW_SKIPPED_REASON_LABELS = {
    CODERABBIT_REVIEW_SKIPPED_REASON_RATE_LIMIT: "Rate limit exceeded",
    CODERABBIT_REVIEW_SKIPPED_REASON_DRAFT_DETECTED: "Draft detected",
}
CODERABBIT_RESUME_COMMENT = "@coderabbitai resume"
CODERABBIT_REVIEW_COMMENT = "@coderabbitai review"


def _pr_ref(repo: str, pr_number: int) -> str:
    """ログ向けの PR 識別子を返す。"""
    return f"{repo} PR #{pr_number}"


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


def _comment_last_updated_at(comment: GitHubComment) -> datetime | None:
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
    comment: GitHubComment,
) -> RateLimitStatus | None:
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
    comment: GitHubComment,
) -> ReviewFailedStatus | None:
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


def _extract_coderabbit_review_skipped_status(
    comment: GitHubComment,
) -> ReviewSkippedStatus | None:
    """コメントから CodeRabbit の Review skipped ステータスを抽出する。"""
    body = str(comment.get("body") or "")
    body_lower = body.lower()
    if CODERABBIT_REVIEW_SKIPPED_MARKER.lower() not in body_lower:
        return None

    reason: str | None = None
    if (
        CODERABBIT_REVIEW_SKIPPED_REASON_LABELS[
            CODERABBIT_REVIEW_SKIPPED_REASON_DRAFT_DETECTED
        ].lower()
        in body_lower
    ):
        reason = CODERABBIT_REVIEW_SKIPPED_REASON_DRAFT_DETECTED
    elif (
        CODERABBIT_REVIEW_SKIPPED_REASON_LABELS[
            CODERABBIT_REVIEW_SKIPPED_REASON_RATE_LIMIT
        ].lower()
        in body_lower
    ):
        reason = CODERABBIT_REVIEW_SKIPPED_REASON_RATE_LIMIT
    if reason is None:
        return None

    updated_at = _comment_last_updated_at(comment)
    if updated_at is None:
        return None

    return {
        "comment_id": comment.get("id"),
        "html_url": str(comment.get("html_url") or comment.get("url") or "").strip(),
        "updated_at": updated_at,
        "reason": reason,
        "reason_label": CODERABBIT_REVIEW_SKIPPED_REASON_LABELS[reason],
    }


def _latest_coderabbit_activity_at(
    pr_data: PRData,
    review_comments: list[GitHubComment],
    issue_comments: list[GitHubComment],
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


def _latest_coderabbit_review_submitted_at(pr_data: PRData) -> datetime | None:
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
    pr_data: PRData,
    review_comments: list[GitHubComment],
    issue_comments: list[GitHubComment],
) -> RateLimitStatus | None:
    """有効な CodeRabbit レート制限を取得する。"""
    latest_rate_limit: RateLimitStatus | None = None
    for comment in issue_comments:
        login = str(comment.get("user", {}).get("login", ""))
        if not is_coderabbit_login(login):
            continue
        rate_limit_status = _extract_coderabbit_rate_limit_status(comment)
        if rate_limit_status is None:
            continue
        if latest_rate_limit is None or rate_limit_status.get(
            "updated_at", _EPOCH
        ) > latest_rate_limit.get("updated_at", _EPOCH):
            latest_rate_limit = rate_limit_status

    if latest_rate_limit is None:
        return None

    # resume_after を過ぎていればレート制限は解消済み
    now = datetime.now(timezone.utc)
    resume_after = latest_rate_limit.get("resume_after")
    if resume_after is not None and now >= resume_after:
        return None

    # レビュー送信があった場合もレート制限を「解消済み」とみなす
    latest_review = _latest_coderabbit_review_submitted_at(pr_data)
    if latest_review is not None and latest_review > latest_rate_limit.get(
        "updated_at", _EPOCH
    ):
        return None
    return latest_rate_limit


def get_active_coderabbit_review_failed(
    pr_data: PRData,
    review_comments: list[GitHubComment],
    issue_comments: list[GitHubComment],
) -> ReviewFailedStatus | None:
    """有効な CodeRabbit レビュー失敗ステータスを取得する。"""
    latest_review_failed: ReviewFailedStatus | None = None
    for comment in issue_comments:
        login = str(comment.get("user", {}).get("login", ""))
        if not is_coderabbit_login(login):
            continue
        review_failed_status = _extract_coderabbit_review_failed_status(comment)
        if review_failed_status is None:
            continue
        if latest_review_failed is None or review_failed_status.get(
            "updated_at", _EPOCH
        ) > latest_review_failed.get("updated_at", _EPOCH):
            latest_review_failed = review_failed_status

    if latest_review_failed is None:
        return None

    latest_review = _latest_coderabbit_review_submitted_at(pr_data)
    if latest_review is not None and latest_review > latest_review_failed.get(
        "updated_at", _EPOCH
    ):
        return None
    return latest_review_failed


def get_active_coderabbit_review_skipped(
    pr_data: PRData,
    review_comments: list[GitHubComment],
    issue_comments: list[GitHubComment],
) -> ReviewSkippedStatus | None:
    """有効な CodeRabbit Review skipped ステータスを取得する。"""
    latest_review_skipped: ReviewSkippedStatus | None = None
    for comment in issue_comments:
        login = str(comment.get("user", {}).get("login", ""))
        if not is_coderabbit_login(login):
            continue
        review_skipped_status = _extract_coderabbit_review_skipped_status(comment)
        if review_skipped_status is None:
            continue
        if latest_review_skipped is None or review_skipped_status.get(
            "updated_at", _EPOCH
        ) > latest_review_skipped.get("updated_at", _EPOCH):
            latest_review_skipped = review_skipped_status

    if latest_review_skipped is None:
        return None

    latest_review = _latest_coderabbit_review_submitted_at(pr_data)
    if latest_review is not None and latest_review > latest_review_skipped.get(
        "updated_at", _EPOCH
    ):
        return None
    return latest_review_skipped


def _has_issue_comment_with_body_after(
    issue_comments: list[GitHubComment],
    threshold: datetime,
    target_body: str,
    *,
    max_age: timedelta | None = None,
) -> bool:
    """指定日時以降に特定本文のコメントが存在するか確認する。

    max_age が指定された場合、その期間より古いコメントは「存在しない」とみなす。
    """
    normalized_target = target_body.strip().lower()
    staleness_cutoff = (datetime.now(timezone.utc) - max_age) if max_age else None
    for comment in issue_comments:
        body = str(comment.get("body") or "").strip().lower()
        if body != normalized_target:
            continue
        posted_at = _comment_last_updated_at(comment)
        if posted_at is not None and posted_at >= threshold:
            if staleness_cutoff is None or posted_at >= staleness_cutoff:
                return True
    return False


def _has_resume_comment_after(
    issue_comments: list[GitHubComment],
    threshold: datetime,
    *,
    max_age: timedelta | None = None,
) -> bool:
    """指定日時以降に resume コメントが存在するか確認する。"""
    return _has_issue_comment_with_body_after(
        issue_comments, threshold, CODERABBIT_RESUME_COMMENT, max_age=max_age
    )


def _has_review_comment_after(
    issue_comments: list[GitHubComment],
    threshold: datetime,
    *,
    max_age: timedelta | None = None,
) -> bool:
    """指定日時以降に review コメントが存在するか確認する。"""
    return _has_issue_comment_with_body_after(
        issue_comments, threshold, CODERABBIT_REVIEW_COMMENT, max_age=max_age
    )


def _post_issue_comment(
    repo: str,
    pr_number: int,
    body: str,
    *,
    error_collector: ErrorCollector | None = None,
) -> bool:
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
        msg = f"failed to post comment to {_pr_ref(repo, pr_number)}: {exc}"
        print(f"Warning: {msg}", file=sys.stderr)
        if error_collector:
            error_collector.add_pr_error(repo, pr_number, msg)
        return False
    if result.returncode == 0:
        print(f"Posted comment to {_pr_ref(repo, pr_number)}: {body}")
        return True

    msg = (
        f"failed to post comment to {_pr_ref(repo, pr_number)}: "
        f"{(result.stderr or result.stdout).strip()}"
    )
    print(f"Warning: {msg}", file=sys.stderr)
    if error_collector:
        error_collector.add_pr_error(repo, pr_number, msg)
    return False


def maybe_auto_resume_coderabbit_review(
    *,
    repo: str,
    pr_number: int,
    issue_comments: list[GitHubComment],
    rate_limit_status: RateLimitStatus | None,
    auto_resume_enabled: bool,
    remaining_resume_posts: int,
    dry_run: bool,
    summarize_only: bool,
    trigger_enabled: bool = True,
    stale_minutes: int = 30,
    error_collector: ErrorCollector | None = None,
) -> bool:
    """レート制限解除後に CodeRabbit の resume コメントを自動投稿する。"""
    if rate_limit_status is None:
        return False
    if not auto_resume_enabled:
        print(
            f"CodeRabbit rate limit detected for {_pr_ref(repo, pr_number)}; "
            "auto resume is disabled."
        )
        return False
    if not trigger_enabled:
        print(
            f"CodeRabbit rate limit detected for {_pr_ref(repo, pr_number)}; "
            "rate-limit trigger is disabled."
        )
        return False
    if remaining_resume_posts <= 0:
        print(
            f"CodeRabbit rate limit detected for {_pr_ref(repo, pr_number)}; "
            "auto resume per-run limit reached."
        )
        return False

    resume_after = rate_limit_status.get("resume_after", _EPOCH)
    now = datetime.now(timezone.utc)
    if now < resume_after:
        remaining = int((resume_after - now).total_seconds())
        print(
            "CodeRabbit rate limit detected for "
            f"{_pr_ref(repo, pr_number)}; auto resume available in "
            f"{_format_duration(remaining)}."
        )
        return False

    threshold = rate_limit_status.get("updated_at", _EPOCH)
    _max_age = timedelta(minutes=stale_minutes) if stale_minutes > 0 else None
    if _has_resume_comment_after(issue_comments, threshold, max_age=_max_age):
        print(
            "Resume comment already exists after the latest CodeRabbit "
            f"rate-limit notice on {_pr_ref(repo, pr_number)}."
        )
        return False

    if dry_run:
        print(
            "[DRY RUN] Would post CodeRabbit resume comment to "
            f"{_pr_ref(repo, pr_number)}: {CODERABBIT_RESUME_COMMENT}"
        )
        return False
    if summarize_only:
        print(
            "Summarize-only mode: skip posting CodeRabbit resume comment to "
            f"{_pr_ref(repo, pr_number)}."
        )
        return False

    return _post_issue_comment(
        repo, pr_number, CODERABBIT_RESUME_COMMENT, error_collector=error_collector
    )


def maybe_auto_resume_coderabbit_review_failed(
    *,
    repo: str,
    pr_number: int,
    issue_comments: list[GitHubComment],
    review_failed_status: ReviewFailedStatus | None,
    auto_resume_enabled: bool,
    remaining_resume_posts: int,
    dry_run: bool,
    summarize_only: bool,
    stale_minutes: int = 30,
    error_collector: ErrorCollector | None = None,
) -> bool:
    """レビュー失敗後に CodeRabbit の resume コメントを自動投稿する。"""
    if review_failed_status is None:
        return False
    if not auto_resume_enabled:
        print(
            f"CodeRabbit review failure detected for {_pr_ref(repo, pr_number)}; "
            "auto resume is disabled."
        )
        return False
    if remaining_resume_posts <= 0:
        print(
            f"CodeRabbit review failure detected for {_pr_ref(repo, pr_number)}; "
            "auto resume per-run limit reached."
        )
        return False

    threshold = review_failed_status.get("updated_at", _EPOCH)
    _max_age = timedelta(minutes=stale_minutes) if stale_minutes > 0 else None
    if _has_resume_comment_after(issue_comments, threshold, max_age=_max_age):
        print(
            "Resume comment already exists after the latest CodeRabbit "
            f"review-failed notice on {_pr_ref(repo, pr_number)}."
        )
        return False

    if dry_run:
        print(
            "[DRY RUN] Would post CodeRabbit resume comment to "
            f"{_pr_ref(repo, pr_number)}: {CODERABBIT_RESUME_COMMENT}"
        )
        return False
    if summarize_only:
        print(
            "Summarize-only mode: skip posting CodeRabbit resume comment to "
            f"{_pr_ref(repo, pr_number)}."
        )
        return False

    return _post_issue_comment(
        repo, pr_number, CODERABBIT_RESUME_COMMENT, error_collector=error_collector
    )


def maybe_auto_trigger_coderabbit_review_skipped(
    *,
    repo: str,
    pr_number: int,
    issue_comments: list[GitHubComment],
    review_skipped_status: ReviewSkippedStatus | None,
    auto_resume_enabled: bool,
    trigger_enabled: bool,
    remaining_resume_posts: int,
    dry_run: bool,
    summarize_only: bool,
    is_draft: bool,
    stale_minutes: int = 30,
    error_collector: ErrorCollector | None = None,
) -> bool:
    """Review skipped 後に CodeRabbit の単発 review を再トリガする。"""
    if review_skipped_status is None:
        return False

    reason_label = str(review_skipped_status.get("reason_label") or "unknown reason")
    if not auto_resume_enabled:
        print(
            f"CodeRabbit review skipped for {_pr_ref(repo, pr_number)} "
            f"({reason_label}); auto resume is disabled."
        )
        return False
    if not trigger_enabled:
        print(
            f"CodeRabbit review skipped for {_pr_ref(repo, pr_number)} "
            f"({reason_label}); trigger is disabled."
        )
        return False
    if remaining_resume_posts <= 0:
        print(
            f"CodeRabbit review skipped for {_pr_ref(repo, pr_number)} "
            "but auto resume per-run limit reached."
        )
        return False
    if (
        review_skipped_status.get("reason")
        == CODERABBIT_REVIEW_SKIPPED_REASON_DRAFT_DETECTED
        and is_draft
    ):
        print(
            f"CodeRabbit review skipped for {_pr_ref(repo, pr_number)} "
            "(Draft detected); PR is still draft."
        )
        return False

    threshold = review_skipped_status.get("updated_at", _EPOCH)
    _max_age = timedelta(minutes=stale_minutes) if stale_minutes > 0 else None
    if _has_review_comment_after(issue_comments, threshold, max_age=_max_age):
        print(
            "Review command already exists after the latest CodeRabbit "
            f"review-skipped notice on {_pr_ref(repo, pr_number)}."
        )
        return False

    if dry_run:
        print(
            "[DRY RUN] Would post CodeRabbit review comment to "
            f"{_pr_ref(repo, pr_number)}: {CODERABBIT_REVIEW_COMMENT}"
        )
        return False
    if summarize_only:
        print(
            "Summarize-only mode: skip posting CodeRabbit review comment to "
            f"{_pr_ref(repo, pr_number)}."
        )
        return False

    return _post_issue_comment(
        repo, pr_number, CODERABBIT_REVIEW_COMMENT, error_collector=error_collector
    )


def has_coderabbit_comments(
    pr_data: PRData,
    review_comments: list[GitHubComment],
    issue_comments: list[GitHubComment] | None = None,
) -> bool:
    """CodeRabbit によるコメント・レビューが1件以上存在するか確認する。"""
    for review in pr_data.get("reviews", []):
        login = review.get("author", {}).get("login", "")
        if is_coderabbit_login(login):
            return True

    for comment in pr_data.get("comments", []):
        if is_coderabbit_login(
            comment.get("author", {}).get("login", "")
        ) or is_coderabbit_login(comment.get("user", {}).get("login", "")):
            return True

    for comment in review_comments:
        if is_coderabbit_login(
            comment.get("user", {}).get("login", "")
        ) or is_coderabbit_login(comment.get("author", {}).get("login", "")):
            return True

    for comment in issue_comments or []:
        if is_coderabbit_login(
            comment.get("user", {}).get("login", "")
        ) or is_coderabbit_login(comment.get("author", {}).get("login", "")):
            return True

    return False


def contains_coderabbit_processing_marker(
    pr_data: PRData,
    review_comments: list[GitHubComment],
    issue_comments: list[GitHubComment] | None = None,
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
