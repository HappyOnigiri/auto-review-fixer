"""共有型定義モジュール。複数ファイルで使用する TypedDict を定義する。"""

from typing import Any, TypedDict

# AppConfig は将来的に TypedDict 化するための型エイリアス。
# 30 以上のキーを持つ複雑な型のため、段階的移行を見据えてエイリアスとして定義する。
AppConfig = dict[str, Any]  # dict-any: ok


class UserInfo(TypedDict, total=False):
    """GitHub ユーザー情報。"""

    login: str
    name: str
    email: str


class LabelInfo(TypedDict, total=False):
    """GitHub ラベル情報。"""

    id: int
    name: str
    color: str


class CommitInfo(TypedDict, total=False):
    """コミット情報（gh pr view --json commits）。"""

    oid: str
    messageHeadline: str
    committedDate: str


class _RepositoryEntryBase(TypedDict):
    repo: str


class RepositoryEntry(_RepositoryEntryBase, total=False):
    """リポジトリ設定エントリ（.refix-batch.yaml の repositories[] 要素）。"""

    user_name: str | None
    user_email: str | None
    setup: dict | None
    models: dict
    ci_log_max_lines: int
    write_result_to_comment: bool
    auto_merge: bool
    enabled_pr_labels: list
    coderabbit_auto_resume: bool
    coderabbit_auto_resume_triggers: dict
    coderabbit_auto_resume_max_per_run: int
    coderabbit_auto_resume_stale_minutes: int
    coderabbit_require_review: bool
    coderabbit_block_while_processing: bool
    coderabbit_ignore_nitpick: bool
    process_draft_prs: bool
    include_fork_repositories: bool
    state_comment_timezone: str
    merge_method: str
    base_update_method: str
    max_modified_prs_per_run: int
    max_committed_prs_per_run: int
    max_claude_prs_per_run: int
    ci_empty_as_success: bool
    ci_empty_grace_minutes: int
    exclude_authors: list
    exclude_labels: list
    target_authors: list
    auto_merge_authors: list
    triggers: dict


class CIErrorDigest(TypedDict):
    """CI ログから抽出したエラー情報のダイジェスト。"""

    error_type: str
    error_message: str
    failed_test: str
    file_line: str
    summary: str


class CIFailureMaterial(TypedDict):
    """CI 失敗プロンプト素材（collect_ci_failure_materials の戻り値要素）。"""

    run_id: str
    source: str
    truncated: bool
    excerpt_lines: list[str]
    digest: CIErrorDigest


class CheckRunData(TypedDict, total=False):
    """REST API の生 check run データ（_filter_check_runs の入出力）。"""

    name: str
    conclusion: str | None
    status: str
    details_url: str
    html_url: str
    id: int


class CheckStatus(TypedDict, total=False):
    """正規化済み CI チェックステータス（PRData.check_runs の要素）。"""

    name: str
    conclusion: str
    state: str
    detailsUrl: str
    targetUrl: str
    context: str
    workflowName: str


class NormalizedReview(TypedDict, total=False):
    """正規化済み PR レビュー（fetch_pr_reviews の戻り値要素）。"""

    id: str
    databaseId: int
    author: UserInfo
    body: str
    state: str
    submittedAt: str
    updatedAt: str
    url: str


class GitHubComment(TypedDict, total=False):
    """GitHub コメント（issue comment / review comment の REST API レスポンス）。

    REST API は user フィールド、GraphQL は author フィールドを使用するため両方を定義。
    """

    id: int | str
    body: str
    user: UserInfo
    author: UserInfo
    created_at: str
    createdAt: str
    updated_at: str
    updatedAt: str
    html_url: str
    url: str


class PRData(TypedDict, total=False):
    """PR データ（fetch_open_prs / fetch_pr_details の戻り値）。

    REST API と GraphQL の両方のレスポンス形式を統合した型。
    """

    number: int
    title: str
    author: UserInfo
    createdAt: str
    updatedAt: str
    labels: list[LabelInfo]
    isDraft: bool
    state: str  # "OPEN", "MERGED", "CLOSED"
    check_runs: list[CheckStatus]
    reviews: list[NormalizedReview]
    comments: list[GitHubComment]
    body: str
    headRefName: str
    baseRefName: str
    headRefOid: str
    commits: list[CommitInfo]
    mergedAt: str
