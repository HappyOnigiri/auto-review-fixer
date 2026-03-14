"""CI チェックの状態確認と CI 修正プロンプト生成を行うモジュール。"""

import json
import re
import sys
from datetime import datetime, timedelta, timezone
from typing import Any

from pr_reviewer import _fetch_classic_statuses_via_rest, _filter_check_runs
from prompt_builder import _xml_escape, _xml_escape_attr
from subprocess_helpers import SubprocessError, run_command
from error_collector import ErrorCollector

# --- 定数 ---
SUCCESSFUL_CI_STATES = {"SUCCESS", "SKIPPED", "NEUTRAL"}
FAILED_CI_CONCLUSIONS = {
    "FAILURE",
    "TIMED_OUT",
    "ACTION_REQUIRED",
    "CANCELLED",
    "STALE",
    "STARTUP_FAILURE",
}
FAILED_CI_STATES = {"ERROR", "FAILURE"}
GITHUB_ACTIONS_RUN_URL_PATTERN = re.compile(r"/actions/runs/(\d+)")


def extract_failing_ci_contexts(pr_data: dict[str, Any]) -> list[dict[str, str]]:
    """pr_data['check_runs']（REST check-runs 形式）から失敗した CI コンテキストを抽出する。

    NOTE: statusCheckRollup (GraphQL) は Fine-grained PAT ではアクセス不可のため使用禁止。
    """
    status_rollup = pr_data.get("check_runs") or []
    if not isinstance(status_rollup, list):
        return []

    failing_contexts: list[dict[str, str]] = []
    for context in status_rollup:
        if not isinstance(context, dict):
            continue

        conclusion = str(context.get("conclusion") or "").upper()
        state = str(context.get("state") or "").upper()
        failed = conclusion in FAILED_CI_CONCLUSIONS or state in FAILED_CI_STATES
        if not failed:
            continue

        name = (
            str(context.get("name") or "")
            or str(context.get("context") or "")
            or str(context.get("workflowName") or "")
            or "unknown-check"
        )
        details_url = str(context.get("detailsUrl") or context.get("targetUrl") or "")
        run_match = GITHUB_ACTIONS_RUN_URL_PATTERN.search(details_url)
        run_id = run_match.group(1) if run_match else ""
        status_label = conclusion or state or "FAILED"
        failing_contexts.append(
            {
                "name": name,
                "status": status_label,
                "details_url": details_url,
                "run_id": run_id,
            }
        )
    return failing_contexts


def _extract_ci_error_digest_from_failed_log(log_text: str) -> dict[str, str]:
    """gh run view --log-failed の出力から構造化されたエラーダイジェストを抽出する。"""
    digest = {
        "error_type": "",
        "error_message": "",
        "failed_test": "",
        "file_line": "",
        "summary": "",
    }
    lines = log_text.splitlines()
    for line in lines:
        if not digest["failed_test"]:
            match_failed_test = re.search(
                r"\b(?:FAILED|ERROR)\s+(?:collecting\s+)?([^\s]+)", line
            )
            if match_failed_test:
                digest["failed_test"] = match_failed_test.group(1)
        if not digest["file_line"]:
            match_file_line = re.search(r"\b([^\s:]+\.py:\d+)", line)
            if match_file_line:
                digest["file_line"] = match_file_line.group(1)
        if not digest["summary"]:
            match_summary = re.search(
                r"\b(\d+\s+(?:failed|errors?)(?:,.*)?\s+in\s+[^\s]+)", line
            )
            if match_summary:
                digest["summary"] = match_summary.group(1)
        if not digest["error_type"]:
            match_error = re.search(
                r"\bE\s+([A-Za-z_][A-Za-z0-9_.]*(?:Error|Exception)):\s*(.*)$", line
            )
            if match_error:
                digest["error_type"] = match_error.group(1)
                digest["error_message"] = match_error.group(2)
    return digest


def _select_ci_failure_log_excerpt(
    log_text: str, max_lines: int
) -> tuple[list[str], bool]:
    """プロンプトコンテキスト用のログ抜粋を選択する。"""
    lines = log_text.splitlines()
    if not lines:
        return [], False

    start_index = 0
    for i, line in enumerate(lines):
        if re.search(r"={5,}\s+(?:FAILURES|ERRORS)\b", line):
            start_index = max(0, i - 5)
            break
    excerpt = lines[start_index:]
    truncated = False
    if len(excerpt) > max_lines:
        excerpt = excerpt[:max_lines]
        truncated = True
    return excerpt, truncated


