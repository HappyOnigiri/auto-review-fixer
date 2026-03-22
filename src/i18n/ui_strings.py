"""GitHub UI string translations for Refix.

Keys:
    state_comment.description
    state_comment.result_log_summary
    state_comment.review_list_summary
    state_comment.table_header_date
    state_comment.truncation_notice
    result_report.phase_title.ci-fix
    result_report.phase_title.merge-conflict-resolution
    result_report.phase_title.review-fix
    result_report.executed_at
    result_report.target_comments
"""

from i18n import register

_UI_STRINGS: dict[str, dict[str, str]] = {
    "state_comment.description": {
        "en": (
            "<!-- This comment is used by Refix to record processing state. "
            "Do not manually edit or delete it. -->"
        ),
        "ja": (
            "<!-- このコメントは Refix が処理状態を記録するためのものです。"
            "手動で編集・削除しないでください。 -->"
        ),
    },
    "state_comment.result_log_summary": {
        "en": "Execution Log",
        "ja": "実行ログ",
    },
    "state_comment.review_list_summary": {
        "en": "Processed Reviews",
        "ja": "対応済みレビュー一覧",
    },
    "state_comment.table_header_date": {
        "en": "Processed At",
        "ja": "処理日時",
    },
    "state_comment.truncation_notice": {
        "en": "\n\n*Older execution logs have been omitted due to length limits.*",
        "ja": "\n\n*古い実行ログは長さ制限のため省略されています。*",
    },
    "result_report.phase_title.ci-fix": {
        "en": "CI Fix",
        "ja": "CI 修正",
    },
    "result_report.phase_title.merge-conflict-resolution": {
        "en": "Conflict Resolution",
        "ja": "コンフリクト解消",
    },
    "result_report.phase_title.review-fix": {
        "en": "Review Fix",
        "ja": "レビュー修正",
    },
    "result_report.executed_at": {
        "en": "**Executed at:** {timestamp}",
        "ja": "**実行日時:** {timestamp}",
    },
    "result_report.target_comments": {
        "en": "**Target comments:** {url_links}",
        "ja": "**対象コメント:** {url_links}",
    },
}

register(_UI_STRINGS)
