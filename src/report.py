"""実行レポートの管理を行うモジュール。"""

import sys
from pathlib import Path

from ci_log import log_endgroup, log_group
from state_manager import StateComment, upsert_state_comment

# --- フェーズレポートのタイトル定義 ---
PHASE_REPORT_TITLES = {
    "ci-fix": "CI 修正",
    "merge-conflict-resolution": "コンフリクト解消",
    "review-fix": "レビュー修正",
}


def prepare_reports_dir(repo: str, works_dir: Path) -> Path:
    """リポジトリのレポートディレクトリを作成して返す。"""
    owner, repo_name = repo.split("/", 1)
    reports_root = works_dir.parent.parent / "reports"
    reports_dir = reports_root / f"{owner}__{repo_name}"
    reports_dir.mkdir(parents=True, exist_ok=True)
    return reports_dir


def build_phase_report_path(reports_dir: Path, pr_number: int, phase_label: str) -> str:
    """PR フェーズのレポートファイルの絶対パスを構築する。"""
    return str((reports_dir / f"pr_{pr_number}_{phase_label}.md").resolve())


def _read_runtime_report_content(report_path: str | None) -> str:
    """ランタイムレポートファイルを読み込み、正規化されたコンテンツを返す。"""
    if not report_path:
        return ""
    report_file = Path(report_path)
    if not report_file.exists():
        return ""
    return report_file.read_text(encoding="utf-8").strip()


def _format_report_for_state_comment(phase_label: str, report_content: str) -> str:
    """1つのフェーズレポートブロックを PR state comment 用にフォーマットする。"""
    normalized_content = report_content.strip()
    if not normalized_content:
        return ""
    phase_title = PHASE_REPORT_TITLES.get(phase_label, phase_label)
    return f"#### {phase_title}\n\n{normalized_content}"


def capture_state_comment_report(
    report_blocks: list[str], phase_label: str, report_path: str | None
) -> None:
    """フェーズレポートを PR state comment に埋め込むためにキャプチャする。"""
    if not report_path:
        return
    try:
        report_content = _read_runtime_report_content(report_path)
    except OSError as exc:
        print(
            f"Warning: failed to read report for state comment ({phase_label}): {exc}",
            file=sys.stderr,
        )
        return
    block = _format_report_for_state_comment(phase_label, report_content)
    if block:
        report_blocks.append(block)


def merge_state_comment_report_body(
    existing_report_body: str, new_report_blocks: list[str]
) -> str:
    """新しい実行レポートを既存のレポート本文の前にマージする。"""
    parts = [block.strip() for block in new_report_blocks if block.strip()]
    existing = (existing_report_body or "").strip()
    if existing:
        parts.append(existing)
    return "\n\n".join(parts)


def persist_state_comment_report_if_changed(
    repo: str,
    pr_number: int,
    state_comment: StateComment,
    report_body: str,
) -> bool:
    """レポートのみの state comment 更新をコンテンツに変更があった場合に永続化する。"""
    normalized_report_body = (report_body or "").strip()
    if normalized_report_body == state_comment.report_body.strip():
        return False
    upsert_state_comment(repo, pr_number, [], report_body=normalized_report_body)
    return True


def emit_runtime_pain_report(
    *,
    report_path: str | None,
    phase_label: str,
    silent: bool,
    claude_failed: bool = False,
) -> None:
    """--silent でない場合、または Claude が失敗した場合にランタイムレポート内容を出力する。"""
    if not report_path or (silent and not claude_failed):
        return
    report_file = Path(report_path)
    log_group(f"Runtime report ({phase_label})")
    try:
        print(f"[report {phase_label}] {report_file}", file=sys.stderr)
        if not report_file.exists():
            print("  レポート出力なし", file=sys.stderr)
            return
        try:
            content = report_file.read_text(encoding="utf-8").strip()
        except Exception as e:
            print(f"  failed to read report file: {e}", file=sys.stderr)
            return

        if not content:
            print("  レポート出力なし", file=sys.stderr)
            return

        print("  --- begin report ---", file=sys.stderr)
        print(content, file=sys.stderr)
        print("  --- end report ---", file=sys.stderr)
    finally:
        log_endgroup()
