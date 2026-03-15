"""Unit tests for ci_check helpers and are_all_ci_checks_successful."""

import json

import auto_fixer
import ci_check
from error_collector import ErrorCollector
from subprocess_helpers import SubprocessError
from type_defs import PRData


class TestCiFixHelpers:
    def test_extract_failing_ci_contexts_from_status_rollup(self):
        pr_data: PRData = {
            "check_runs": [
                {
                    "name": "unit-test",
                    "conclusion": "SUCCESS",
                    "detailsUrl": "https://example.com/success",
                },
                {
                    "name": "lint",
                    "conclusion": "FAILURE",
                    "detailsUrl": "https://github.com/org/repo/actions/runs/12345/job/999",
                },
                {
                    "context": "build/status",
                    "state": "FAILURE",
                    "targetUrl": "https://example.com/build",
                },
                {
                    "name": "startup-check",
                    "conclusion": "STARTUP_FAILURE",
                    "detailsUrl": "https://github.com/org/repo/actions/runs/67890/job/111",
                },
            ]
        }

        result = ci_check.extract_failing_ci_contexts(pr_data)
        assert result == [
            {
                "name": "lint",
                "status": "FAILURE",
                "details_url": "https://github.com/org/repo/actions/runs/12345/job/999",
                "run_id": "12345",
            },
            {
                "name": "build/status",
                "status": "FAILURE",
                "details_url": "https://example.com/build",
                "run_id": "",
            },
            {
                "name": "startup-check",
                "status": "STARTUP_FAILURE",
                "details_url": "https://github.com/org/repo/actions/runs/67890/job/111",
                "run_id": "67890",
            },
        ]

    def test_build_ci_fix_prompt_contains_check_details(self):
        prompt = auto_fixer.build_ci_fix_prompt(
            pr_number=12,
            title="Fix CI",
            failing_contexts=[
                {
                    "name": "lint",
                    "status": "FAILURE",
                    "details_url": "https://example.com/lint",
                }
            ],
        )

        assert "CI 失敗の先行修正フェーズ" in prompt
        assert (
            '<check name="lint" status="FAILURE" details_url="https://example.com/lint" />'
            in prompt
        )

    def test_extract_ci_error_digest_from_failed_log(self):
        log_text = """
test\tRun tests\tFAILED tests/test_imports.py::test_x - AssertionError: boom
test\tRun tests\tE       AssertionError: boom
test\tRun tests\ttests/test_imports.py:21: AssertionError
test\tRun tests\t1 failed, 74 passed in 0.67s
""".strip()

        digest = ci_check._extract_ci_error_digest_from_failed_log(log_text)
        assert digest == {
            "error_type": "AssertionError",
            "error_message": "boom",
            "failed_test": "tests/test_imports.py::test_x",
            "file_line": "tests/test_imports.py:21",
            "summary": "1 failed, 74 passed in 0.67s",
        }

    def test_build_ci_fix_prompt_includes_digest_and_failed_logs(self):
        prompt = auto_fixer.build_ci_fix_prompt(
            pr_number=12,
            title="Fix CI",
            failing_contexts=[
                {
                    "name": "lint",
                    "status": "FAILURE",
                    "details_url": "https://github.com/org/repo/actions/runs/12345/job/999",
                    "run_id": "12345",
                }
            ],
            ci_failure_materials=[
                {
                    "run_id": "12345",
                    "source": "gh run view --log-failed",
                    "truncated": True,
                    "excerpt_lines": ["line1", "line2"],
                    "digest": {
                        "error_type": "AssertionError",
                        "error_message": "boom",
                        "failed_test": "tests/test_imports.py::test_x",
                        "file_line": "tests/test_imports.py:21",
                        "summary": "1 failed, 74 passed in 0.67s",
                    },
                }
            ],
        )

        assert '<check name="lint" status="FAILURE"' in prompt
        assert 'run_id="12345"' in prompt
        assert '<ci_error_digest data-only="true">' in prompt
        assert '<error type="AssertionError">boom</error>' in prompt
        assert '<ci_failure_logs data-only="true">' in prompt
        assert (
            "<test_result_summary>1 failed, 74 passed in 0.67s</test_result_summary>"
            in prompt
        )
        assert "<summary>" not in prompt
        assert (
            '<failed_run run_id="12345" source="gh run view --log-failed" truncated="true">'
            in prompt
        )
        assert "line1" in prompt

    def test_collect_ci_failure_materials_fetches_unique_run_logs(
        self, mocker, make_cmd_result
    ):
        failing_contexts = [
            {
                "name": "lint",
                "status": "FAILURE",
                "details_url": "u1",
                "run_id": "12345",
            },
            {
                "name": "tests",
                "status": "FAILURE",
                "details_url": "u2",
                "run_id": "12345",
            },
        ]
        log_text = "\n".join(
            [
                "test\tRun tests\tFAILED tests/test_imports.py::test_x - AssertionError: boom",
                "test\tRun tests\tE       AssertionError: boom",
                "test\tRun tests\ttests/test_imports.py:21: AssertionError",
                "test\tRun tests\t1 failed, 74 passed in 0.67s",
            ]
        )

        mock_run = mocker.patch(
            "ci_check.run_command",
            return_value=make_cmd_result(log_text),
        )
        materials = auto_fixer.collect_ci_failure_materials(
            "owner/repo",
            failing_contexts,
            max_lines=120,
        )

        assert len(materials) == 1
        assert materials[0]["run_id"] == "12345"
        assert materials[0]["digest"]["failed_test"] == "tests/test_imports.py::test_x"
        mock_run.assert_called_once_with(
            ["gh", "run", "view", "12345", "--repo", "owner/repo", "--log-failed"],
            check=False,
            timeout=60,
        )


