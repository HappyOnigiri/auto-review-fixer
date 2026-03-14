"""実行結果ブロックのフォーマットと結合を行うモジュール。"""

from state_manager import current_timestamp

PHASE_TITLES = {
    "ci-fix": "CI 修正",
    "merge-conflict-resolution": "コンフリクト解消",
    "review-fix": "レビュー修正",
}


def format_phase_result_block(
    phase_label: str,
    stdout_text: str,
    timestamp: str,
    comment_urls: list[str] | None = None,
) -> str:
    """1フェーズの実行結果ブロックを生成する。"""
    phase_title = PHASE_TITLES.get(phase_label, phase_label)
    stripped_stdout = stdout_text.strip()
    fence = "```"
    while fence in stripped_stdout:
        fence += "`"
    lines = [
        f"#### {phase_title}",
        "",
        f"**実行日時:** {timestamp}",
    ]
    if comment_urls:
        url_links = ", ".join(
            f"[link{i + 1}]({url})" for i, url in enumerate(comment_urls)
        )
        lines.append(f"**対象コメント:** {url_links}")
    lines.extend(
        [
            "",
            fence,
            stripped_stdout,
            fence,
        ]
    )
    return "\n".join(lines)


def merge_result_log_body(
    existing_body: str,
    new_blocks: list[str],
) -> str:
    """新しいブロックを既存の本文の前にマージする。"""
    parts = [block.strip() for block in new_blocks if block.strip()]
    existing = (existing_body or "").strip()
    if existing:
        parts.append(existing)
    return "\n\n".join(parts)


def build_phase_result_entry(
    phase_label: str,
    stdout_text: str,
    timezone_name: str,
    comment_urls: list[str] | None = None,
) -> str:
    """タイムスタンプを生成し format_phase_result_block を呼ぶ。"""
    timestamp = current_timestamp(timezone_name)
    return format_phase_result_block(
        phase_label=phase_label,
        stdout_text=stdout_text,
        timestamp=timestamp,
        comment_urls=comment_urls,
    )