def collect_ci_failure_materials(
    repo: str,
    failing_contexts: list[dict[str, str]],
    *,
    max_lines: int,
    error_collector: ErrorCollector | None = None,
    pr_number: int | None = None,
) -> list[dict[str, Any]]:
    """失敗した CI ログを取得し、構造化されたプロンプト素材を構築する。"""
    max_lines = max(20, max_lines)

    materials: list[dict[str, Any]] = []
    seen_run_ids: set[str] = set()
    for context in failing_contexts:
        run_id = str(context.get("run_id", "")).strip()
        if not run_id or run_id in seen_run_ids:
            continue
        seen_run_ids.add(run_id)
        try:
            run_view_result = run_command(
                ["gh", "run", "view", run_id, "--repo", repo, "--log-failed"],
                check=False,
                timeout=60,
            )
        except SubprocessError:
            msg = f"failed to fetch CI logs for run {run_id}; skipping"
            print(f"Warning: {msg}", file=sys.stderr)
            if error_collector:
                if pr_number is not None:
                    error_collector.add_pr_error(repo, pr_number, msg)
                else:
                    error_collector.add_repo_error(repo, msg)
            continue
        if run_view_result.returncode != 0:
            msg = f"failed to fetch failed CI logs for run {run_id}: {run_view_result.stderr.strip()}"
            print(f"Warning: {msg}", file=sys.stderr)
            if error_collector:
                if pr_number is not None:
                    error_collector.add_pr_error(repo, pr_number, msg)
                else:
                    error_collector.add_repo_error(repo, msg)
            continue
        raw_log = run_view_result.stdout.strip("\n")
        if not raw_log.strip():
            continue
        excerpt_lines, truncated = _select_ci_failure_log_excerpt(
            raw_log, max_lines=max_lines
        )
        materials.append(
            {
                "run_id": run_id,
                "source": "gh run view --log-failed",
                "truncated": truncated,
                "excerpt_lines": excerpt_lines,
                "digest": _extract_ci_error_digest_from_failed_log(raw_log),
            }
        )
    return materials