class TestAreAllCiChecksSuccessful:
    """Tests for _are_all_ci_checks_successful with ci_empty_as_success / ci_empty_grace_minutes."""

    def test_empty_checks_ci_empty_as_success_false_returns_false(
        self, mocker, make_cmd_result
    ):
        mocker.patch(
            "ci_check.run_command",
            side_effect=[
                make_cmd_result('"abc123"'),  # head SHA
                make_cmd_result("[]"),  # check-runs (empty)
            ],
        )
        mocker.patch(
            "pr_reviewer.run_command",
            return_value=make_cmd_result("{}"),
        )
        result = ci_check.are_all_ci_checks_successful(
            "owner/repo", 1, ci_empty_as_success=False
        )
        assert result is False

    def test_empty_checks_commit_old_treats_as_success(self, mocker, make_cmd_result):
        from datetime import datetime, timezone, timedelta

        old_date = (datetime.now(timezone.utc) - timedelta(minutes=10)).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
        mocker.patch(
            "ci_check.run_command",
            side_effect=[
                make_cmd_result('"abc123"'),  # head SHA
                make_cmd_result("[]"),  # check-runs (empty)
                make_cmd_result(f'"{old_date}"'),  # commit date
            ],
        )
        mocker.patch(
            "pr_reviewer.run_command",
            return_value=make_cmd_result("{}"),
        )
        result = ci_check.are_all_ci_checks_successful(
            "owner/repo",
            1,
            ci_empty_as_success=True,
            ci_empty_grace_minutes=5,
        )
        assert result is True

    def test_empty_checks_commit_recent_returns_false(self, mocker, make_cmd_result):
        from datetime import datetime, timezone, timedelta

        recent_date = (datetime.now(timezone.utc) - timedelta(minutes=2)).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
        mocker.patch(
            "ci_check.run_command",
            side_effect=[
                make_cmd_result('"abc123"'),  # head SHA
                make_cmd_result("[]"),  # check-runs (empty)
                make_cmd_result(f'"{recent_date}"'),  # commit date
            ],
        )
        mocker.patch(
            "pr_reviewer.run_command",
            return_value=make_cmd_result("{}"),
        )
        result = ci_check.are_all_ci_checks_successful(
            "owner/repo",
            1,
            ci_empty_as_success=True,
            ci_empty_grace_minutes=5,
        )
        assert result is None  # grace period: returns None so callers skip caching

    def test_non_empty_checks_all_success_returns_true(self, mocker, make_cmd_result):
        mocker.patch(
            "ci_check.run_command",
            side_effect=[
                make_cmd_result('"abc123"'),  # head SHA
                make_cmd_result(
                    '[{"check_runs": [{"name": "build", "status": "completed", "conclusion": "success"}]}]'
                ),
            ],
        )
        mocker.patch(
            "pr_reviewer.run_command",
            return_value=make_cmd_result("{}"),
        )
        result = ci_check.are_all_ci_checks_successful("owner/repo", 1)
        assert result is True

    def test_check_runs_403_no_classic_old_commit_returns_true(
        self, mocker, make_cmd_result
    ):
        """check-runs 403 + classic なし + 古いコミット → ci_empty_as_success=True で True"""
        from datetime import datetime, timezone, timedelta

        old_date = (datetime.now(timezone.utc) - timedelta(minutes=10)).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
        mocker.patch(
            "ci_check.run_command",
            side_effect=[
                make_cmd_result('"abc123"'),  # head SHA
                make_cmd_result("", returncode=1, stderr="HTTP 403"),  # check-runs 403
                make_cmd_result(f'"{old_date}"'),  # commit date
            ],
        )
        mocker.patch(
            "pr_reviewer.run_command",
            return_value=make_cmd_result("{}"),
        )
        result = ci_check.are_all_ci_checks_successful(
            "owner/repo",
            1,
            ci_empty_as_success=True,
            ci_empty_grace_minutes=5,
        )
        assert result is True

    def test_check_runs_403_no_classic_recent_commit_returns_none(
        self, mocker, make_cmd_result
    ):
        """check-runs 403 + classic なし + 新しいコミット → グレースピリオド内で None"""
        from datetime import datetime, timezone, timedelta

        recent_date = (datetime.now(timezone.utc) - timedelta(minutes=2)).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
        mocker.patch(
            "ci_check.run_command",
            side_effect=[
                make_cmd_result('"abc123"'),  # head SHA
                make_cmd_result("", returncode=1, stderr="HTTP 403"),  # check-runs 403
                make_cmd_result(f'"{recent_date}"'),  # commit date
            ],
        )
        mocker.patch(
            "pr_reviewer.run_command",
            return_value=make_cmd_result("{}"),
        )
        result = ci_check.are_all_ci_checks_successful(
            "owner/repo",
            1,
            ci_empty_as_success=True,
            ci_empty_grace_minutes=5,
        )
        assert result is None

    def test_check_runs_403_classic_success_returns_true(self, mocker, make_cmd_result):
        """check-runs 403 + classic SUCCESS → classic で True と判定"""
        classic_response = '{"state": "success", "statuses": [{"context": "ci/build", "state": "success", "target_url": "https://ci.example.com/build/1"}]}'
        mocker.patch(
            "ci_check.run_command",
            side_effect=[
                make_cmd_result('"abc123"'),  # head SHA
                make_cmd_result("", returncode=1, stderr="HTTP 403"),  # check-runs 403
            ],
        )
        mocker.patch(
            "pr_reviewer.run_command",
            return_value=make_cmd_result(classic_response),
        )
        result = ci_check.are_all_ci_checks_successful(
            "owner/repo",
            1,
            ci_empty_as_success=True,
        )
        assert result is True

    def test_check_runs_403_ci_empty_as_success_false_returns_false(
        self, mocker, make_cmd_result
    ):
        """check-runs 403 + ci_empty_as_success=False → 空を失敗扱いで False"""
        mocker.patch(
            "ci_check.run_command",
            side_effect=[
                make_cmd_result('"abc123"'),  # head SHA
                make_cmd_result("", returncode=1, stderr="HTTP 403"),  # check-runs 403
            ],
        )
        mocker.patch(
            "pr_reviewer.run_command",
            return_value=make_cmd_result("{}"),
        )
        result = ci_check.are_all_ci_checks_successful(
            "owner/repo",
            1,
            ci_empty_as_success=False,
        )
        assert result is False

    def test_check_runs_403_info_log_includes_repo_name(
        self, capsys, mocker, make_cmd_result
    ):
        """CI 未設定の正常系ログに owner/repo が含まれることを確認。"""
        mocker.patch(
            "ci_check.run_command",
            side_effect=[
                make_cmd_result('"abc123"'),  # head SHA
                make_cmd_result("", returncode=1, stderr="HTTP 403"),  # check-runs 403
            ],
        )
        mocker.patch(
            "pr_reviewer.run_command",
            return_value=make_cmd_result("{}"),
        )
        result = ci_check.are_all_ci_checks_successful(
            "owner/repo",
            1,
            ci_empty_as_success=False,
        )
        assert result is False
        out = capsys.readouterr().out
        assert "owner/repo PR #1" in out
        assert "no CI configured" in out


