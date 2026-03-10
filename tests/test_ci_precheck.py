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
            patch("ci_precheck._get_pr_status_and_ids", return_value=("skip:no_coderabbit", [])),
        ):
            result = ci_precheck.check_review_targets(["owner/repo"])

        assert result.has_open_pr is True
        assert result.has_review_target is False
        assert result.should_run is False
        assert result.target_prs == []
        assert result.pr_statuses == [("owner/repo#1", "skip:no_coderabbit")]

    def test_detects_review_target(self):
        with (
            patch("ci_precheck._list_open_pr_numbers", return_value=[1, 2]),
            patch(
                "ci_precheck._get_pr_status_and_ids",
                side_effect=[("skip:no_coderabbit", []), ("target", ["PRR_xxx"])],
            ),
            patch("ci_precheck._db_available", return_value=False),
        ):
            result = ci_precheck.check_review_targets(["owner/repo"])

        assert result.has_open_pr is True
        assert result.has_review_target is True
        assert result.should_run is True
        assert result.target_prs == ["owner/repo#2"]
        assert result.pr_statuses == [
            ("owner/repo#1", "skip:no_coderabbit"),
            ("owner/repo#2", "target"),
        ]

    def test_detects_skip_all_resolved(self):
        with (
            patch("ci_precheck._list_open_pr_numbers", return_value=[1]),
            patch("ci_precheck._get_pr_status_and_ids", return_value=("skip:all_resolved", [])),
        ):
            result = ci_precheck.check_review_targets(["owner/repo"])

        assert result.has_open_pr is True
        assert result.has_review_target is False
        assert result.target_prs == []
        assert result.pr_statuses == [("owner/repo#1", "skip:all_resolved")]

    def test_detects_review_only_as_target(self):
        """Review-level CodeRabbit (no inline) should be target."""
        with (
            patch("ci_precheck._list_open_pr_numbers", return_value=[1]),
            patch("ci_precheck._get_pr_status_and_ids", return_value=("target", ["PRR_xxx"])),
            patch("ci_precheck._db_available", return_value=False),
        ):
            result = ci_precheck.check_review_targets(["owner/repo"])

        assert result.has_open_pr is True
        assert result.has_review_target is True
        assert result.target_prs == ["owner/repo#1"]
        assert result.pr_statuses == [("owner/repo#1", "target")]


