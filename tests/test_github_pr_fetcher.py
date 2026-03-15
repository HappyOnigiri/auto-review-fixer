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
