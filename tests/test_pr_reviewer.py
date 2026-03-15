"""Unit tests for pr_reviewer helpers."""

import pytest

import pr_reviewer
from subprocess_helpers import SubprocessError
from type_defs import CheckRunData


def test_fetch_pr_reviews_normalizes_ids_and_urls(mocker, make_cmd_result):
    mocker.patch(
        "pr_reviewer.run_command",
        return_value=make_cmd_result(
            '[[{"id": 123, "user": {"login": "coderabbitai[bot]"}, "body": "fix", "state": "COMMENTED", "submitted_at": "2026-03-11T12:00:00Z", "html_url": "https://github.com/owner/repo/pull/1#pullrequestreview-123"}]]'
        ),
    )
    reviews = pr_reviewer.fetch_pr_reviews("owner/repo", 1)

    assert reviews == [
        {
            "id": "r123",
            "databaseId": 123,
            "author": {"login": "coderabbitai[bot]"},
            "body": "fix",
            "state": "COMMENTED",
            "submittedAt": "2026-03-11T12:00:00Z",
            "url": "https://github.com/owner/repo/pull/1#pullrequestreview-123",
        }
    ]


def test_fetch_pr_review_comments_flattens_paginated_response(mocker, make_cmd_result):
    mocker.patch(
        "pr_reviewer.run_command",
        return_value=make_cmd_result(
            '[[{"id": 10, "body": "a"}], [{"id": 11, "body": "b"}]]'
        ),
    )
    comments = pr_reviewer.fetch_pr_review_comments("owner/repo", 1)

    assert comments == [{"id": 10, "body": "a"}, {"id": 11, "body": "b"}]


def test_fetch_issue_comments_flattens_paginated_response(mocker, make_cmd_result):
    mocker.patch(
        "pr_reviewer.run_command",
        return_value=make_cmd_result(
            '[[{"id": 21, "body": "a"}], [{"id": 22, "body": "b"}]]'
        ),
    )
    comments = pr_reviewer.fetch_issue_comments("owner/repo", 1)

    assert comments == [{"id": 21, "body": "a"}, {"id": 22, "body": "b"}]


def test_fetch_pr_reviews_subprocess_error_raises(mocker):
    mocker.patch("pr_reviewer.run_command", side_effect=SubprocessError("net error"))
    with pytest.raises(RuntimeError, match="Failed to fetch PR reviews"):
        pr_reviewer.fetch_pr_reviews("owner/repo", 1)


def test_fetch_pr_reviews_nonzero_exit_raises(mocker, make_cmd_result):
    mocker.patch(
        "pr_reviewer.run_command",
        return_value=make_cmd_result("", returncode=1, stderr="API error"),
    )
    with pytest.raises(RuntimeError, match="Failed to fetch PR reviews"):
        pr_reviewer.fetch_pr_reviews("owner/repo", 1)


def test_fetch_pr_reviews_parse_failure_raises(mocker, make_cmd_result):
    mocker.patch("pr_reviewer.run_command", return_value=make_cmd_result("not-json"))
    with pytest.raises(RuntimeError, match="Failed to parse PR reviews response"):
        pr_reviewer.fetch_pr_reviews("owner/repo", 1)


def test_fetch_pr_review_comments_subprocess_error_raises(mocker):
    mocker.patch("pr_reviewer.run_command", side_effect=SubprocessError("net error"))
    with pytest.raises(RuntimeError, match="Failed to fetch review comments"):
        pr_reviewer.fetch_pr_review_comments("owner/repo", 1)


def test_fetch_pr_review_comments_nonzero_exit_raises(mocker, make_cmd_result):
    mocker.patch(
        "pr_reviewer.run_command",
        return_value=make_cmd_result("", returncode=1, stderr="API error"),
    )
    with pytest.raises(RuntimeError, match="Failed to fetch review comments"):
        pr_reviewer.fetch_pr_review_comments("owner/repo", 1)


def test_fetch_pr_review_comments_parse_failure_raises(mocker, make_cmd_result):
    mocker.patch("pr_reviewer.run_command", return_value=make_cmd_result("not-json"))
    with pytest.raises(RuntimeError, match="Failed to parse review comments response"):
        pr_reviewer.fetch_pr_review_comments("owner/repo", 1)


def test_fetch_review_threads_subprocess_error_raises(mocker):
    mocker.patch("pr_reviewer.run_command", side_effect=SubprocessError("net error"))
    with pytest.raises(RuntimeError, match="Failed to fetch review threads"):
        pr_reviewer.fetch_review_threads("owner/repo", 1)