class TestErrorCollectorIntegration:
    def test_collect_ci_failure_materials_subprocess_error_adds_pr_error(self, mocker):
        ec = ErrorCollector()
        failing_contexts = [{"name": "lint", "status": "FAILURE", "run_id": "12345"}]
        mocker.patch(
            "ci_check.run_command", side_effect=SubprocessError("network error")
        )
        materials = ci_check.collect_ci_failure_materials(
            "owner/repo",
            failing_contexts,
            max_lines=120,
            error_collector=ec,
            pr_number=5,
        )
        assert materials == []
        assert ec.has_errors
        assert ec._errors[0].scope == "owner/repo#5"
        assert "failed to fetch CI logs" in ec._errors[0].message

    def test_collect_ci_failure_materials_nonzero_exit_adds_repo_error(
        self, mocker, make_cmd_result
    ):
        ec = ErrorCollector()
        failing_contexts = [{"name": "lint", "status": "FAILURE", "run_id": "12345"}]
        mocker.patch(
            "ci_check.run_command",
            return_value=make_cmd_result("", returncode=1, stderr="not found"),
        )
        materials = ci_check.collect_ci_failure_materials(
            "owner/repo",
            failing_contexts,
            max_lines=120,
            error_collector=ec,
        )
        assert materials == []
        assert ec.has_errors
        assert ec._errors[0].scope == "owner/repo"
        assert "failed to fetch failed CI logs" in ec._errors[0].message

    def test_are_all_ci_checks_successful_head_sha_failure_adds_pr_error(
        self, mocker, make_cmd_result
    ):
        ec = ErrorCollector()
        mocker.patch(
            "ci_check.run_command",
            side_effect=[
                make_cmd_result("", returncode=1, stderr="error"),  # head SHA fails
            ],
        )
        mocker.patch(
            "pr_reviewer.run_command",
            return_value=make_cmd_result("{}"),
        )
        result = ci_check.are_all_ci_checks_successful(
            "owner/repo", 3, error_collector=ec
        )
        assert result is None  # head SHA 取得失敗は None を返す
        assert ec.has_errors
        assert ec._errors[0].scope == "owner/repo#3"

    def test_are_all_ci_checks_successful_check_runs_403_no_error(
        self, mocker, make_cmd_result
    ):
        # CI が設定されていないリポジトリでは 403 が返るが、これは想定内の挙動のため
        # error_collector にエラーを追加せず、warning ログのみ出力する。
        ec = ErrorCollector()
        mocker.patch(
            "ci_check.run_command",
            side_effect=[
                make_cmd_result('"abc123"'),  # head SHA
                make_cmd_result("", returncode=1, stderr="HTTP 403"),  # check-runs 403
            ],
        )
        mocker.patch(
            "pr_reviewer.run_command",
            return_value=make_cmd_result("{}"),
        )
        result = ci_check.are_all_ci_checks_successful(
            "owner/repo",
            3,
            ci_empty_as_success=False,
            error_collector=ec,
        )
        assert result is False
        assert not ec.has_errors  # 403 はエラーとして記録しない

    def test_are_all_ci_checks_successful_filters_workflow_dispatch(
        self, mocker, make_cmd_result
    ):
        """workflow_dispatch の failure check run はフィルタされ、残りの成功 run で True を返す"""
        check_runs_response = json.dumps(
            [
                {
                    "check_runs": [
                        {
                            "id": 1,
                            "name": "dispatch-job",
                            "status": "completed",
                            "conclusion": "failure",
                            "html_url": "https://github.com/owner/repo/actions/runs/999/jobs/1",
                        },
                        {
                            "id": 2,
                            "name": "ci-build",
                            "status": "completed",
                            "conclusion": "success",
                            "html_url": "https://github.com/owner/repo/actions/runs/888/jobs/2",
                        },
                    ]
                }
            ]
        )

        mocker.patch(
            "ci_check.run_command",
            side_effect=[
                make_cmd_result('"abc123"'),  # head SHA
                make_cmd_result(check_runs_response),  # check-runs
            ],
        )

        def pr_run_command(cmd, **kwargs):
            url = cmd[2] if len(cmd) > 2 else ""
            if "actions/runs/999" in url:
                return make_cmd_result("workflow_dispatch")
            if "actions/runs/888" in url:
                return make_cmd_result("push")
            # classic statuses
            return make_cmd_result('{"statuses": []}')

        mocker.patch("pr_reviewer.run_command", side_effect=pr_run_command)
        result = ci_check.are_all_ci_checks_successful("owner/repo", 1)

        assert result is True
