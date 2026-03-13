"""Unit tests for report module."""

from unittest.mock import patch


import report
from state_manager import StateComment


# ---------------------------------------------------------------------------
# prepare_reports_dir
# ---------------------------------------------------------------------------


def test_prepare_reports_dir_creates_and_returns_path(tmp_path):
    # works_dir structure: <root>/works/<owner>__<repo>/
    works_dir = tmp_path / "works" / "owner__repo"
    works_dir.mkdir(parents=True)

    result = report.prepare_reports_dir("owner/repo", works_dir)

    expected = tmp_path / "reports" / "owner__repo"
    assert result == expected
    assert result.is_dir()


def test_prepare_reports_dir_is_idempotent(tmp_path):
    works_dir = tmp_path / "works" / "owner__myrepo"
    works_dir.mkdir(parents=True)

    first = report.prepare_reports_dir("owner/myrepo", works_dir)
    second = report.prepare_reports_dir("owner/myrepo", works_dir)

    assert first == second
    assert first.is_dir()


# ---------------------------------------------------------------------------
# build_phase_report_path
# ---------------------------------------------------------------------------


def test_build_phase_report_path_returns_correct_string(tmp_path):
    reports_dir = tmp_path / "reports" / "owner__repo"
    reports_dir.mkdir(parents=True)

    result = report.build_phase_report_path(reports_dir, 42, "ci-fix")

    assert result == str((reports_dir / "pr_42_ci-fix.md").resolve())


def test_build_phase_report_path_includes_pr_number_and_label(tmp_path):
    reports_dir = tmp_path / "reports"
    reports_dir.mkdir(parents=True)

    result = report.build_phase_report_path(reports_dir, 7, "review-fix")

    assert "pr_7_review-fix.md" in result


# ---------------------------------------------------------------------------
# merge_state_comment_report_body
# ---------------------------------------------------------------------------


def test_merge_state_comment_report_body_prepends_new_blocks_before_existing():
    existing = "old content"
    new_blocks = ["block A", "block B"]

    result = report.merge_state_comment_report_body(existing, new_blocks)

    assert result == "block A\n\nblock B\n\nold content"


def test_merge_state_comment_report_body_with_no_existing():
    result = report.merge_state_comment_report_body("", ["block A"])

    assert result == "block A"


def test_merge_state_comment_report_body_with_no_new_blocks():
    result = report.merge_state_comment_report_body("existing", [])

    assert result == "existing"


def test_merge_state_comment_report_body_with_empty_blocks_are_skipped():
    result = report.merge_state_comment_report_body(
        "existing", ["", "  ", "real block"]
    )

    assert result == "real block\n\nexisting"


def test_merge_state_comment_report_body_both_empty():
    result = report.merge_state_comment_report_body("", [])

    assert result == ""


# ---------------------------------------------------------------------------
# persist_state_comment_report_if_changed
# ---------------------------------------------------------------------------


def _make_state_comment(report_body=""):
    return StateComment(
        github_comment_id=None,
        body="",
        entries=[],
        processed_ids=set(),
        archived_ids=set(),
        report_body=report_body,
    )


def test_persist_state_comment_report_calls_upsert_when_body_changed():
    state_comment = _make_state_comment(report_body="old report")

    with patch.object(report, "upsert_state_comment") as mock_upsert:
        changed = report.persist_state_comment_report_if_changed(
            "owner/repo", 1, state_comment, "new report"
        )

    assert changed is True
    mock_upsert.assert_called_once_with("owner/repo", 1, [], report_body="new report")


def test_persist_state_comment_report_skips_upsert_when_body_unchanged():
    state_comment = _make_state_comment(report_body="same report")

    with patch.object(report, "upsert_state_comment") as mock_upsert:
        changed = report.persist_state_comment_report_if_changed(
            "owner/repo", 1, state_comment, "same report"
        )

    assert changed is False
    mock_upsert.assert_not_called()


def test_persist_state_comment_report_normalizes_whitespace_before_comparing():
    state_comment = _make_state_comment(report_body="  same report  ")

    with patch.object(report, "upsert_state_comment") as mock_upsert:
        changed = report.persist_state_comment_report_if_changed(
            "owner/repo", 1, state_comment, "same report"
        )

    assert changed is False
    mock_upsert.assert_not_called()