class TestGetPrStatusAndIds:
    """Tests for _get_pr_status_and_ids() - review-level vs inline."""

    def test_review_only_returns_target_with_ids(self):
        """CodeRabbit review with body, no inline comments -> target with review id."""
        graphql_response = {
            "data": {
                "repository": {
                    "pullRequest": {
                        "reviews": {
                            "nodes": [
                                {"id": "PRR_xxx", "author": {"login": "coderabbitai[bot]"}},
                            ]
                        },
                        "reviewThreads": {"pageInfo": {"hasNextPage": False}, "nodes": []},
                    }
                }
            }
        }
        with patch("ci_precheck._run_gh_json", return_value=graphql_response):
            status, ids = ci_precheck._get_pr_status_and_ids("owner/repo", 1)
        assert status == "target"
        assert ids == ["PRR_xxx"]

    def test_review_multiple_ids_all_collected(self):
        """Multiple CodeRabbit review IDs on the same page must all be collected."""
        graphql_response = {
            "data": {
                "repository": {
                    "pullRequest": {
                        "reviews": {
                            "pageInfo": {"hasNextPage": False},
                            "nodes": [
                                {"id": "PRR_aaa", "author": {"login": "coderabbitai[bot]"}},
                                {"id": "PRR_bbb", "author": {"login": "coderabbitai[bot]"}},
                            ],
                        },
                    }
                }
            }
        }
        with patch("ci_precheck._run_gh_json", return_value=graphql_response):
            status, ids = ci_precheck._get_pr_status_and_ids("owner/repo", 1)
        assert status == "target"
        assert ids == ["PRR_aaa", "PRR_bbb"]

    def test_reviews_pagination_fetches_all_pages(self):
        """Reviews spanning multiple pages must all be fetched before returning."""
        page1 = {
            "data": {
                "repository": {
                    "pullRequest": {
                        "reviews": {
                            "pageInfo": {"hasNextPage": True, "endCursor": "cursor1"},
                            "nodes": [
                                {"id": "PRR_aaa", "author": {"login": "coderabbitai[bot]"}},
                            ],
                        },
                    }
                }
            }
        }
        page2 = {
            "data": {
                "repository": {
                    "pullRequest": {
                        "reviews": {
                            "pageInfo": {"hasNextPage": False, "endCursor": None},
                            "nodes": [
                                {"id": "PRR_bbb", "author": {"login": "coderabbitai[bot]"}},
                            ],
                        },
                    }
                }
            }
        }
        threads_empty = {
            "data": {
                "repository": {
                    "pullRequest": {
                        "reviewThreads": {
                            "pageInfo": {"hasNextPage": False},
                            "nodes": [],
                        },
                    }
                }
            }
        }
        with patch("ci_precheck._run_gh_json", side_effect=[page1, page2, threads_empty]):
            status, ids = ci_precheck._get_pr_status_and_ids("owner/repo", 1)
        assert status == "target"
        assert ids == ["PRR_aaa", "PRR_bbb"]

    def test_inline_only_returns_target_with_ids(self):
        """Unresolved inline CodeRabbit thread -> target with discussion id."""
        graphql_response = {
            "data": {
                "repository": {
                    "pullRequest": {
                        "reviews": {"nodes": []},
                        "reviewThreads": {
                            "pageInfo": {"hasNextPage": False},
                            "nodes": [
                                {
                                    "isResolved": False,
                                    "comments": {
                                        "pageInfo": {"hasNextPage": False},
                                        "nodes": [
                                            {
                                                "databaseId": 12345,
                                                "author": {"login": "coderabbitai[bot]"},
                                            },
                                        ],
                                    },
                                },
                            ],
                        },
                    }
                }
            }
        }
        with patch("ci_precheck._run_gh_json", return_value=graphql_response):
            status, ids = ci_precheck._get_pr_status_and_ids("owner/repo", 1)
        assert status == "target"
        assert ids == ["discussion_r12345"]

    def test_thread_comments_pagination_fetches_all_when_over_100(self):
        """Thread with >100 comments: paginates via node query instead of raising."""
        reviews_page = {
            "data": {
                "repository": {
                    "pullRequest": {
                        "reviews": {"pageInfo": {"hasNextPage": False}, "nodes": []},
                    }
                }
            }
        }
        threads_page = {
            "data": {
                "repository": {
                    "pullRequest": {
                        "reviewThreads": {
                            "pageInfo": {"hasNextPage": False},
                            "nodes": [
                                {
                                    "id": "PRRT_xxx",
                                    "isResolved": False,
                                    "comments": {
                                        "pageInfo": {"hasNextPage": True, "endCursor": "c1"},
                                        "nodes": [
                                            {"databaseId": 1, "author": {"login": "coderabbitai[bot]"}},
                                        ],
                                    },
                                },
                            ],
                        },
                    }
                }
            }
        }
        # node query returns all comments (incl. page 2) with CodeRabbit
        node_page = {
            "data": {
                "node": {
                    "comments": {
                        "pageInfo": {"hasNextPage": False},
                        "nodes": [
                            {"databaseId": 1, "author": {"login": "coderabbitai[bot]"}},
                            {"databaseId": 2, "author": {"login": "coderabbitai[bot]"}},
                        ],
                    }
                }
            }
        }
        with patch(
            "ci_precheck._run_gh_json",
            side_effect=[reviews_page, threads_page, node_page],
        ):
            status, ids = ci_precheck._get_pr_status_and_ids("owner/repo", 1)
        assert status == "target"
        assert "discussion_r1" in ids
        assert "discussion_r2" in ids

    def test_both_review_and_inline_ids_collected(self):
        """Both PRR_* review IDs and unresolved discussion_r* IDs should all be returned."""
        graphql_response = {
            "data": {
                "repository": {
                    "pullRequest": {
                        "reviews": {
                            "pageInfo": {"hasNextPage": False},
                            "nodes": [
                                {"id": "PRR_xxx", "author": {"login": "coderabbitai[bot]"}},
                            ],
                        },
                        "reviewThreads": {
                            "pageInfo": {"hasNextPage": False},
                            "nodes": [
                                {
                                    "isResolved": False,
                                    "comments": {
                                        "pageInfo": {"hasNextPage": False},
                                        "nodes": [
                                            {
                                                "databaseId": 12345,
                                                "author": {"login": "coderabbitai[bot]"},
                                            },
                                        ],
                                    },
                                },
                            ],
                        },
                    }
                }
            }
        }
        with patch("ci_precheck._run_gh_json", return_value=graphql_response):
            status, ids = ci_precheck._get_pr_status_and_ids("owner/repo", 1)
        assert status == "target"
        assert "PRR_xxx" in ids
        assert "discussion_r12345" in ids