def build_ci_fix_prompt(
    pr_number: int,
    title: str,
    failing_contexts: list[dict[str, str]],
    ci_failure_materials: list[dict[str, Any]] | None = None,
) -> str:
    """CI 修正フェーズ用のプロンプトを生成する。"""
    checks = []
    for item in failing_contexts:
        name = _xml_escape_attr(item.get("name", "unknown-check"))
        status = _xml_escape_attr(item.get("status", "FAILED"))
        details_url = _xml_escape_attr(item.get("details_url", ""))
        run_id = _xml_escape_attr(item.get("run_id", ""))
        attrs = [f'name="{name}"', f'status="{status}"']
        if details_url:
            attrs.append(f'details_url="{details_url}"')
        if run_id:
            attrs.append(f'run_id="{run_id}"')
        checks.append("  <check " + " ".join(attrs) + " />")

    checks_block = (
        '<ci_failures data-only="true">\n' + "\n".join(checks) + "\n</ci_failures>"
        if checks
        else '<ci_failures data-only="true" />'
    )
    escaped_title = _xml_escape(title)
    digest_block = ""
    logs_block = ""
    if ci_failure_materials:
        digest_entries: list[str] = []
        log_entries: list[str] = []
        for material in ci_failure_materials:
            run_id = _xml_escape_attr(str(material.get("run_id", "")))
            digest = (
                material.get("digest", {})
                if isinstance(material.get("digest"), dict)
                else {}
            )
            error_type = _xml_escape_attr(str(digest.get("error_type", "")))
            error_message = _xml_escape(str(digest.get("error_message", "")))
            failed_test = _xml_escape(str(digest.get("failed_test", "")))
            file_line = _xml_escape(str(digest.get("file_line", "")))
            summary = _xml_escape(str(digest.get("summary", "")))
            digest_entries.append(
                "\n".join(
                    [
                        f'  <digest run_id="{run_id}">',
                        f'    <error type="{error_type}">{error_message}</error>',
                        f"    <failed_test>{failed_test}</failed_test>",
                        f"    <file_line>{file_line}</file_line>",
                        f"    <test_result_summary>{summary}</test_result_summary>",
                        "  </digest>",
                    ]
                )
            )
            source = _xml_escape_attr(
                str(material.get("source", "gh run view --log-failed"))
            )
            truncated = "true" if material.get("truncated") else "false"
            excerpt_lines = material.get("excerpt_lines", [])
            escaped_lines = []
            if isinstance(excerpt_lines, list):
                escaped_lines = [_xml_escape(str(line)) for line in excerpt_lines]
            log_entries.append(
                "\n".join(
                    [
                        f'  <failed_run run_id="{run_id}" source="{source}" truncated="{truncated}">',
                        *[f"    {line}" for line in escaped_lines],
                        "  </failed_run>",
                    ]
                )
            )
        digest_block = (
            '<ci_error_digest data-only="true">\n'
            + "\n".join(digest_entries)
            + "\n</ci_error_digest>"
        )
        logs_block = (
            '<ci_failure_logs data-only="true">\n'
            + "\n".join(log_entries)
            + "\n</ci_failure_logs>"
        )

    extra_blocks = [checks_block]
    if digest_block:
        extra_blocks.append(digest_block)
    if logs_block:
        extra_blocks.append(logs_block)
    extra_data = "\n\n".join(extra_blocks)
    pr_meta_block = f"""<pr_meta data-only="true">
  <pr_number>{pr_number}</pr_number>
  <pr_title>{escaped_title}</pr_title>
</pr_meta>"""

    return f"""<instructions>
以下は CI 失敗の先行修正フェーズです。
- 目的: 失敗している CI を通すために必要な修正だけを最小限で行う
- 必須条件:
  1. このフェーズでは CI 修正のみを行う（レビュー指摘対応や merge base 取り込みは行わない）
  2. 変更した場合のみ git commit して push する
  3. 変更不要なら commit / push はしない
- 対象PRの情報は <pr_meta> ブロックを参照すること
</instructions>

{pr_meta_block}

{extra_data}
"""


