"""Unit tests for ci_precheck module."""

import os
from pathlib import Path
from unittest.mock import patch

import ci_precheck


class TestParseReposFromEnv:
    """Tests for parse_repos_from_env()."""

    def test_parses_repo_spec_and_deduplicates(self):
        with patch(
            "ci_precheck._expand_repo_spec",
            side_effect=[["owner/repo"], ["owner/repo"], ["owner/lib-a", "owner/lib-b"]],
        ):
            repos = ci_precheck.parse_repos_from_env(
                "owner/repo:User:user@example.com,owner/repo,owner/lib*"
            )
        assert repos == ["owner/repo", "owner/lib-a", "owner/lib-b"]

    def test_skips_invalid_entries_and_owner_wildcard(self, capsys):
        with patch("ci_precheck._expand_repo_spec", return_value=["owner/repo"]):
            repos = ci_precheck.parse_repos_from_env("invalid,own*/repo,owner/repo")
        captured = capsys.readouterr()
        assert repos == ["owner/repo"]
        assert "skipping invalid repo entry" in captured.err
        assert "owner wildcard is not supported" in captured.err


class TestExpandRepoSpec:
    """Tests for wildcard expansion."""

    def test_expand_repo_spec_with_wildcard(self):
        with patch(
            "ci_precheck._list_repositories_for_owner",
            return_value=["owner/lib-a", "owner/lib-b", "owner/app"],
        ):
            repos = ci_precheck._expand_repo_spec("owner", "lib-*")
        assert repos == ["owner/lib-a", "owner/lib-b"]


class TestCheckReviewTargets:
    """Tests for check_review_targets()."""

    def test_detects_open_pr_without_review_target(self):
        with (
            patch("ci_precheck._list_open_pr_numbers", return_value=[1]),
            patch("ci_precheck._pr_has_coderabbit_review", return_value=False),
            patch("ci_precheck._pr_has_unresolved_coderabbit_thread", return_value=False),
        ):
            result = ci_precheck.check_review_targets(["owner/repo"])

        assert result.has_open_pr is True
        assert result.has_review_target is False
        assert result.should_run is False
        assert result.target_prs == []

    def test_detects_review_target(self):
        with (
            patch("ci_precheck._list_open_pr_numbers", return_value=[1, 2]),
            patch("ci_precheck._pr_has_coderabbit_review", side_effect=[False, True]),
            patch("ci_precheck._pr_has_unresolved_coderabbit_thread", return_value=False),
        ):
            result = ci_precheck.check_review_targets(["owner/repo"])

        assert result.has_open_pr is True
        assert result.has_review_target is True
        assert result.should_run is True
        assert result.target_prs == ["owner/repo#2"]


class TestMain:
    """Tests for main()."""

    def test_main_writes_github_outputs(self, tmp_path: Path):
        output_file = tmp_path / "github_output.txt"
        env = {
            "REPOS": "owner/repo",
            "GITHUB_OUTPUT": str(output_file),
        }
        with (
            patch.dict(os.environ, env, clear=False),
            patch("ci_precheck.parse_repos_from_env", return_value=["owner/repo"]),
            patch(
                "ci_precheck.check_review_targets",
                return_value=ci_precheck.PrecheckResult(
                    has_open_pr=True,
                    has_review_target=False,
                    target_prs=[],
                ),
            ),
        ):
            code = ci_precheck.main()

        assert code == 0
        written = output_file.read_text(encoding="utf-8")
        assert "has_open_pr=true" in written
        assert "has_review_target=false" in written
        assert "should_run=false" in written

    def test_main_errors_when_repos_empty(self):
        with patch.dict(os.environ, {"REPOS": "   "}, clear=False):
            code = ci_precheck.main()
        assert code == 1
