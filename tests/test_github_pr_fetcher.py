"""Unit tests for github_pr_fetcher."""

from unittest.mock import Mock, patch

import pytest

import github_pr_fetcher


def test_fetch_open_prs_uses_large_limit_by_default():
    result = Mock(returncode=0, stdout='[{"number": 1, "title": "Test"}]', stderr="")

    with patch("github_pr_fetcher.run_command", return_value=result) as mock_run:
        prs = github_pr_fetcher.fetch_open_prs("owner/repo")

    assert prs == [{"number": 1, "title": "Test"}]
    assert any("isDraft" in arg for arg in mock_run.call_args.args[0])
    assert mock_run.call_args.args[0][-1] == "1000"


def test_fetch_open_prs_raises_on_invalid_json():
    result = Mock(returncode=0, stdout="{not-json", stderr="")

    with patch("github_pr_fetcher.run_command", return_value=result):
        with pytest.raises(RuntimeError, match="Failed to parse PR list"):
            github_pr_fetcher.fetch_open_prs("owner/repo")