def test_fetch_review_threads_nonzero_exit_raises(mocker, make_cmd_result):
    mocker.patch(
        "pr_reviewer.run_command",
        return_value=make_cmd_result("", returncode=1, stderr="API error"),
    )
    with pytest.raises(RuntimeError, match="Failed to fetch review threads"):
        pr_reviewer.fetch_review_threads("owner/repo", 1)


def test_fetch_review_threads_parse_failure_raises(mocker, make_cmd_result):
    mocker.patch("pr_reviewer.run_command", return_value=make_cmd_result("not-json"))
    with pytest.raises(RuntimeError, match="Failed to parse review threads response"):
        pr_reviewer.fetch_review_threads("owner/repo", 1)


def test_resolve_review_thread_subprocess_error_raises(mocker):
    mocker.patch("pr_reviewer.run_command", side_effect=SubprocessError("net error"))
    with pytest.raises(RuntimeError, match="Failed to resolve thread"):
        pr_reviewer.resolve_review_thread("thread-node-id")


def test_resolve_review_thread_nonzero_exit_raises(mocker, make_cmd_result):
    mocker.patch(
        "pr_reviewer.run_command",
        return_value=make_cmd_result("", returncode=1, stderr="permission denied"),
    )
    with pytest.raises(RuntimeError, match="Failed to resolve thread"):
        pr_reviewer.resolve_review_thread("thread-node-id")


def test_resolve_review_thread_success_returns_true(mocker, make_cmd_result):
    mocker.patch(
        "pr_reviewer.run_command", return_value=make_cmd_result('{"data": {}}')
    )
    assert pr_reviewer.resolve_review_thread("thread-node-id") is True


class TestFilterCheckRuns:
    def test_excludes_workflow_dispatch(self, mocker, make_cmd_result):
        runs: list[CheckRunData] = [
            {
                "id": 1,
                "name": "dispatch-job",
                "html_url": "https://github.com/owner/repo/actions/runs/999/jobs/1",
            }
        ]
        mocker.patch(
            "pr_reviewer.run_command",
            return_value=make_cmd_result("workflow_dispatch"),
        )
        result = pr_reviewer._filter_check_runs(runs, "owner/repo")
        assert result == []

    def test_keeps_latest_by_name(self, mocker, make_cmd_result):
        # GitHub Actions runs with the same name should be deduped (keep latest)
        runs: list[CheckRunData] = [
            {
                "id": 10,
                "name": "ci-build",
                "html_url": "https://github.com/owner/repo/actions/runs/111/jobs/10",
            },
            {
                "id": 20,
                "name": "ci-build",
                "html_url": "https://github.com/owner/repo/actions/runs/111/jobs/20",
            },
        ]
        mocker.patch(
            "pr_reviewer.run_command",
            return_value=make_cmd_result('"push"'),
        )
        result = pr_reviewer._filter_check_runs(runs, "owner/repo")
        assert len(result) == 1
        assert result[0].get("id") == 20

    def test_no_run_id_all_kept(self):
        # 外部 CI (run ID なし) は dedup 対象外 - 同名でも全て保持
        runs: list[CheckRunData] = [
            {"id": 10, "name": "ci-build"},
            {"id": 20, "name": "ci-build"},
        ]
        result = pr_reviewer._filter_check_runs(runs, "owner/repo")
        assert len(result) == 2

    def test_combined_dispatch_excluded_then_dedup(self, mocker, make_cmd_result):
        runs: list[CheckRunData] = [
            {
                "id": 1,
                "name": "job",
                "html_url": "https://github.com/owner/repo/actions/runs/999/jobs/1",
            },
            {
                "id": 2,
                "name": "job",
                "html_url": "https://github.com/owner/repo/actions/runs/888/jobs/2",
            },
            {
                "id": 3,
                "name": "job",
                "html_url": "https://github.com/owner/repo/actions/runs/888/jobs/3",
            },
        ]

        def mock_run(cmd, **kwargs):
            if "runs/999" in cmd[2]:
                return make_cmd_result("workflow_dispatch")
            return make_cmd_result("push")

        mocker.patch("pr_reviewer.run_command", side_effect=mock_run)
        result = pr_reviewer._filter_check_runs(runs, "owner/repo")

        # dispatch run (id=1) excluded; among id=2 and id=3 (same name, run 888), id=3 wins
        assert len(result) == 1
        assert result[0].get("id") == 3

    def test_no_run_id_keeps_run(self):
        runs: list[CheckRunData] = [
            {
                "id": 5,
                "name": "external-ci",
                "html_url": "https://jenkins.example.com/build/1",
            },
        ]
        result = pr_reviewer._filter_check_runs(runs, "owner/repo")
        assert len(result) == 1
        assert result[0].get("id") == 5