class TestFilterTargetsByDb:
    """Tests for DB verification filtering."""

    def test_filters_all_processed_to_skip(self):
        """When all review IDs are in DB, PR becomes skip:all_processed."""
        with (
            patch("ci_precheck._list_open_pr_numbers", return_value=[1]),
            patch(
                "ci_precheck._get_pr_status_and_ids",
                return_value=("target", ["PRR_xxx", "discussion_r123"]),
            ),
            patch("ci_precheck._db_available", return_value=True),
            patch("ci_precheck._filter_targets_by_db") as mock_filter,
        ):
            mock_filter.return_value = ([], [("owner/repo#1", "skip:all_processed")])
            result = ci_precheck.check_review_targets(["owner/repo"])

        assert result.has_open_pr is True
        assert result.has_review_target is False
        assert result.target_prs == []
        assert result.pr_statuses == [("owner/repo#1", "skip:all_processed")]

    def test_keeps_unprocessed_as_target(self):
        """When some IDs not in DB, PR stays target."""
        with (
            patch("ci_precheck._list_open_pr_numbers", return_value=[1]),
            patch(
                "ci_precheck._get_pr_status_and_ids",
                return_value=("target", ["PRR_xxx"]),
            ),
            patch("ci_precheck._db_available", return_value=True),
            patch("ci_precheck._filter_targets_by_db") as mock_filter,
        ):
            mock_filter.return_value = (["owner/repo#1"], [])
            result = ci_precheck.check_review_targets(["owner/repo"])

        assert result.has_open_pr is True
        assert result.has_review_target is True
        assert result.target_prs == ["owner/repo#1"]
        assert result.pr_statuses == [("owner/repo#1", "target")]

    def test_skips_db_when_unavailable(self):
        """When DB not available, no filtering; candidates stay as targets."""
        with (
            patch("ci_precheck._list_open_pr_numbers", return_value=[1]),
            patch(
                "ci_precheck._get_pr_status_and_ids",
                return_value=("target", ["PRR_xxx"]),
            ),
            patch("ci_precheck._db_available", return_value=False),
        ):
            result = ci_precheck.check_review_targets(["owner/repo"])

        assert result.has_open_pr is True
        assert result.has_review_target is True
        assert result.target_prs == ["owner/repo#1"]
        assert result.pr_statuses == [("owner/repo#1", "target")]


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
                    pr_statuses=[("owner/repo#1", "skip:no_coderabbit")],
                ),
            ),
        ):
            code = ci_precheck.main()

        assert code == 0
        written = output_file.read_text(encoding="utf-8")
        assert "has_open_pr=true" in written
        assert "has_review_target=false" in written
        assert "should_run=false" in written
        assert "owner/repo#1: skip:no_coderabbit" in written

    def test_main_errors_when_repos_empty(self):
        with patch.dict(os.environ, {"REPOS": "   "}, clear=False):
            code = ci_precheck.main()
        assert code == 1
