"""Unit tests for ci_check helpers and are_all_ci_checks_successful."""

from unittest.mock import Mock, patch


import auto_fixer
import ci_check
from error_collector import ErrorCollector
from subprocess_helpers import SubprocessError


class TestCiFixHelpers:
    def test_extract_failing_ci_contexts_from_status_rollup(self):
        pr_data = {
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

    def test_collect_ci_failure_materials_fetches_unique_run_logs(self):
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

        with patch(
            "ci_check.run_command",
            return_value=Mock(returncode=0, stdout=log_text, stderr=""),
        ) as mock_run:
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

    def test_empty_checks_ci_empty_as_success_false_returns_false(self):
        with (
            patch("ci_check.run_command") as mock_run,
            patch(
                "pr_reviewer.run_command",
                return_value=Mock(returncode=0, stdout="{}", stderr=""),
            ),
        ):
            mock_run.side_effect = [
                Mock(returncode=0, stdout='"abc123"', stderr=""),  # head SHA
                Mock(returncode=0, stdout="[]", stderr=""),  # check-runs (empty)
            ]
            result = ci_check.are_all_ci_checks_successful(
                "owner/repo", 1, ci_empty_as_success=False
            )
        assert result is False
        assert mock_run.call_count == 2

    def test_empty_checks_commit_old_treats_as_success(self):
        from datetime import datetime, timezone, timedelta

        old_date = (datetime.now(timezone.utc) - timedelta(minutes=10)).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
        with (
            patch("ci_check.run_command") as mock_run,
            patch(
                "pr_reviewer.run_command",
                return_value=Mock(returncode=0, stdout="{}", stderr=""),
            ),
        ):
            mock_run.side_effect = [
                Mock(returncode=0, stdout='"abc123"', stderr=""),  # head SHA
                Mock(returncode=0, stdout="[]", stderr=""),  # check-runs (empty)
                Mock(returncode=0, stdout=f'"{old_date}"', stderr=""),  # commit date
            ]
            result = ci_check.are_all_ci_checks_successful(
                "owner/repo",
                1,
                ci_empty_as_success=True,
                ci_empty_grace_minutes=5,
            )
        assert result is True
        assert mock_run.call_count == 3

    def test_empty_checks_commit_recent_returns_false(self):
        from datetime import datetime, timezone, timedelta

        recent_date = (datetime.now(timezone.utc) - timedelta(minutes=2)).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
        with (
            patch("ci_check.run_command") as mock_run,
            patch(
                "pr_reviewer.run_command",
                return_value=Mock(returncode=0, stdout="{}", stderr=""),
            ),
        ):
            mock_run.side_effect = [
                Mock(returncode=0, stdout='"abc123"', stderr=""),  # head SHA
                Mock(returncode=0, stdout="[]", stderr=""),  # check-runs (empty)
                Mock(returncode=0, stdout=f'"{recent_date}"', stderr=""),  # commit date
            ]
            result = ci_check.are_all_ci_checks_successful(
                "owner/repo",
                1,
                ci_empty_as_success=True,
                ci_empty_grace_minutes=5,
            )
        assert result is None  # grace period: returns None so callers skip caching
        assert mock_run.call_count == 3

    def test_non_empty_checks_all_success_returns_true(self):
        with (
            patch("ci_check.run_command") as mock_run,
            patch(
                "pr_reviewer.run_command",
                return_value=Mock(returncode=0, stdout="{}", stderr=""),
            ),
        ):
            mock_run.side_effect = [
                Mock(returncode=0, stdout='"abc123"', stderr=""),  # head SHA
                Mock(
                    returncode=0,
                    stdout='[{"check_runs": [{"name": "build", "status": "completed", "conclusion": "success"}]}]',
                    stderr="",
                ),  # check-runs (non-empty, all success)
            ]
            result = ci_check.are_all_ci_checks_successful("owner/repo", 1)
        assert result is True
        assert mock_run.call_count == 2

    def test_check_runs_403_no_classic_old_commit_returns_true(self):
        """check-runs 403 + classic なし + 古いコミット → ci_empty_as_success=True で True"""
        from datetime import datetime, timezone, timedelta

        old_date = (datetime.now(timezone.utc) - timedelta(minutes=10)).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
        with (
            patch("ci_check.run_command") as mock_run,
            patch(
                "pr_reviewer.run_command",
                return_value=Mock(returncode=0, stdout="{}", stderr=""),
            ),
        ):
            mock_run.side_effect = [
                Mock(returncode=0, stdout='"abc123"', stderr=""),  # head SHA
                Mock(returncode=1, stdout="", stderr="HTTP 403"),  # check-runs 403
                Mock(returncode=0, stdout=f'"{old_date}"', stderr=""),  # commit date
            ]
            result = ci_check.are_all_ci_checks_successful(
                "owner/repo",
                1,
                ci_empty_as_success=True,
                ci_empty_grace_minutes=5,
            )
        assert result is True

    def test_check_runs_403_no_classic_recent_commit_returns_none(self):
        """check-runs 403 + classic なし + 新しいコミット → グレースピリオド内で None"""
        from datetime import datetime, timezone, timedelta

        recent_date = (datetime.now(timezone.utc) - timedelta(minutes=2)).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
        with (
            patch("ci_check.run_command") as mock_run,
            patch(
                "pr_reviewer.run_command",
                return_value=Mock(returncode=0, stdout="{}", stderr=""),
            ),
        ):
            mock_run.side_effect = [
                Mock(returncode=0, stdout='"abc123"', stderr=""),  # head SHA
                Mock(returncode=1, stdout="", stderr="HTTP 403"),  # check-runs 403
                Mock(returncode=0, stdout=f'"{recent_date}"', stderr=""),  # commit date
            ]
            result = ci_check.are_all_ci_checks_successful(
                "owner/repo",
                1,
                ci_empty_as_success=True,
                ci_empty_grace_minutes=5,
            )
        assert result is None

    def test_check_runs_403_classic_success_returns_true(self):
        """check-runs 403 + classic SUCCESS → classic で True と判定"""
        classic_response = '{"state": "success", "statuses": [{"context": "ci/build", "state": "success", "target_url": "https://ci.example.com/build/1"}]}'
        with (
            patch("ci_check.run_command") as mock_run,
            patch(
                "pr_reviewer.run_command",
                return_value=Mock(returncode=0, stdout=classic_response, stderr=""),
            ),
        ):
            mock_run.side_effect = [
                Mock(returncode=0, stdout='"abc123"', stderr=""),  # head SHA
                Mock(returncode=1, stdout="", stderr="HTTP 403"),  # check-runs 403
            ]
            result = ci_check.are_all_ci_checks_successful(
                "owner/repo",
                1,
                ci_empty_as_success=True,
            )
        assert result is True

    def test_check_runs_403_ci_empty_as_success_false_returns_false(self):
        """check-runs 403 + ci_empty_as_success=False → 空を失敗扱いで False"""
        with (
            patch("ci_check.run_command") as mock_run,
            patch(
                "pr_reviewer.run_command",
                return_value=Mock(returncode=0, stdout="{}", stderr=""),
            ),
        ):
            mock_run.side_effect = [
                Mock(returncode=0, stdout='"abc123"', stderr=""),  # head SHA
                Mock(returncode=1, stdout="", stderr="HTTP 403"),  # check-runs 403
            ]
            result = ci_check.are_all_ci_checks_successful(
                "owner/repo",
                1,
                ci_empty_as_success=False,
            )
        assert result is False


class TestErrorCollectorIntegration:
    def test_collect_ci_failure_materials_subprocess_error_adds_pr_error(self):
        ec = ErrorCollector()
        failing_contexts = [{"name": "lint", "status": "FAILURE", "run_id": "12345"}]
        with patch(
            "ci_check.run_command", side_effect=SubprocessError("network error")
        ):
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

    def test_collect_ci_failure_materials_nonzero_exit_adds_repo_error(self):
        ec = ErrorCollector()
        failing_contexts = [{"name": "lint", "status": "FAILURE", "run_id": "12345"}]
        with patch(
            "ci_check.run_command",
            return_value=Mock(returncode=1, stdout="", stderr="not found"),
        ):
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

    def test_are_all_ci_checks_successful_head_sha_failure_adds_pr_error(self):
        ec = ErrorCollector()
        with (
            patch("ci_check.run_command") as mock_run,
            patch(
                "pr_reviewer.run_command",
                return_value=Mock(returncode=0, stdout="{}", stderr=""),
            ),
        ):
            mock_run.side_effect = [
                Mock(returncode=1, stdout="", stderr="error"),  # head SHA fails
            ]
            result = ci_check.are_all_ci_checks_successful(
                "owner/repo", 3, error_collector=ec
            )
        assert result is None  # head SHA 取得失敗は None を返す
        assert ec.has_errors
        assert ec._errors[0].scope == "owner/repo#3"

    def test_are_all_ci_checks_successful_check_runs_403_adds_pr_error(self):
        ec = ErrorCollector()
        with (
            patch("ci_check.run_command") as mock_run,
            patch(
                "pr_reviewer.run_command",
                return_value=Mock(returncode=0, stdout="{}", stderr=""),
            ),
        ):
            mock_run.side_effect = [
                Mock(returncode=0, stdout='"abc123"', stderr=""),  # head SHA
                Mock(returncode=1, stdout="", stderr="HTTP 403"),  # check-runs 403
            ]
            result = ci_check.are_all_ci_checks_successful(
                "owner/repo",
                3,
                ci_empty_as_success=False,
                error_collector=ec,
            )
        assert result is False
        assert ec.has_errors
        assert ec._errors[0].scope == "owner/repo#3"
