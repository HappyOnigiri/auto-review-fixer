"""Unit tests for github_pr_fetcher."""

import pytest

import github_pr_fetcher


def test_fetch_open_prs_uses_large_limit_by_default(mocker, make_cmd_result):
    mock_run = mocker.patch(
        "github_pr_fetcher.run_command",
        return_value=make_cmd_result('[{"number": 1, "title": "Test"}]'),
    )
    prs = github_pr_fetcher.fetch_open_prs("owner/repo")

    assert prs == [{"number": 1, "title": "Test"}]
    assert any("isDraft" in arg for arg in mock_run.call_args.args[0])
    assert mock_run.call_args.args[0][-1] == "1000"


def test_fetch_open_prs_raises_on_invalid_json(mocker, make_cmd_result):
    mocker.patch(
        "github_pr_fetcher.run_command",
        return_value=make_cmd_result("{not-json"),
    )
    with pytest.raises(RuntimeError, match="Failed to parse PR list"):
        github_pr_fetcher.fetch_open_prs("owner/repo")


def test_fetch_single_pr_returns_pr_data(mocker, make_cmd_result):
    pr_json = '{"number": 42, "title": "Fix bug", "isDraft": false}'
    mock_run = mocker.patch(
        "github_pr_fetcher.run_command",
        return_value=make_cmd_result(pr_json),
    )
    pr = github_pr_fetcher.fetch_single_pr("owner/repo", 42)

    assert pr.get("number") == 42
    assert pr.get("title") == "Fix bug"
    cmd = mock_run.call_args.args[0]
    assert "gh" in cmd
    assert "pr" in cmd
    assert "view" in cmd
    assert "42" in cmd
    assert "--repo" in cmd
    assert "owner/repo" in cmd
    assert "isDraft" in " ".join(cmd)


def test_fetch_single_pr_raises_on_nonzero_returncode(mocker, make_cmd_result):
    mocker.patch(
        "github_pr_fetcher.run_command",
        return_value=make_cmd_result("", returncode=1, stderr="PR not found"),
    )
    with pytest.raises(RuntimeError, match="Error fetching PR #99"):
        github_pr_fetcher.fetch_single_pr("owner/repo", 99)


def test_fetch_single_pr_raises_on_invalid_json(mocker, make_cmd_result):
    mocker.patch(
        "github_pr_fetcher.run_command",
        return_value=make_cmd_result("{not-json"),
    )
    with pytest.raises(RuntimeError, match="Failed to parse PR #"):
        github_pr_fetcher.fetch_single_pr("owner/repo", 1)
