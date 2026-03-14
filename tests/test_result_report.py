"""Unit tests for result_report module."""

import result_report
from unittest.mock import patch


class TestFormatPhaseResultBlock:
    def test_basic_format(self):
        block = result_report.format_phase_result_block(
            phase_label="ci-fix",
            stdout_text="All tests passed",
            timestamp="2026-03-13 15:30:00 JST",
        )
        assert "#### CI 修正" in block
        assert "**実行日時:** 2026-03-13 15:30:00 JST" in block
        assert "All tests passed" in block
        assert "<summary>stdout</summary>" not in block
        assert "```" in block
        assert "**対象コメント:**" not in block

    def test_review_fix_with_comment_urls(self):
        block = result_report.format_phase_result_block(
            phase_label="review-fix",
            stdout_text="Fixed the issue",
            timestamp="2026-03-13 10:00:00 JST",
            comment_urls=["https://github.com/owner/repo/pull/1#r123"],
        )
        assert "#### レビュー修正" in block
        assert "**対象コメント:**" in block
        assert "[link1](https://github.com/owner/repo/pull/1#r123)" in block

    def test_merge_conflict_resolution_label(self):
        block = result_report.format_phase_result_block(
            phase_label="merge-conflict-resolution",
            stdout_text="Resolved",
            timestamp="2026-03-13 10:00:00 JST",
        )
        assert "#### コンフリクト解消" in block

    def test_multiple_comment_urls(self):
        block = result_report.format_phase_result_block(
            phase_label="review-fix",
            stdout_text="Fixed",
            timestamp="2026-03-13 10:00:00 JST",
            comment_urls=["https://github.com/a", "https://github.com/b"],
        )
        assert "[link1](https://github.com/a)" in block
        assert "[link2](https://github.com/b)" in block


class TestMergeResultLogBody:
    def test_prepends_new_blocks_before_existing(self):
        existing = "old content"
        new_blocks = ["block A", "block B"]
        result = result_report.merge_result_log_body(existing, new_blocks)
        assert result == "block A\n\nblock B\n\nold content"

    def test_with_no_existing(self):
        result = result_report.merge_result_log_body("", ["block A"])
        assert result == "block A"

    def test_with_no_new_blocks(self):
        result = result_report.merge_result_log_body("existing", [])
        assert result == "existing"

    def test_empty_blocks_are_skipped(self):
        result = result_report.merge_result_log_body(
            "existing", ["", "  ", "real block"]
        )
        assert result == "real block\n\nexisting"

    def test_both_empty(self):
        result = result_report.merge_result_log_body("", [])
        assert result == ""


class TestBuildPhaseResultEntry:
    def test_generates_block_with_timestamp(self):
        with patch(
            "result_report.current_timestamp", return_value="2026-03-13 15:30:00 JST"
        ):
            entry = result_report.build_phase_result_entry(
                phase_label="ci-fix",
                stdout_text="output text",
                timezone_name="JST",
            )
        assert "2026-03-13 15:30:00 JST" in entry
        assert "#### CI 修正" in entry
        assert "output text" in entry

    def test_passes_comment_urls_for_review_fix(self):
        with patch(
            "result_report.current_timestamp", return_value="2026-03-13 10:00:00 JST"
        ):
            entry = result_report.build_phase_result_entry(
                phase_label="review-fix",
                stdout_text="fixed",
                timezone_name="JST",
                comment_urls=["https://github.com/owner/repo/pull/1#r123"],
            )
        assert "**対象コメント:**" in entry
