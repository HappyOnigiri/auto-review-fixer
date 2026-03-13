"""Unit tests for git_ops module."""

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

import git_ops


def _make_result(returncode=0, stdout="", stderr=""):
    return SimpleNamespace(returncode=returncode, stdout=stdout, stderr=stderr)


# ---------------------------------------------------------------------------
# get_branch_compare_status
# ---------------------------------------------------------------------------


def test_get_branch_compare_status_returns_status_and_behind_by():
    payload = '{"status": "diverged", "behind_by": 3}'
    with patch.object(
        git_ops, "run_command", return_value=_make_result(stdout=payload)
    ) as mock_run:
        status, behind_by = git_ops.get_branch_compare_status(
            "owner/repo", "main", "feature"
        )

    assert status == "diverged"
    assert behind_by == 3
    mock_run.assert_called_once()
    args = mock_run.call_args[0][0]
    assert "repos/owner/repo/compare/main...feature" in args[-1]


def test_get_branch_compare_status_raises_on_nonzero_returncode():
    with patch.object(
        git_ops,
        "run_command",
        return_value=_make_result(returncode=1, stderr="not found"),
    ):
        with pytest.raises(RuntimeError, match="Error fetching compare status"):
            git_ops.get_branch_compare_status("owner/repo", "main", "feature")


def test_get_branch_compare_status_raises_on_invalid_json():
    with patch.object(
        git_ops, "run_command", return_value=_make_result(stdout="not-json")
    ):
        with pytest.raises(RuntimeError, match="Failed to parse compare status"):
            git_ops.get_branch_compare_status("owner/repo", "main", "feature")


def test_get_branch_compare_status_raises_on_missing_fields():
    payload = '{"status": "ahead"}'  # behind_by missing
    with patch.object(
        git_ops, "run_command", return_value=_make_result(stdout=payload)
    ):
        with pytest.raises(RuntimeError, match="Unexpected compare payload"):
            git_ops.get_branch_compare_status("owner/repo", "main", "feature")


def test_get_branch_compare_status_url_encodes_branch_names():
    payload = '{"status": "identical", "behind_by": 0}'
    with patch.object(
        git_ops, "run_command", return_value=_make_result(stdout=payload)
    ) as mock_run:
        git_ops.get_branch_compare_status("owner/repo", "main", "feature/my branch")

    args = mock_run.call_args[0][0]
    assert "feature%2Fmy%20branch" in args[-1]


# ---------------------------------------------------------------------------
# needs_base_merge
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("compare_status", "behind_by", "expected"),
    [
        ("identical", 0, False),
        ("ahead", 0, False),
        ("behind", 0, True),
        ("diverged", 0, True),
        ("ahead", 1, True),
        ("identical", 2, True),
    ],
)
def test_needs_base_merge(compare_status, behind_by, expected):
    assert git_ops.needs_base_merge(compare_status, behind_by) == expected


# ---------------------------------------------------------------------------
# has_merge_conflicts
# ---------------------------------------------------------------------------


def test_has_merge_conflicts_returns_true_when_unmerged_files():
    with patch.object(
        git_ops, "run_git", return_value=_make_result(stdout="conflict.py\n")
    ):
        assert git_ops.has_merge_conflicts(Path("/some/repo")) is True


def test_has_merge_conflicts_returns_false_when_no_unmerged_files():
    with patch.object(git_ops, "run_git", return_value=_make_result(stdout="")):
        assert git_ops.has_merge_conflicts(Path("/some/repo")) is False


def test_has_merge_conflicts_raises_on_nonzero_returncode():
    with patch.object(
        git_ops, "run_git", return_value=_make_result(returncode=1, stdout="")
    ):
        with pytest.raises(RuntimeError, match="failed to detect merge conflicts"):
            git_ops.has_merge_conflicts(Path("/some/repo"))


# ---------------------------------------------------------------------------
# merge_base_branch
# ---------------------------------------------------------------------------


def test_merge_base_branch_returns_merged_changes_true_when_head_changed():
    works_dir = Path("/some/repo")
    fetch_result = _make_result()
    rev_parse_pre = _make_result(stdout="abc123\n")
    merge_result = _make_result()
    rev_parse_post = _make_result(stdout="def456\n")

    side_effects = [fetch_result, rev_parse_pre, merge_result, rev_parse_post]
    with patch.object(git_ops, "run_git", side_effect=side_effects):
        merged_changes, has_conflicts = git_ops.merge_base_branch(works_dir, "main")

    assert merged_changes is True
    assert has_conflicts is False


def test_merge_base_branch_returns_merged_changes_false_when_head_unchanged():
    works_dir = Path("/some/repo")
    same_sha = "abc123"
    side_effects = [
        _make_result(),  # fetch
        _make_result(stdout=f"{same_sha}\n"),  # rev-parse HEAD before
        _make_result(),  # merge (success)
        _make_result(stdout=f"{same_sha}\n"),  # rev-parse HEAD after
    ]
    with patch.object(git_ops, "run_git", side_effect=side_effects):
        merged_changes, has_conflicts = git_ops.merge_base_branch(works_dir, "main")

    assert merged_changes is False
    assert has_conflicts is False


def test_merge_base_branch_returns_has_conflicts_true_when_merge_fails_with_conflicts():
    works_dir = Path("/some/repo")
    side_effects = [
        _make_result(),  # fetch
        _make_result(stdout="abc123\n"),  # rev-parse HEAD before
        _make_result(returncode=1),  # merge fails
        _make_result(stdout="file.py\n"),  # has_merge_conflicts -> run_git diff
    ]
    with patch.object(git_ops, "run_git", side_effect=side_effects):
        merged_changes, has_conflicts = git_ops.merge_base_branch(works_dir, "main")

    assert merged_changes is False
    assert has_conflicts is True


def test_merge_base_branch_raises_when_merge_fails_without_conflicts():
    works_dir = Path("/some/repo")
    side_effects = [
        _make_result(),  # fetch
        _make_result(stdout="abc123\n"),  # rev-parse HEAD before
        _make_result(returncode=1, stderr="some other error"),  # merge fails
        _make_result(stdout=""),  # has_merge_conflicts -> no conflicts
    ]
    with patch.object(git_ops, "run_git", side_effect=side_effects):
        with pytest.raises(
            RuntimeError, match="git merge failed without conflict markers"
        ):
            git_ops.merge_base_branch(works_dir, "main")