def are_all_ci_checks_successful(
    repo: str,
    pr_number: int,
    *,
    ci_empty_as_success: bool = True,
    ci_empty_grace_minutes: int = 5,
    error_collector: ErrorCollector | None = None,
) -> bool | None:
    """REST API 経由で全 CI チェックが成功しているか確認する。

    NOTE: statusCheckRollup / gh pr checks (GraphQL) は Fine-grained PAT ではアクセス不可のため使用禁止。
    """
    # head commit SHA を取得
    try:
        head_result = run_command(
            ["gh", "api", f"repos/{repo}/pulls/{pr_number}", "--jq", ".head.sha"],
            check=False,
            timeout=60,
        )
    except Exception:
        msg = f"timed out fetching head SHA for PR #{pr_number}; skip refix: done labeling."
        print(f"Warning: {msg}", file=sys.stderr)
        if error_collector:
            error_collector.add_pr_error(repo, pr_number, msg)
        return None
    if head_result.returncode != 0 or not (
        head_sha := (head_result.stdout or "").strip()
    ):
        msg = f"CI checks unavailable for PR #{pr_number}; skip refix: done labeling."
        print(msg)
        if error_collector:
            error_collector.add_pr_error(repo, pr_number, msg)
        return None

    # REST 経由で check runs を取得
    try:
        result = run_command(
            [
                "gh",
                "api",
                f"repos/{repo}/commits/{head_sha}/check-runs",
                "--paginate",
                "--slurp",
            ],
            check=False,
            timeout=60,
        )
    except Exception:
        msg = f"timed out fetching check runs for PR #{pr_number}; skip refix: done labeling."
        print(f"Warning: {msg}", file=sys.stderr)
        if error_collector:
            error_collector.add_pr_error(repo, pr_number, msg)
        return None
    runs: list[dict[str, Any]] = []
    if result.returncode != 0:
        stderr_text = result.stderr or ""
        if "403" in stderr_text:
            msg = f"check-runs API returned 403 for PR #{pr_number} (insufficient permissions); treating as empty."
            print(f"Warning: {msg}", file=sys.stderr)
            if error_collector:
                error_collector.add_pr_error(repo, pr_number, msg)
        else:
            msg = f"check-runs API failed for PR #{pr_number} (exit {result.returncode}); skip refix: done labeling."
            print(f"Warning: {msg}", file=sys.stderr)
            if error_collector:
                error_collector.add_pr_error(repo, pr_number, msg)
            return None
    else:
        try:
            data = json.loads(result.stdout) if result.stdout else []
        except json.JSONDecodeError:
            msg = f"failed to parse CI check state for PR #{pr_number}"
            print(f"Warning: {msg}", file=sys.stderr)
            if error_collector:
                error_collector.add_pr_error(repo, pr_number, msg)
            return None

        for page in data if isinstance(data, list) else [data]:
            if isinstance(page, dict):
                runs.extend(
                    r for r in (page.get("check_runs") or []) if isinstance(r, dict)
                )
        runs = _filter_check_runs(runs, repo)

    # classic statuses（Jenkins, Travis 等）も取得
    classic = _fetch_classic_statuses_via_rest(repo, head_sha)

    if not runs and not classic:
        if not ci_empty_as_success:
            print(
                f"CI checks unavailable for PR #{pr_number}; skip refix: done labeling."
            )
            return False
        # checks が空: 最新コミットが猶予期間より古ければ CI なしとみなす
        try:
            commit_result = run_command(
                [
                    "gh",
                    "api",
                    f"repos/{repo}/commits/{head_sha}",
                    "--jq",
                    ".commit.committer.date",
                ],
                check=False,
                timeout=60,
            )
        except Exception:
            msg = (
                f"timed out fetching commit date for PR #{pr_number}; "
                "skip refix: done labeling."
            )
            print(f"Warning: {msg}", file=sys.stderr)
            if error_collector:
                error_collector.add_pr_error(repo, pr_number, msg)
            return None
        if commit_result.returncode != 0 or not (
            date_str := (commit_result.stdout or "").strip()
        ):
            msg = (
                f"CI checks unavailable for PR #{pr_number}; skip refix: done labeling."
            )
            print(msg)
            if error_collector:
                error_collector.add_pr_error(repo, pr_number, msg)
            return None
        try:
            # jq は JSON 文字列を出力するため、パースして生の日付文字列を取得
            if date_str.startswith('"') and date_str.endswith('"'):
                date_str = json.loads(date_str)
            commit_dt = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
            if commit_dt.tzinfo is None:
                commit_dt = commit_dt.replace(tzinfo=timezone.utc)
            elapsed = datetime.now(timezone.utc) - commit_dt
            if elapsed < timedelta(minutes=ci_empty_grace_minutes):
                print(
                    f"CI checks unavailable for PR #{pr_number} "
                    f"(empty, commit < {ci_empty_grace_minutes}min ago); skip refix: done labeling."
                )
                return None  # 猶予期間: 経過後にリトライ; updatedAt をキャッシュしない
        except (ValueError, TypeError):
            msg = (
                f"CI checks unavailable for PR #{pr_number}; skip refix: done labeling."
            )
            print(msg)
            if error_collector:
                error_collector.add_pr_error(repo, pr_number, msg)
            return None
        print(
            f"PR #{pr_number}: no CI checks, commit >{ci_empty_grace_minutes}min ago; treat as success."
        )
        return True

    # check runs を評価: 完了済み runs は conclusion が SUCCESSFUL セットに含まれる必要あり
    conclusions: list[str] = []
    for r in runs:
        if not isinstance(r, dict):
            continue
        status = str(r.get("status") or "").upper()
        conclusion = str(r.get("conclusion") or "").upper()
        if status != "COMPLETED":
            # まだ実行中
            return False
        conclusions.append(conclusion)

    # classic statuses を評価（正規化: "conclusion" は大文字化された state を保持）
    for cs in classic:
        if not isinstance(cs, dict):
            continue
        state = str(cs.get("conclusion") or cs.get("state") or "").upper()
        if not state or state == "PENDING":
            # まだ待機中
            return False
        conclusions.append(state)

    if not conclusions:
        print(f"CI checks unavailable for PR #{pr_number}; skip refix: done labeling.")
        return False

    all_success = all(c in SUCCESSFUL_CI_STATES for c in conclusions)
    if not all_success:
        print(
            f"CI checks not all successful for PR #{pr_number}: {', '.join(conclusions)}"
        )
    return all_success
