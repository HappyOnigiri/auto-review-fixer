"""Unit tests for auto_fixer module."""

import sys
from pathlib import Path
from unittest.mock import ANY

import pytest

import auto_fixer
from claude_limit import ClaudeCommandFailedError, ClaudeUsageLimitError
from error_collector import ErrorCollector
from state_manager import StateComment, StateEntry

# Rate limit body constants (also used in TestProcessRepo tests)
_RATE_LIMIT_BODY = """
> [!WARNING]
> ## Rate limit exceeded
>
> `@HappyOnigiri` has exceeded the limit for the number of commits that can be reviewed per hour. Please wait **5 minutes and 11 seconds** before requesting another review.
""".strip()

_REVIEW_FAILED_BODY = """
> [!CAUTION]
> ## Review failed
>
> The head commit changed during the review from 8c95504f7bdc7b6f178d693ad16194afa00240bd to 769422c80b767b53c7cd900db05a71bc8713b9a8.
""".strip()

_REVIEW_SKIPPED_DRAFT_BODY = """
> [!IMPORTANT]
> ## Review skipped
>
> Draft detected.
>
> Please check the settings in the CodeRabbit UI or the `.coderabbit.yaml` file in this repository. To trigger a single review, invoke the `@coderabbitai review` command.
""".strip()


def make_state_comment(*processed_ids: str) -> StateComment:
    return StateComment(
        github_comment_id=None,
        body="",
        entries=[],
        processed_ids=set(processed_ids),
        archived_ids=set(),
    )


class TestMain:
    def test_load_config_error_exits_with_error(self, mocker):
        mocker.patch.object(sys, "argv", ["auto_fixer.py"])
        mocker.patch("auto_fixer.load_dotenv")
        mocker.patch("auto_fixer.load_config", side_effect=SystemExit(1))
        with pytest.raises(SystemExit) as exc_info:
            auto_fixer.main()
        assert exc_info.value.code == 1

    def test_main_passes_loaded_config_to_process_repo(self, mocker):
        cfg = {
            "models": {"summarize": "haiku", "fix": "sonnet"},
            "ci_log_max_lines": 120,
            "repositories": [
                {"repo": "owner/repo", "user_name": None, "user_email": None}
            ],
        }
        mocker.patch.object(sys, "argv", ["auto_fixer.py", "--config", "custom.yaml"])
        mocker.patch("auto_fixer.load_dotenv")
        mock_load_config = mocker.patch("auto_fixer.load_config", return_value=cfg)
        mock_process_repo = mocker.patch("auto_fixer.process_repo", return_value=[])
        auto_fixer.main()

        mock_load_config.assert_called_once_with("custom.yaml")
        mock_process_repo.assert_called_once_with(
            {"repo": "owner/repo", "user_name": None, "user_email": None},
            dry_run=False,
            silent=False,
            summarize_only=False,
            config=cfg,
            global_modified_prs=set(),
            global_committed_prs=set(),
            global_claude_prs=set(),
            global_coderabbit_resumed_prs=set(),
            auto_resume_run_state=ANY,
            global_backfilled_count=[0],
            error_collector=ANY,
        )
        assert mock_process_repo.call_args.kwargs["auto_resume_run_state"] == {
            "posted": 0,
            "max_per_run": 1,
        }
        assert (
            mock_process_repo.call_args.kwargs["global_coderabbit_resumed_prs"] == set()
        )

    def test_main_prints_resumed_prs_before_commit_list(self, mocker, capsys):
        cfg = {
            "models": {"summarize": "haiku", "fix": "sonnet"},
            "ci_log_max_lines": 120,
            "repositories": [
                {"repo": "owner/repo", "user_name": None, "user_email": None}
            ],
        }

        def _process_repo_side_effect(*_args, **kwargs):
            kwargs["global_coderabbit_resumed_prs"].add(("owner/repo", 123))
            return [("owner/repo", 123, "abc123 test commit")]

        mocker.patch.object(sys, "argv", ["auto_fixer.py", "--config", "config.yaml"])
        mocker.patch("auto_fixer.load_dotenv")
        mocker.patch("auto_fixer.load_config", return_value=cfg)
        mocker.patch("auto_fixer.process_repo", side_effect=_process_repo_side_effect)
        auto_fixer.main()

        out = capsys.readouterr().out
        assert "CodeRabbit を再トリガした PR 一覧:" in out
        assert "  - owner/repo PR #123" in out
        assert "コミットを追加した PR 一覧:" in out
        assert out.index("CodeRabbit を再トリガした PR 一覧:") < out.index(
            "コミットを追加した PR 一覧:"
        )

    def test_main_skips_resumed_prs_section_when_empty(self, mocker, capsys):
        cfg = {
            "models": {"summarize": "haiku", "fix": "sonnet"},
            "ci_log_max_lines": 120,
            "repositories": [
                {"repo": "owner/repo", "user_name": None, "user_email": None}
            ],
        }
        mocker.patch.object(sys, "argv", ["auto_fixer.py", "--config", "config.yaml"])
        mocker.patch("auto_fixer.load_dotenv")
        mocker.patch("auto_fixer.load_config", return_value=cfg)
        mocker.patch("auto_fixer.process_repo", return_value=[])
        auto_fixer.main()

        out = capsys.readouterr().out
        assert "CodeRabbit を再トリガした PR 一覧:" not in out

    def test_usage_limit_exits_nonzero_immediately(self, mocker, capsys):
        cfg = {
            "models": {"summarize": "haiku", "fix": "sonnet"},
            "ci_log_max_lines": 120,
            "repositories": [
                {"repo": "owner/repo", "user_name": None, "user_email": None}
            ],
        }
        mocker.patch.object(sys, "argv", ["auto_fixer.py", "--config", "config.yaml"])
        mocker.patch("auto_fixer.load_dotenv")
        mocker.patch("auto_fixer.load_config", return_value=cfg)
        mocker.patch(
            "auto_fixer.process_repo",
            side_effect=ClaudeUsageLimitError(
                phase="review-fix",
                returncode=1,
                stdout="You've hit your limit",
                stderr="",
            ),
        )
        with pytest.raises(SystemExit) as exc_info:
            auto_fixer.main()

        assert exc_info.value.code == 1
        err = capsys.readouterr().err
        assert "Failing CI immediately" in err

    def test_claude_nonzero_exits_nonzero_immediately(self, mocker, capsys):
        cfg = {
            "models": {"summarize": "haiku", "fix": "sonnet"},
            "ci_log_max_lines": 120,
            "repositories": [
                {"repo": "owner/repo", "user_name": None, "user_email": None}
            ],
        }
        mocker.patch.object(sys, "argv", ["auto_fixer.py", "--config", "config.yaml"])
        mocker.patch("auto_fixer.load_dotenv")
        mocker.patch("auto_fixer.load_config", return_value=cfg)
        mocker.patch(
            "auto_fixer.process_repo",
            side_effect=ClaudeCommandFailedError(
                phase="review-fix",
                returncode=1,
                stdout="API Error",
                stderr="bad headers",
            ),
        )
        with pytest.raises(SystemExit) as exc_info:
            auto_fixer.main()

        assert exc_info.value.code == 1
        err = capsys.readouterr().err
        assert "Failing CI immediately" in err
        assert "stdout: API Error" in err
        assert "stderr: bad headers" in err

    def test_empty_repos_exits_nonzero(self, mocker):
        cfg = {
            "models": {"summarize": "haiku", "fix": "sonnet"},
            "ci_log_max_lines": 120,
            "repositories": [],
        }
        mocker.patch.object(sys, "argv", ["auto_fixer.py", "--config", "config.yaml"])
        mocker.patch("auto_fixer.load_dotenv")
        mocker.patch("auto_fixer.load_config", return_value=cfg)
        mocker.patch("auto_fixer.expand_repositories", return_value=[])
        with pytest.raises(SystemExit) as exc_info:
            auto_fixer.main()

        assert exc_info.value.code == 1

    def test_repo_error_exits_nonzero_with_summary(self, mocker, capsys):
        cfg = {
            "models": {"summarize": "haiku", "fix": "sonnet"},
            "ci_log_max_lines": 120,
            "repositories": [
                {"repo": "owner/repo", "user_name": None, "user_email": None}
            ],
        }
        mocker.patch.object(sys, "argv", ["auto_fixer.py", "--config", "config.yaml"])
        mocker.patch("auto_fixer.load_dotenv")
        mocker.patch("auto_fixer.load_config", return_value=cfg)
        mocker.patch(
            "auto_fixer.process_repo", side_effect=RuntimeError("connection error")
        )
        with pytest.raises(SystemExit) as exc_info:
            auto_fixer.main()

        assert exc_info.value.code == 1
        out = capsys.readouterr().out
        assert "Error summary" in out


class TestProcessRepo:
    """Thin orchestration tests for process_repo(). All external deps mocked."""

    def test_empty_prs_returns_early(self, mocker, capsys):
        """No open PRs -> early return, no git/claude calls."""
        mocker.patch("auto_fixer.fetch_open_prs", return_value=[])
        mock_run = mocker.patch("auto_fixer.subprocess.run")
        mock_popen = mocker.patch("auto_fixer.subprocess.Popen")
        auto_fixer.process_repo({"repo": "owner/repo"})
        out = capsys.readouterr().out
        assert "No open PRs found" in out
        mock_run.assert_not_called()
        mock_popen.assert_not_called()

    def test_auto_merge_enabled_backfills_merged_labels_even_without_open_prs(
        self, mocker
    ):
        cfg = {
            "models": {"summarize": "haiku", "fix": "sonnet"},
            "ci_log_max_lines": 120,
            "auto_merge": True,
            "process_draft_prs": False,
            "repositories": [
                {"repo": "owner/repo", "user_name": None, "user_email": None}
            ],
        }
        mocker.patch("auto_fixer.fetch_open_prs", return_value=[])
        mock_backfill = mocker.patch("auto_fixer.backfill_merged_labels")
        auto_fixer.process_repo({"repo": "owner/repo"}, config=cfg)
        mock_backfill.assert_called_once_with(
            "owner/repo",
            limit=100,
            enabled_pr_label_keys={
                "running",
                "done",
                "merged",
                "auto_merge_requested",
                "ci_pending",
            },
            error_collector=None,
        )

    def test_draft_pr_is_skipped_by_default(self, mocker):
        prs = [{"number": 1, "title": "Draft PR", "isDraft": True}]
        mocker.patch("auto_fixer.fetch_open_prs", return_value=prs)
        mock_fetch_pr_details = mocker.patch("auto_fixer.fetch_pr_details")
        auto_fixer.process_repo({"repo": "owner/repo"})

        mock_fetch_pr_details.assert_not_called()

    def test_draft_pr_is_processed_when_enabled(self, mocker):
        prs = [{"number": 1, "title": "Draft PR", "isDraft": True}]
        pr_data = {
            "headRefName": "feature",
            "baseRefName": "main",
            "title": "Draft PR",
            "reviews": [],
            "comments": [],
        }
        cfg = {
            "models": {"summarize": "haiku", "fix": "sonnet"},
            "ci_log_max_lines": 120,
            "process_draft_prs": True,
            "repositories": [
                {"repo": "owner/repo", "user_name": None, "user_email": None}
            ],
        }
        mocker.patch("auto_fixer.fetch_open_prs", return_value=prs)
        mock_fetch_pr_details = mocker.patch(
            "auto_fixer.fetch_pr_details", return_value=pr_data
        )
        mocker.patch("auto_fixer.fetch_pr_review_comments", return_value=[])
        mocker.patch("auto_fixer.fetch_review_threads", return_value={})
        mocker.patch("auto_fixer.fetch_issue_comments", return_value=[])
        mocker.patch("auto_fixer.get_branch_compare_status", return_value=("ahead", 0))
        mocker.patch("auto_fixer.load_state_comment", return_value=make_state_comment())
        mocker.patch(
            "auto_fixer.update_done_label_if_completed",
            return_value=(False, False),
        )
        auto_fixer.process_repo(
            {"repo": "owner/repo"},
            config=cfg,
            global_modified_prs=set(),
            global_committed_prs=set(),
            global_claude_prs=set(),
        )

        mock_fetch_pr_details.assert_called_once_with("owner/repo", 1)

    def test_dry_run_no_external_commands(self, mocker, tmp_path, capsys):
        """dry_run=True -> no Claude API calls, no git clone."""
        prs = [{"number": 1, "title": "Test"}]
        pr_data = {
            "headRefName": "feature",
            "baseRefName": "main",
            "title": "Test",
            "reviews": [
                {"id": "r1", "body": "fix", "author": {"login": "coderabbitai[bot]"}}
            ],
        }
        mocker.patch("auto_fixer.fetch_open_prs", return_value=prs)
        mocker.patch("auto_fixer.fetch_pr_details", return_value=pr_data)
        mocker.patch("auto_fixer.fetch_pr_review_comments", return_value=[])
        mocker.patch("auto_fixer.fetch_review_threads", return_value={})
        mocker.patch("auto_fixer.fetch_issue_comments", return_value=[])
        mocker.patch("auto_fixer.get_branch_compare_status", return_value=("ahead", 0))
        mocker.patch("auto_fixer.load_state_comment", return_value=make_state_comment())
        mocker.patch("auto_fixer.prepare_repository", return_value=tmp_path)
        mock_summarize = mocker.patch("auto_fixer.summarize_reviews")
        mock_popen = mocker.patch("auto_fixer.subprocess.Popen")
        mocker.patch("auto_fixer.upsert_state_comment")
        mocker.patch("auto_fixer.resolve_review_thread")
        auto_fixer.process_repo({"repo": "owner/repo"}, dry_run=True)
        mock_summarize.assert_not_called()
        mock_popen.assert_not_called()
        out = capsys.readouterr().out
        assert "[DRY RUN]" in out
        assert "follow only the top-level <instructions> section" in out

    def test_processes_multiple_targets_in_single_claude_run(
        self, mocker, make_cmd_result, make_process_mock, tmp_path
    ):
        """複数指摘でも Claude 実行は1回で、既読化は全対象に行う。"""
        prs = [{"number": 1, "title": "Test"}]
        pr_data = {
            "headRefName": "feature",
            "baseRefName": "main",
            "title": "Test",
            "reviews": [
                {
                    "id": "r1",
                    "body": "fix review",
                    "author": {"login": "coderabbitai[bot]"},
                }
            ],
        }
        review_comments = [
            {
                "id": 10,
                "path": "src/foo.py",
                "line": 12,
                "body": "fix comment",
                "user": {"login": "coderabbitai[bot]"},
            }
        ]
        thread_map = {10: "thread-node-id"}

        def _run_side_effect(cmd, **kwargs):
            if cmd == ["git", "rev-parse", "HEAD"]:
                return make_cmd_result("abc123\n")
            if (
                cmd[:4] == ["git", "log", "--oneline", "--first-parent"]
                and cmd[4] == "abc123..HEAD"
            ) or (cmd[:3] == ["git", "log", "--oneline"] and cmd[3] == "abc123..HEAD"):
                return make_cmd_result("deadbee fix\n")
            if cmd == ["git", "status", "--porcelain"]:
                return make_cmd_result("")
            if cmd[:3] == ["git", "push", "origin"]:
                return make_cmd_result("")
            if cmd == ["git", "log", "origin/feature..HEAD", "--oneline"]:
                return make_cmd_result("")
            raise AssertionError(f"Unexpected subprocess.run call: {cmd}")

        process_mock = make_process_mock(stdout="ok")

        captured_prompts: list[str] = []

        def popen_side_effect(cmd, **kwargs):
            cwd = kwargs.get("cwd", "")
            if cwd:
                pf = Path(cwd) / "_review_prompt.md"
                if pf.exists():
                    captured_prompts.append(pf.read_text())
            return process_mock

        mocker.patch("auto_fixer.fetch_open_prs", return_value=prs)
        mocker.patch("auto_fixer.fetch_pr_details", return_value=pr_data)
        mocker.patch(
            "auto_fixer.fetch_pr_review_comments", return_value=review_comments
        )
        mocker.patch("auto_fixer.fetch_review_threads", return_value=thread_map)
        mocker.patch("auto_fixer.fetch_issue_comments", return_value=[])
        mocker.patch("auto_fixer.get_branch_compare_status", return_value=("ahead", 0))
        mocker.patch("auto_fixer.load_state_comment", return_value=make_state_comment())
        mocker.patch("auto_fixer.prepare_repository", return_value=tmp_path)
        mocker.patch(
            "auto_fixer.summarize_reviews",
            return_value={
                "r1": "review summary",
                "discussion_r10": "comment summary",
            },
        )
        mocker.patch("auto_fixer.subprocess.run", side_effect=_run_side_effect)
        mock_popen = mocker.patch(
            "auto_fixer.subprocess.Popen", side_effect=popen_side_effect
        )
        mocker.patch("auto_fixer.set_pr_running_label")
        mock_upsert_state_comment = mocker.patch("auto_fixer.upsert_state_comment")
        mock_resolve_thread = mocker.patch(
            "auto_fixer.resolve_review_thread", return_value=True
        )
        auto_fixer.process_repo({"repo": "owner/repo"})

        assert mock_popen.call_count == 1
        assert len(captured_prompts) == 1
        assert "review summary" in captured_prompts[0]
        assert "comment summary" in captured_prompts[0]
        mock_resolve_thread.assert_called_once_with("thread-node-id")
        mock_upsert_state_comment.assert_called_once()
        args = mock_upsert_state_comment.call_args.args
        assert args[:2] == ("owner/repo", 1)
        assert [(entry.comment_id, entry.url) for entry in args[2]] == [
            ("r1", "https://github.com/owner/repo/pull/1#discussion_r1"),
            ("discussion_r10", "https://github.com/owner/repo/pull/1#discussion_r10"),
        ]

    def test_ci_fix_runs_before_merge_and_review_fix(
        self, mocker, make_cmd_result, tmp_path
    ):
        prs = [{"number": 1, "title": "Test"}]
        pr_data = {
            "headRefName": "feature",
            "baseRefName": "main",
            "title": "Test",
            "reviews": [
                {
                    "id": "r1",
                    "body": "fix review",
                    "author": {"login": "coderabbitai[bot]"},
                }
            ],
            "check_runs": [
                {
                    "name": "ci/test",
                    "conclusion": "FAILURE",
                    "detailsUrl": "https://example.com/ci/test",
                }
            ],
        }
        call_order: list[str] = []

        def run_claude_side_effect(*, phase_label, **kwargs):
            call_order.append(phase_label)
            if phase_label == "ci-fix":
                return ("aaa111 ci fix", "ci stdout")
            if phase_label == "review-fix":
                return ("bbb222 review fix", "review stdout")
            raise AssertionError(f"Unexpected phase_label: {phase_label}")

        def merge_side_effect(*args, **kwargs):
            call_order.append("merge-base")
            return (False, False)

        def run_side_effect(cmd, **kwargs):
            if cmd == ["git", "status", "--porcelain"]:
                return make_cmd_result("")
            if cmd[:3] == ["git", "push", "origin"]:
                return make_cmd_result("")
            if cmd == ["git", "log", "origin/feature..HEAD", "--oneline"]:
                return make_cmd_result("")
            raise AssertionError(f"Unexpected subprocess.run call: {cmd}")

        mocker.patch("auto_fixer.fetch_open_prs", return_value=prs)
        mocker.patch("auto_fixer.fetch_pr_details", return_value=pr_data)
        mocker.patch("auto_fixer.fetch_pr_review_comments", return_value=[])
        mocker.patch("auto_fixer.fetch_review_threads", return_value={})
        mocker.patch("auto_fixer.fetch_issue_comments", return_value=[])
        mocker.patch("auto_fixer.get_branch_compare_status", return_value=("behind", 1))
        mocker.patch("auto_fixer.load_state_comment", return_value=make_state_comment())
        mocker.patch("auto_fixer.prepare_repository", return_value=tmp_path)
        mocker.patch("auto_fixer.collect_ci_failure_materials", return_value=[])
        mocker.patch("auto_fixer.merge_base_branch", side_effect=merge_side_effect)
        mocker.patch(
            "auto_fixer.summarize_reviews", return_value={"r1": "review summary"}
        )
        mocker.patch("auto_fixer.run_claude_prompt", side_effect=run_claude_side_effect)
        mocker.patch("auto_fixer.set_pr_running_label")
        mocker.patch("auto_fixer.subprocess.run", side_effect=run_side_effect)
        mock_upsert_state_comment = mocker.patch("auto_fixer.upsert_state_comment")
        auto_fixer.process_repo({"repo": "owner/repo"})

        assert call_order == ["ci-fix", "merge-base", "review-fix"]
        mock_upsert_state_comment.assert_called_once()
        args = mock_upsert_state_comment.call_args.args
        assert args[:2] == ("owner/repo", 1)
        assert [(entry.comment_id, entry.url) for entry in args[2]] == [
            ("r1", "https://github.com/owner/repo/pull/1#discussion_r1"),
        ]

    def test_ci_only_path_when_no_reviews_and_not_behind(
        self, mocker, make_cmd_result, tmp_path
    ):
        """CI failing, no reviews, not behind -> only ci-fix phase runs."""
        prs = [{"number": 1, "title": "Test"}]
        pr_data = {
            "headRefName": "feature",
            "baseRefName": "main",
            "title": "Test",
            "reviews": [],
            "check_runs": [
                {
                    "name": "ci/test",
                    "conclusion": "FAILURE",
                    "detailsUrl": "https://example.com/ci/test",
                }
            ],
        }
        call_order: list[str] = []

        def run_claude_side_effect(*, phase_label, **kwargs):
            call_order.append(phase_label)
            if phase_label == "ci-fix":
                return ("aaa111 ci fix", "ci stdout")
            raise AssertionError(f"Unexpected phase_label: {phase_label}")

        mocker.patch("auto_fixer.fetch_open_prs", return_value=prs)
        mocker.patch("auto_fixer.fetch_pr_details", return_value=pr_data)
        mocker.patch("auto_fixer.fetch_pr_review_comments", return_value=[])
        mocker.patch("auto_fixer.fetch_review_threads", return_value={})
        mocker.patch("auto_fixer.fetch_issue_comments", return_value=[])
        mocker.patch("auto_fixer.get_branch_compare_status", return_value=("ahead", 0))
        mocker.patch("auto_fixer.load_state_comment", return_value=make_state_comment())
        mocker.patch("auto_fixer.prepare_repository", return_value=tmp_path)
        mocker.patch("auto_fixer.collect_ci_failure_materials", return_value=[])
        mocker.patch("auto_fixer.run_claude_prompt", side_effect=run_claude_side_effect)
        mocker.patch(
            "auto_fixer.subprocess.run",
            return_value=make_cmd_result(""),
        )
        mock_upsert_state_comment = mocker.patch("auto_fixer.upsert_state_comment")
        auto_fixer.process_repo({"repo": "owner/repo"})

        assert call_order == ["ci-fix"]
        # write_result_to_comment defaults to True, so the result log is written to state comment
        mock_upsert_state_comment.assert_called_once()

    def test_ci_only_path_records_result_log_in_state_comment_when_enabled(
        self, mocker, make_cmd_result, tmp_path
    ):
        prs = [{"number": 1, "title": "Test"}]
        pr_data = {
            "headRefName": "feature",
            "baseRefName": "main",
            "title": "Test",
            "reviews": [],
            "check_runs": [
                {
                    "name": "ci/test",
                    "conclusion": "FAILURE",
                    "detailsUrl": "https://example.com/ci/test",
                }
            ],
        }
        cfg = {
            "models": {"summarize": "haiku", "fix": "sonnet"},
            "ci_log_max_lines": 120,
            "write_result_to_comment": True,
            "repositories": [
                {"repo": "owner/repo", "user_name": None, "user_email": None}
            ],
        }

        mocker.patch("auto_fixer.fetch_open_prs", return_value=prs)
        mocker.patch("auto_fixer.fetch_pr_details", return_value=pr_data)
        mocker.patch("auto_fixer.fetch_pr_review_comments", return_value=[])
        mocker.patch("auto_fixer.fetch_review_threads", return_value={})
        mocker.patch("auto_fixer.fetch_issue_comments", return_value=[])
        mocker.patch("auto_fixer.get_branch_compare_status", return_value=("ahead", 0))
        mocker.patch("auto_fixer.load_state_comment", return_value=make_state_comment())
        mocker.patch("auto_fixer.prepare_repository", return_value=tmp_path)
        mocker.patch("auto_fixer.collect_ci_failure_materials", return_value=[])
        mocker.patch(
            "auto_fixer.run_claude_prompt",
            return_value=("aaa111 ci fix", "CI stdout output"),
        )
        mocker.patch(
            "auto_fixer.subprocess.run",
            return_value=make_cmd_result(""),
        )
        mock_upsert = mocker.patch("auto_fixer.upsert_state_comment")
        mocker.patch(
            "auto_fixer.update_done_label_if_completed",
            return_value=(False, False),
        )
        auto_fixer.process_repo({"repo": "owner/repo"}, config=cfg)

        # CI-only パスでは upsert_state_comment で result_log_body が保存される
        mock_upsert.assert_called_once()
        call_kwargs = mock_upsert.call_args.kwargs
        assert "result_log_body" in call_kwargs
        assert "CI stdout output" in call_kwargs["result_log_body"]

    def test_rate_limit_skips_review_fix_but_runs_ci_and_merge_base(
        self, mocker, make_cmd_result, tmp_path
    ):
        prs = [{"number": 1, "title": "Test"}]
        pr_data = {
            "headRefName": "feature",
            "baseRefName": "main",
            "title": "Test",
            "reviews": [
                {
                    "id": "r1",
                    "body": "fix review",
                    "author": {"login": "coderabbitai[bot]"},
                }
            ],
            "check_runs": [
                {
                    "name": "ci/test",
                    "conclusion": "FAILURE",
                    "detailsUrl": "https://example.com/ci/test",
                }
            ],
        }
        issue_comments = [
            {
                "id": 99,
                "body": _RATE_LIMIT_BODY,
                "user": {"login": "coderabbitai[bot]"},
                "updated_at": "2999-03-11T12:00:00Z",
            }
        ]
        call_order: list[str] = []

        def run_claude_side_effect(*, phase_label, **kwargs):
            call_order.append(phase_label)
            if phase_label == "ci-fix":
                return ("aaa111 ci fix", "ci stdout")
            raise AssertionError(f"Unexpected phase_label: {phase_label}")

        def merge_side_effect(*args, **kwargs):
            call_order.append("merge-base")
            return (False, False)

        mocker.patch("auto_fixer.fetch_open_prs", return_value=prs)
        mocker.patch("auto_fixer.fetch_pr_details", return_value=pr_data)
        mocker.patch("auto_fixer.fetch_pr_review_comments", return_value=[])
        mocker.patch("auto_fixer.fetch_review_threads", return_value={})
        mocker.patch("auto_fixer.fetch_issue_comments", return_value=issue_comments)
        mocker.patch("auto_fixer.get_branch_compare_status", return_value=("behind", 1))
        mocker.patch("auto_fixer.load_state_comment", return_value=make_state_comment())
        mocker.patch("auto_fixer.prepare_repository", return_value=tmp_path)
        mocker.patch("auto_fixer.collect_ci_failure_materials", return_value=[])
        mocker.patch("auto_fixer.merge_base_branch", side_effect=merge_side_effect)
        mocker.patch("auto_fixer.run_claude_prompt", side_effect=run_claude_side_effect)
        mocker.patch("auto_fixer.set_pr_running_label")
        mocker.patch(
            "auto_fixer.subprocess.run",
            return_value=make_cmd_result(""),
        )
        mock_update_done = mocker.patch(
            "auto_fixer.update_done_label_if_completed",
            return_value=(False, False),
        )
        mock_summarize = mocker.patch("auto_fixer.summarize_reviews")
        auto_fixer.process_repo({"repo": "owner/repo"})

        assert call_order == ["ci-fix", "merge-base"]
        mock_summarize.assert_not_called()
        assert mock_update_done.call_args.kwargs["coderabbit_rate_limit_active"] is True

    def test_review_failed_auto_resume_counts_toward_per_run_limit(self, mocker):
        prs = [
            {"number": 1, "title": "PR 1"},
            {"number": 2, "title": "PR 2"},
        ]
        pr_data = {
            "headRefName": "feature",
            "baseRefName": "main",
            "title": "Test",
            "reviews": [],
            "comments": [],
        }
        issue_comments_by_pr = {
            1: [
                {
                    "id": 101,
                    "body": _REVIEW_FAILED_BODY,
                    "user": {"login": "coderabbitai[bot]"},
                    "updated_at": "2026-03-11T12:00:00Z",
                }
            ],
            2: [
                {
                    "id": 102,
                    "body": _REVIEW_FAILED_BODY,
                    "user": {"login": "coderabbitai[bot]"},
                    "updated_at": "2026-03-11T12:05:00Z",
                }
            ],
        }
        cfg = {
            "models": {"summarize": "haiku", "fix": "sonnet"},
            "ci_log_max_lines": 120,
            "auto_merge": False,
            "coderabbit_auto_resume": True,
            "coderabbit_auto_resume_max_per_run": 1,
            "process_draft_prs": False,
            "state_comment_timezone": "JST",
            "max_modified_prs_per_run": 0,
            "max_committed_prs_per_run": 2,
            "max_claude_prs_per_run": 0,
            "repositories": [
                {"repo": "owner/repo", "user_name": None, "user_email": None}
            ],
        }
        global_resumed_prs: set[tuple[str, int]] = set()
        auto_resume_run_state = {"posted": 0, "max_per_run": 1}

        mocker.patch("auto_fixer.fetch_open_prs", return_value=prs)
        mocker.patch("auto_fixer.fetch_pr_details", return_value=pr_data)
        mocker.patch("auto_fixer.fetch_pr_review_comments", return_value=[])
        mocker.patch("auto_fixer.fetch_review_threads", return_value={})
        mocker.patch(
            "auto_fixer.fetch_issue_comments",
            side_effect=lambda _repo, pr_number: issue_comments_by_pr[pr_number],
        )
        mocker.patch("auto_fixer.get_branch_compare_status", return_value=("ahead", 0))
        mocker.patch("auto_fixer.load_state_comment", return_value=make_state_comment())
        mocker.patch("auto_fixer.set_pr_running_label")
        mocker.patch(
            "auto_fixer.update_done_label_if_completed",
            return_value=(False, False),
        )
        mock_post_issue_comment = mocker.patch(
            "coderabbit._post_issue_comment", return_value=True
        )
        auto_fixer.process_repo(
            {"repo": "owner/repo"},
            config=cfg,
            global_coderabbit_resumed_prs=global_resumed_prs,
            auto_resume_run_state=auto_resume_run_state,
        )

        assert mock_post_issue_comment.call_count == 1
        assert auto_resume_run_state["posted"] == 1
        assert len(global_resumed_prs) == 1

    def test_review_skipped_draft_detected_triggers_single_review(self, mocker):
        prs = [{"number": 1, "title": "PR 1", "isDraft": False}]
        pr_data = {
            "headRefName": "feature",
            "baseRefName": "main",
            "title": "Test",
            "reviews": [],
            "comments": [],
            "isDraft": False,
        }
        cfg = {
            "models": {"summarize": "haiku", "fix": "sonnet"},
            "ci_log_max_lines": 120,
            "auto_merge": False,
            "coderabbit_auto_resume": True,
            "coderabbit_auto_resume_triggers": {
                "rate_limit": True,
                "draft_detected": True,
            },
            "coderabbit_auto_resume_max_per_run": 1,
            "process_draft_prs": False,
            "state_comment_timezone": "JST",
            "max_modified_prs_per_run": 0,
            "max_committed_prs_per_run": 2,
            "max_claude_prs_per_run": 0,
            "repositories": [
                {"repo": "owner/repo", "user_name": None, "user_email": None}
            ],
        }
        mocker.patch("auto_fixer.fetch_open_prs", return_value=prs)
        mocker.patch("auto_fixer.fetch_pr_details", return_value=pr_data)
        mocker.patch("auto_fixer.fetch_pr_review_comments", return_value=[])
        mocker.patch("auto_fixer.fetch_review_threads", return_value={})
        mocker.patch(
            "auto_fixer.fetch_issue_comments",
            return_value=[
                {
                    "id": 111,
                    "body": _REVIEW_SKIPPED_DRAFT_BODY,
                    "user": {"login": "coderabbitai[bot]"},
                    "updated_at": "2026-03-11T12:00:00Z",
                }
            ],
        )
        mocker.patch("auto_fixer.get_branch_compare_status", return_value=("ahead", 0))
        mocker.patch("auto_fixer.load_state_comment", return_value=make_state_comment())
        mocker.patch("auto_fixer.set_pr_running_label")
        mock_update_done = mocker.patch(
            "auto_fixer.update_done_label_if_completed",
            return_value=(False, False),
        )
        mock_post = mocker.patch("coderabbit._post_issue_comment", return_value=True)
        auto_fixer.process_repo({"repo": "owner/repo"}, config=cfg)

        mock_post.assert_called_once_with(
            "owner/repo", 1, "@coderabbitai review", error_collector=None
        )
        assert (
            mock_update_done.call_args.kwargs["coderabbit_review_skipped_active"]
            is True
        )

    def test_review_skipped_draft_detected_does_not_trigger_while_pr_is_draft(
        self, mocker
    ):
        prs = [{"number": 1, "title": "PR 1", "isDraft": True}]
        pr_data = {
            "headRefName": "feature",
            "baseRefName": "main",
            "title": "Test",
            "reviews": [],
            "comments": [],
            "isDraft": True,
        }
        cfg = {
            "models": {"summarize": "haiku", "fix": "sonnet"},
            "ci_log_max_lines": 120,
            "auto_merge": False,
            "coderabbit_auto_resume": True,
            "coderabbit_auto_resume_triggers": {
                "rate_limit": True,
                "draft_detected": True,
            },
            "coderabbit_auto_resume_max_per_run": 1,
            "process_draft_prs": True,
            "state_comment_timezone": "JST",
            "max_modified_prs_per_run": 0,
            "max_committed_prs_per_run": 2,
            "max_claude_prs_per_run": 0,
            "repositories": [
                {"repo": "owner/repo", "user_name": None, "user_email": None}
            ],
        }
        mocker.patch("auto_fixer.fetch_open_prs", return_value=prs)
        mocker.patch("auto_fixer.fetch_pr_details", return_value=pr_data)
        mocker.patch("auto_fixer.fetch_pr_review_comments", return_value=[])
        mocker.patch("auto_fixer.fetch_review_threads", return_value={})
        mocker.patch(
            "auto_fixer.fetch_issue_comments",
            return_value=[
                {
                    "id": 111,
                    "body": _REVIEW_SKIPPED_DRAFT_BODY,
                    "user": {"login": "coderabbitai[bot]"},
                    "updated_at": "2026-03-11T12:00:00Z",
                }
            ],
        )
        mocker.patch("auto_fixer.get_branch_compare_status", return_value=("ahead", 0))
        mocker.patch("auto_fixer.load_state_comment", return_value=make_state_comment())
        mocker.patch("auto_fixer.set_pr_running_label")
        mock_update_done = mocker.patch(
            "auto_fixer.update_done_label_if_completed",
            return_value=(False, False),
        )
        mock_post = mocker.patch("coderabbit._post_issue_comment")
        auto_fixer.process_repo({"repo": "owner/repo"}, config=cfg)

        mock_post.assert_not_called()
        assert (
            mock_update_done.call_args.kwargs["coderabbit_review_skipped_active"]
            is True
        )

    def test_summarize_only_stops_before_fix_and_state_update(
        self, mocker, tmp_path, capsys
    ):
        """summarize_only=True -> no fix model, no state comment update."""
        prs = [{"number": 1, "title": "Test"}]
        pr_data = {
            "headRefName": "feature",
            "baseRefName": "main",
            "title": "Test",
            "reviews": [
                {"id": "r1", "body": "fix", "author": {"login": "coderabbitai"}}
            ],
        }
        mocker.patch("auto_fixer.fetch_open_prs", return_value=prs)
        mocker.patch("auto_fixer.fetch_pr_details", return_value=pr_data)
        mocker.patch("auto_fixer.fetch_pr_review_comments", return_value=[])
        mocker.patch("auto_fixer.fetch_review_threads", return_value={})
        mocker.patch("auto_fixer.fetch_issue_comments", return_value=[])
        mocker.patch("auto_fixer.get_branch_compare_status", return_value=("ahead", 0))
        mocker.patch("auto_fixer.load_state_comment", return_value=make_state_comment())
        mocker.patch("auto_fixer.prepare_repository", return_value=tmp_path)
        mocker.patch("auto_fixer.summarize_reviews", return_value={"r1": "summary"})
        mock_popen = mocker.patch("auto_fixer.subprocess.Popen")
        mocker.patch("auto_fixer.upsert_state_comment")
        auto_fixer.process_repo({"repo": "owner/repo"}, summarize_only=True)
        mock_popen.assert_not_called()
        out = capsys.readouterr().out
        assert "Summarize-only mode" in out

    def test_summarize_only_reports_raw_text_fallback(self, mocker, capsys):
        prs = [{"number": 1, "title": "Test"}]
        pr_data = {
            "headRefName": "feature",
            "baseRefName": "main",
            "title": "Test",
            "reviews": [
                {"id": "r1", "body": "fix", "author": {"login": "coderabbitai"}}
            ],
        }
        mocker.patch("auto_fixer.fetch_open_prs", return_value=prs)
        mocker.patch("auto_fixer.fetch_pr_details", return_value=pr_data)
        mocker.patch("auto_fixer.fetch_pr_review_comments", return_value=[])
        mocker.patch("auto_fixer.fetch_review_threads", return_value={})
        mocker.patch("auto_fixer.fetch_issue_comments", return_value=[])
        mocker.patch("auto_fixer.get_branch_compare_status", return_value=("ahead", 0))
        mocker.patch("auto_fixer.load_state_comment", return_value=make_state_comment())
        mocker.patch("auto_fixer.summarize_reviews", return_value={})
        mock_popen = mocker.patch("auto_fixer.subprocess.Popen")
        mocker.patch("auto_fixer.upsert_state_comment")
        auto_fixer.process_repo({"repo": "owner/repo"}, summarize_only=True)
        mock_popen.assert_not_called()
        out = capsys.readouterr().out
        assert "falling back to raw review text for all 1 item(s)" in out

    def test_summarize_only_usage_limit_raises(self, mocker):
        prs = [{"number": 1, "title": "Test"}]
        pr_data = {
            "headRefName": "feature",
            "baseRefName": "main",
            "title": "Test",
            "reviews": [
                {"id": "r1", "body": "fix", "author": {"login": "coderabbitai"}}
            ],
        }
        mocker.patch("auto_fixer.fetch_open_prs", return_value=prs)
        mocker.patch("auto_fixer.fetch_pr_details", return_value=pr_data)
        mocker.patch("auto_fixer.fetch_pr_review_comments", return_value=[])
        mocker.patch("auto_fixer.fetch_review_threads", return_value={})
        mocker.patch("auto_fixer.fetch_issue_comments", return_value=[])
        mocker.patch("auto_fixer.get_branch_compare_status", return_value=("ahead", 0))
        mocker.patch("auto_fixer.load_state_comment", return_value=make_state_comment())
        mocker.patch(
            "auto_fixer.summarize_reviews",
            side_effect=ClaudeUsageLimitError(
                phase="summarization",
                returncode=1,
                stdout="You've hit your limit",
                stderr="",
            ),
        )
        with pytest.raises(ClaudeUsageLimitError):
            auto_fixer.process_repo({"repo": "owner/repo"}, summarize_only=True)

    def test_behind_merge_runs_push_no_claude(
        self, mocker, make_cmd_result, tmp_path, capsys
    ):
        """behind PR with no review targets -> merge runs, push happens, no Claude called."""
        prs = [{"number": 1, "title": "Test"}]
        pr_data = {
            "headRefName": "feature/test",
            "baseRefName": "main",
            "title": "Test",
            "reviews": [],
        }
        mock_run = mocker.patch("auto_fixer.subprocess.run")
        mock_popen = mocker.patch("auto_fixer.subprocess.Popen")
        mocker.patch("auto_fixer.fetch_open_prs", return_value=prs)
        mocker.patch("auto_fixer.fetch_pr_details", return_value=pr_data)
        mocker.patch("auto_fixer.fetch_pr_review_comments", return_value=[])
        mocker.patch("auto_fixer.fetch_review_threads", return_value={})
        mocker.patch("auto_fixer.fetch_issue_comments", return_value=[])
        mocker.patch("auto_fixer.get_branch_compare_status", return_value=("behind", 0))
        mocker.patch("auto_fixer.load_state_comment", return_value=make_state_comment())
        mocker.patch("auto_fixer.prepare_repository", return_value=tmp_path)
        mocker.patch("auto_fixer.merge_base_branch", return_value=(True, False))
        mocker.patch("auto_fixer.upsert_state_comment")
        mock_run.return_value = make_cmd_result("abc1234 Merge main\n")
        result = auto_fixer.process_repo({"repo": "owner/repo"})
        mock_popen.assert_not_called()
        push_calls = [
            c for c in mock_run.call_args_list if c.args and "push" in c.args[0]
        ]
        assert push_calls, "git push should be called after clean merge"
        assert result, "should report the merge commit in commits_added_to"
        out = capsys.readouterr().out
        assert "behind" in out.lower()

    def test_done_label_does_not_skip_processing_when_behind(self, mocker, tmp_path):
        prs = [{"number": 1, "title": "Test", "labels": [{"name": "refix: done"}]}]
        pr_data = {
            "headRefName": "feature/test",
            "baseRefName": "main",
            "title": "Test",
            "reviews": [],
            "comments": [],
        }
        mocker.patch("auto_fixer.fetch_open_prs", return_value=prs)
        mocker.patch("auto_fixer.fetch_pr_details", return_value=pr_data)
        mocker.patch("auto_fixer.fetch_pr_review_comments", return_value=[])
        mocker.patch("auto_fixer.fetch_review_threads", return_value={})
        mocker.patch("auto_fixer.fetch_issue_comments", return_value=[])
        mocker.patch("auto_fixer.get_branch_compare_status", return_value=("behind", 1))
        mocker.patch("auto_fixer.load_state_comment", return_value=make_state_comment())
        mock_prepare = mocker.patch(
            "auto_fixer.prepare_repository", return_value=tmp_path
        )
        mocker.patch("auto_fixer.merge_base_branch", return_value=(False, False))
        mocker.patch(
            "auto_fixer.update_done_label_if_completed",
            return_value=(False, False),
        )
        auto_fixer.process_repo({"repo": "owner/repo"})

        mock_prepare.assert_called_once()

    def test_review_fix_start_sets_running_label(
        self, mocker, make_cmd_result, tmp_path
    ):
        prs = [{"number": 1, "title": "Test"}]
        pr_data = {
            "headRefName": "feature",
            "baseRefName": "main",
            "title": "Test",
            "reviews": [
                {"id": "r1", "body": "fix", "author": {"login": "coderabbitai[bot]"}}
            ],
            "comments": [],
        }
        mocker.patch("auto_fixer.fetch_open_prs", return_value=prs)
        mocker.patch("auto_fixer.fetch_pr_details", return_value=pr_data)
        mocker.patch("auto_fixer.fetch_pr_review_comments", return_value=[])
        mocker.patch("auto_fixer.fetch_review_threads", return_value={})
        mocker.patch("auto_fixer.fetch_issue_comments", return_value=[])
        mocker.patch("auto_fixer.get_branch_compare_status", return_value=("ahead", 0))
        mocker.patch("auto_fixer.load_state_comment", return_value=make_state_comment())
        mocker.patch("auto_fixer.prepare_repository", return_value=tmp_path)
        mocker.patch("auto_fixer.summarize_reviews", return_value={"r1": "summary"})
        mocker.patch("auto_fixer.run_claude_prompt", return_value=("", ""))
        mock_set_running = mocker.patch("auto_fixer.set_pr_running_label")
        mocker.patch(
            "auto_fixer.update_done_label_if_completed",
            return_value=(False, False),
        )
        mocker.patch("auto_fixer.upsert_state_comment")
        mocker.patch(
            "auto_fixer.subprocess.run",
            return_value=make_cmd_result(""),
        )
        auto_fixer.process_repo({"repo": "owner/repo"})

        mock_set_running.assert_called_once_with(
            "owner/repo",
            1,
            pr_data=pr_data,
            enabled_pr_label_keys={
                "running",
                "done",
                "merged",
                "auto_merge_requested",
                "ci_pending",
            },
        )

    def test_process_repo_passes_state_comment_timezone_to_create_state_entry(
        self, mocker, make_cmd_result, tmp_path
    ):
        prs = [{"number": 1, "title": "Test"}]
        pr_data = {
            "headRefName": "feature",
            "baseRefName": "main",
            "title": "Test",
            "reviews": [
                {"id": "r1", "body": "fix", "author": {"login": "coderabbitai[bot]"}}
            ],
            "comments": [],
        }
        cfg = {
            "models": {"summarize": "haiku", "fix": "sonnet"},
            "ci_log_max_lines": 120,
            "state_comment_timezone": "UTC",
            "repositories": [
                {"repo": "owner/repo", "user_name": None, "user_email": None}
            ],
        }
        captured_timezones: list[str] = []

        def _create_state_entry_side_effect(
            comment_id: str,
            url: str,
            processed_at: str | None = None,
            timezone_name: str = "JST",
        ) -> StateEntry:
            captured_timezones.append(timezone_name)
            return StateEntry(
                comment_id=comment_id, url=url, processed_at="2026-03-11 12:00:00 UTC"
            )

        def _run_side_effect(cmd, **kwargs):
            if cmd == ["git", "status", "--porcelain"]:
                return make_cmd_result("")
            if cmd == ["git", "log", "origin/feature..HEAD", "--oneline"]:
                return make_cmd_result("")
            return make_cmd_result("")

        mocker.patch("auto_fixer.fetch_open_prs", return_value=prs)
        mocker.patch("auto_fixer.fetch_pr_details", return_value=pr_data)
        mocker.patch("auto_fixer.fetch_pr_review_comments", return_value=[])
        mocker.patch("auto_fixer.fetch_review_threads", return_value={})
        mocker.patch("auto_fixer.fetch_issue_comments", return_value=[])
        mocker.patch("auto_fixer.get_branch_compare_status", return_value=("ahead", 0))
        mocker.patch("auto_fixer.load_state_comment", return_value=make_state_comment())
        mocker.patch("auto_fixer.prepare_repository", return_value=tmp_path)
        mocker.patch("auto_fixer.summarize_reviews", return_value={"r1": "summary"})
        mocker.patch("auto_fixer.run_claude_prompt", return_value=("", ""))
        mocker.patch("auto_fixer.set_pr_running_label")
        mocker.patch(
            "auto_fixer.update_done_label_if_completed",
            return_value=(False, False),
        )
        mocker.patch("auto_fixer.upsert_state_comment")
        mocker.patch("auto_fixer.subprocess.run", side_effect=_run_side_effect)
        mocker.patch(
            "auto_fixer.create_state_entry",
            side_effect=_create_state_entry_side_effect,
        )
        auto_fixer.process_repo({"repo": "owner/repo"}, config=cfg)

        assert captured_timezones == ["UTC"]

    def test_fetch_open_prs_failure_records_in_error_collector(self, mocker):
        """fetch_open_prs 失敗時に error_collector にエラーが記録される。"""
        from error_collector import ErrorCollector

        collector = ErrorCollector()
        mocker.patch(
            "auto_fixer.fetch_open_prs", side_effect=RuntimeError("network error")
        )
        auto_fixer.process_repo({"repo": "owner/repo"}, error_collector=collector)

        assert collector.has_errors
        assert any("owner/repo" == r.scope for r in collector._errors)
        assert any("Failed to fetch PRs" in r.message for r in collector._errors)

    def test_pr_exception_records_in_error_collector(self, mocker):
        """PR ループ内でエラー時に error_collector にエラーが記録される。"""
        from error_collector import ErrorCollector

        prs = [{"number": 1, "title": "PR #1", "isDraft": False}]
        collector = ErrorCollector()
        mocker.patch("auto_fixer.fetch_open_prs", return_value=prs)
        mocker.patch(
            "auto_fixer.fetch_pr_details",
            side_effect=RuntimeError("API error"),
        )
        auto_fixer.process_repo({"repo": "owner/repo"}, error_collector=collector)

        assert collector.has_errors
        assert any("owner/repo#1" == r.scope for r in collector._errors)


class TestPerRunLimitsProcessRepo:
    """process_repo のPR処理件数制限のスキップ動作テスト。"""

    def _make_pr(self, number, title="PR"):
        return {"number": number, "title": f"{title} #{number}", "isDraft": False}

    def _make_pr_data(self, number):
        return {
            "headRefName": f"feature-{number}",
            "baseRefName": "main",
            "title": f"PR #{number}",
            "reviews": [],
            "comments": [],
        }

    def test_max_modified_prs_skips_after_limit(self, mocker, capsys):
        """max_modified_prs_per_run=1 の場合、2つ目のPRはスキップされる。"""
        prs = [self._make_pr(1), self._make_pr(2)]
        cfg = {
            "models": {"summarize": "haiku", "fix": "sonnet"},
            "ci_log_max_lines": 120,
            "max_modified_prs_per_run": 1,
            "max_committed_prs_per_run": 0,
            "max_claude_prs_per_run": 0,
            "repositories": [{"repo": "owner/repo"}],
        }
        mocker.patch("auto_fixer.fetch_open_prs", return_value=prs)
        mocker.patch(
            "auto_fixer.fetch_pr_details",
            side_effect=[
                self._make_pr_data(1),
                self._make_pr_data(2),
            ],
        )
        mocker.patch("auto_fixer.fetch_pr_review_comments", return_value=[])
        mocker.patch("auto_fixer.fetch_review_threads", return_value={})
        mocker.patch("auto_fixer.fetch_issue_comments", return_value=[])
        mocker.patch("auto_fixer.get_branch_compare_status", return_value=("ahead", 0))
        mocker.patch("auto_fixer.load_state_comment", return_value=make_state_comment())
        mocker.patch(
            "auto_fixer.update_done_label_if_completed", return_value=(True, False)
        )
        auto_fixer.process_repo(
            {"repo": "owner/repo"},
            config=cfg,
            global_modified_prs=set(),
            global_committed_prs=set(),
            global_claude_prs=set(),
        )

        out = capsys.readouterr().out
        # 1つ目のPRは処理される
        assert "Checking owner/repo PR #1" in out
        # 2つ目のPRはスキップされる
        assert (
            "Skipping owner/repo PR #2: max_modified_prs_per_run limit reached" in out
        )

    def test_max_committed_prs_skips_claude_and_push(self, mocker, capsys, tmp_path):
        """max_committed_prs_per_run=1 の場合、2つ目のPRではClaude/push操作がスキップされる。"""
        # PR1: レビューあり（Claude実行→コミット追加）
        # PR2: レビューあり（スキップされるべき）
        prs = [self._make_pr(1), self._make_pr(2)]
        pr_data_1 = {
            "headRefName": "feature-1",
            "baseRefName": "main",
            "title": "PR #1",
            "reviews": [
                {
                    "author": {"login": "coderabbitai[bot]"},
                    "body": "Fix this",
                    "databaseId": 100,
                },
            ],
            "comments": [],
        }
        pr_data_2 = {
            "headRefName": "feature-2",
            "baseRefName": "main",
            "title": "PR #2",
            "reviews": [
                {
                    "author": {"login": "coderabbitai[bot]"},
                    "body": "Fix that",
                    "databaseId": 200,
                },
            ],
            "comments": [],
        }
        cfg = {
            "models": {"summarize": "haiku", "fix": "sonnet"},
            "ci_log_max_lines": 120,
            "max_modified_prs_per_run": 0,
            "max_committed_prs_per_run": 1,
            "max_claude_prs_per_run": 0,
            "repositories": [{"repo": "owner/repo"}],
        }

        works_dir = tmp_path / "works" / "owner__repo"
        works_dir.mkdir(parents=True)

        from unittest.mock import Mock

        mock_popen = Mock()
        mock_popen.communicate.return_value = ("", "")
        mock_popen.returncode = 0

        def mock_run_side_effect(*args, **kwargs):
            cmd = args[0] if args else kwargs.get("args", [])
            mock_result = Mock()
            mock_result.returncode = 0
            mock_result.stdout = ""
            mock_result.stderr = ""
            if cmd and cmd[0] == "git" and "rev-parse" in cmd:
                mock_result.stdout = "abc123"
            if cmd and cmd[0] == "git" and "log" in cmd:
                mock_result.stdout = "abc123 review fix"
            if cmd and cmd[0] == "git" and "status" in cmd:
                mock_result.stdout = ""  # クリーンな状態
            return mock_result

        mocker.patch("auto_fixer.fetch_open_prs", return_value=prs)
        mocker.patch("auto_fixer.fetch_pr_details", side_effect=[pr_data_1, pr_data_2])
        mocker.patch("auto_fixer.fetch_pr_review_comments", return_value=[])
        mocker.patch("auto_fixer.fetch_review_threads", return_value={})
        mocker.patch("auto_fixer.fetch_issue_comments", return_value=[])
        mocker.patch("auto_fixer.get_branch_compare_status", return_value=("ahead", 0))
        mocker.patch("auto_fixer.load_state_comment", return_value=make_state_comment())
        mocker.patch("auto_fixer.prepare_repository", return_value=works_dir)
        mocker.patch("auto_fixer.summarize_reviews", return_value={})
        mock_claude = mocker.patch(
            "auto_fixer.run_claude_prompt",
            return_value=("abc123 review fix", "review stdout"),
        )
        mocker.patch("auto_fixer.set_pr_running_label")
        mocker.patch("auto_fixer.edit_pr_label")
        mocker.patch("auto_fixer.upsert_state_comment")
        mocker.patch(
            "auto_fixer.update_done_label_if_completed",
            return_value=(False, False),
        )
        mocker.patch("auto_fixer.subprocess.run", side_effect=mock_run_side_effect)
        mocker.patch("auto_fixer.subprocess.Popen", return_value=mock_popen)
        auto_fixer.process_repo(
            {"repo": "owner/repo"},
            config=cfg,
            global_modified_prs=set(),
            global_committed_prs=set(),
            global_claude_prs=set(),
        )

        out = capsys.readouterr().out
        # PR#1 は処理される
        assert "Checking owner/repo PR #1" in out
        # PR#2 はスキップメッセージが出力される
        assert "max_committed_prs_per_run limit reached" in out
        # Claude は1回だけ呼ばれる（PR#1のみ）
        assert mock_claude.call_count == 1


class TestExcludeFilters:
    """exclude_authors / exclude_labels によるスキップ動作テスト。"""

    def _make_pr(self, number, author_login="", labels=None):
        return {
            "number": number,
            "title": f"PR #{number}",
            "isDraft": False,
            "author": {"login": author_login},
            "labels": [{"name": lbl} for lbl in (labels or [])],
        }

    def test_exclude_authors_exact_match_skips_pr(self, mocker, capsys):
        prs = [self._make_pr(1, author_login="renovate-bot")]
        cfg = {
            "models": {"summarize": "haiku", "fix": "sonnet"},
            "ci_log_max_lines": 120,
            "exclude_authors": ["renovate-bot"],
            "repositories": [
                {"repo": "owner/repo", "user_name": None, "user_email": None}
            ],
        }
        mocker.patch("auto_fixer.fetch_open_prs", return_value=prs)
        mock_fetch = mocker.patch("auto_fixer.fetch_pr_details")
        auto_fixer.process_repo({"repo": "owner/repo"}, config=cfg)

        mock_fetch.assert_not_called()
        assert "exclude_authors" in capsys.readouterr().out

    def test_exclude_authors_wildcard_matches(self, mocker, capsys):
        prs = [self._make_pr(1, author_login="dependabot-app")]
        cfg = {
            "models": {"summarize": "haiku", "fix": "sonnet"},
            "ci_log_max_lines": 120,
            "exclude_authors": ["dependabot*"],
            "repositories": [
                {"repo": "owner/repo", "user_name": None, "user_email": None}
            ],
        }
        mocker.patch("auto_fixer.fetch_open_prs", return_value=prs)
        mock_fetch = mocker.patch("auto_fixer.fetch_pr_details")
        auto_fixer.process_repo({"repo": "owner/repo"}, config=cfg)

        mock_fetch.assert_not_called()
        assert "exclude_authors" in capsys.readouterr().out

    def test_exclude_labels_exact_match_skips_pr(self, mocker, capsys):
        prs = [self._make_pr(1, labels=["do-not-merge"])]
        cfg = {
            "models": {"summarize": "haiku", "fix": "sonnet"},
            "ci_log_max_lines": 120,
            "exclude_labels": ["do-not-merge"],
            "repositories": [
                {"repo": "owner/repo", "user_name": None, "user_email": None}
            ],
        }
        mocker.patch("auto_fixer.fetch_open_prs", return_value=prs)
        mock_fetch = mocker.patch("auto_fixer.fetch_pr_details")
        auto_fixer.process_repo({"repo": "owner/repo"}, config=cfg)

        mock_fetch.assert_not_called()
        assert "exclude_labels" in capsys.readouterr().out

    def test_exclude_labels_wildcard_matches(self, mocker, capsys):
        prs = [self._make_pr(1, labels=["autorelease: tagged"])]
        cfg = {
            "models": {"summarize": "haiku", "fix": "sonnet"},
            "ci_log_max_lines": 120,
            "exclude_labels": ["autorelease: *"],
            "repositories": [
                {"repo": "owner/repo", "user_name": None, "user_email": None}
            ],
        }
        mocker.patch("auto_fixer.fetch_open_prs", return_value=prs)
        mock_fetch = mocker.patch("auto_fixer.fetch_pr_details")
        auto_fixer.process_repo({"repo": "owner/repo"}, config=cfg)

        mock_fetch.assert_not_called()
        assert "exclude_labels" in capsys.readouterr().out

    def test_no_match_processes_normally(self, mocker):
        prs = [self._make_pr(1, author_login="normal-user", labels=["feature"])]
        pr_data = {
            "headRefName": "feature",
            "baseRefName": "main",
            "title": "PR #1",
            "reviews": [],
            "comments": [],
        }
        cfg = {
            "models": {"summarize": "haiku", "fix": "sonnet"},
            "ci_log_max_lines": 120,
            "exclude_authors": ["*[bot]"],
            "exclude_labels": ["do-not-merge"],
            "repositories": [
                {"repo": "owner/repo", "user_name": None, "user_email": None}
            ],
        }
        mocker.patch("auto_fixer.fetch_open_prs", return_value=prs)
        mock_fetch = mocker.patch("auto_fixer.fetch_pr_details", return_value=pr_data)
        mocker.patch("auto_fixer.fetch_pr_review_comments", return_value=[])
        mocker.patch("auto_fixer.fetch_review_threads", return_value={})
        mocker.patch("auto_fixer.fetch_issue_comments", return_value=[])
        mocker.patch("auto_fixer.get_branch_compare_status", return_value=("ahead", 0))
        mocker.patch("auto_fixer.load_state_comment", return_value=make_state_comment())
        mocker.patch(
            "auto_fixer.update_done_label_if_completed", return_value=(False, False)
        )
        auto_fixer.process_repo({"repo": "owner/repo"}, config=cfg)

        mock_fetch.assert_called_once()


class TestTargetAuthorsFilter:
    """target_authors によるスキップ動作テスト。"""

    def _make_pr(self, number, author_login=""):
        return {
            "number": number,
            "title": f"PR #{number}",
            "isDraft": False,
            "author": {"login": author_login},
            "labels": [],
        }

    def _base_cfg(self, target_authors):
        return {
            "models": {"summarize": "haiku", "fix": "sonnet"},
            "ci_log_max_lines": 120,
            "target_authors": target_authors,
            "repositories": [
                {"repo": "owner/repo", "user_name": None, "user_email": None}
            ],
        }

    def test_empty_target_authors_processes_all(self, mocker, capsys):
        prs = [self._make_pr(1, author_login="any-user")]
        pr_data = {
            "headRefName": "feature",
            "baseRefName": "main",
            "title": "PR #1",
            "reviews": [],
            "comments": [],
        }
        mocker.patch("auto_fixer.fetch_open_prs", return_value=prs)
        mock_fetch = mocker.patch("auto_fixer.fetch_pr_details", return_value=pr_data)
        mocker.patch("auto_fixer.fetch_pr_review_comments", return_value=[])
        mocker.patch("auto_fixer.fetch_review_threads", return_value={})
        mocker.patch("auto_fixer.fetch_issue_comments", return_value=[])
        mocker.patch("auto_fixer.get_branch_compare_status", return_value=("ahead", 0))
        mocker.patch("auto_fixer.load_state_comment", return_value=make_state_comment())
        mocker.patch(
            "auto_fixer.update_done_label_if_completed", return_value=(False, False)
        )
        auto_fixer.process_repo({"repo": "owner/repo"}, config=self._base_cfg([]))

        mock_fetch.assert_called_once()

    def test_matching_author_is_processed(self, mocker, capsys):
        prs = [self._make_pr(1, author_login="user-a")]
        pr_data = {
            "headRefName": "feature",
            "baseRefName": "main",
            "title": "PR #1",
            "reviews": [],
            "comments": [],
        }
        mocker.patch("auto_fixer.fetch_open_prs", return_value=prs)
        mock_fetch = mocker.patch("auto_fixer.fetch_pr_details", return_value=pr_data)
        mocker.patch("auto_fixer.fetch_pr_review_comments", return_value=[])
        mocker.patch("auto_fixer.fetch_review_threads", return_value={})
        mocker.patch("auto_fixer.fetch_issue_comments", return_value=[])
        mocker.patch("auto_fixer.get_branch_compare_status", return_value=("ahead", 0))
        mocker.patch("auto_fixer.load_state_comment", return_value=make_state_comment())
        mocker.patch(
            "auto_fixer.update_done_label_if_completed", return_value=(False, False)
        )
        auto_fixer.process_repo(
            {"repo": "owner/repo"}, config=self._base_cfg(["user-a"])
        )

        mock_fetch.assert_called_once()

    def test_non_matching_author_skips_pr(self, mocker, capsys):
        prs = [self._make_pr(1, author_login="other-user")]
        mocker.patch("auto_fixer.fetch_open_prs", return_value=prs)
        mock_fetch = mocker.patch("auto_fixer.fetch_pr_details")
        auto_fixer.process_repo(
            {"repo": "owner/repo"}, config=self._base_cfg(["user-a"])
        )

        mock_fetch.assert_not_called()
        assert "target_authors" in capsys.readouterr().out

    def test_wildcard_pattern_matches(self, mocker, capsys):
        prs = [self._make_pr(1, author_login="dep-bot")]
        pr_data = {
            "headRefName": "feature",
            "baseRefName": "main",
            "title": "PR #1",
            "reviews": [],
            "comments": [],
        }
        mocker.patch("auto_fixer.fetch_open_prs", return_value=prs)
        mock_fetch = mocker.patch("auto_fixer.fetch_pr_details", return_value=pr_data)
        mocker.patch("auto_fixer.fetch_pr_review_comments", return_value=[])
        mocker.patch("auto_fixer.fetch_review_threads", return_value={})
        mocker.patch("auto_fixer.fetch_issue_comments", return_value=[])
        mocker.patch("auto_fixer.get_branch_compare_status", return_value=("ahead", 0))
        mocker.patch("auto_fixer.load_state_comment", return_value=make_state_comment())
        mocker.patch(
            "auto_fixer.update_done_label_if_completed", return_value=(False, False)
        )
        auto_fixer.process_repo(
            {"repo": "owner/repo"}, config=self._base_cfg(["dep-?ot"])
        )

        mock_fetch.assert_called_once()

    def test_wildcard_pattern_matches_prefix(self, mocker, capsys):
        prs = [self._make_pr(1, author_login="dep-xyz")]
        pr_data = {
            "headRefName": "feature",
            "baseRefName": "main",
            "title": "PR #1",
            "reviews": [],
            "comments": [],
        }
        mocker.patch("auto_fixer.fetch_open_prs", return_value=prs)
        mock_fetch = mocker.patch("auto_fixer.fetch_pr_details", return_value=pr_data)
        mocker.patch("auto_fixer.fetch_pr_review_comments", return_value=[])
        mocker.patch("auto_fixer.fetch_review_threads", return_value={})
        mocker.patch("auto_fixer.fetch_issue_comments", return_value=[])
        mocker.patch("auto_fixer.get_branch_compare_status", return_value=("ahead", 0))
        mocker.patch("auto_fixer.load_state_comment", return_value=make_state_comment())
        mocker.patch(
            "auto_fixer.update_done_label_if_completed", return_value=(False, False)
        )
        auto_fixer.process_repo({"repo": "owner/repo"}, config=self._base_cfg(["dep*"]))

        mock_fetch.assert_called_once()


class TestAutoMergeAuthorsFilter:
    """auto_merge_authors による自動マージ制御テスト。"""

    def _make_pr(self, number, author_login=""):
        return {
            "number": number,
            "title": f"PR #{number}",
            "isDraft": False,
            "author": {"login": author_login},
            "labels": [],
        }

    def _base_cfg(self, auto_merge_authors, auto_merge=True):
        return {
            "models": {"summarize": "haiku", "fix": "sonnet"},
            "ci_log_max_lines": 120,
            "auto_merge": auto_merge,
            "auto_merge_authors": auto_merge_authors,
            "repositories": [
                {"repo": "owner/repo", "user_name": None, "user_email": None}
            ],
        }

    def _pr_data(self):
        return {
            "headRefName": "feature",
            "baseRefName": "main",
            "title": "PR #1",
            "reviews": [],
            "comments": [],
        }

    def test_empty_auto_merge_authors_merges_all(self, mocker, capsys):
        prs = [self._make_pr(1, author_login="any-user")]
        mocker.patch("auto_fixer.fetch_open_prs", return_value=prs)
        mocker.patch("auto_fixer.fetch_pr_details", return_value=self._pr_data())
        mocker.patch("auto_fixer.fetch_pr_review_comments", return_value=[])
        mocker.patch("auto_fixer.fetch_review_threads", return_value={})
        mocker.patch("auto_fixer.fetch_issue_comments", return_value=[])
        mocker.patch("auto_fixer.get_branch_compare_status", return_value=("ahead", 0))
        mocker.patch("auto_fixer.load_state_comment", return_value=make_state_comment())
        mocker.patch(
            "auto_fixer.update_done_label_if_completed", return_value=(False, False)
        )
        auto_fixer.process_repo({"repo": "owner/repo"}, config=self._base_cfg([]))

        out = capsys.readouterr().out
        assert "auto_merge_authors" not in out

    def test_matching_author_merge_enabled(self, mocker, capsys):
        prs = [self._make_pr(1, author_login="user-a")]
        mocker.patch("auto_fixer.fetch_open_prs", return_value=prs)
        mocker.patch("auto_fixer.fetch_pr_details", return_value=self._pr_data())
        mocker.patch("auto_fixer.fetch_pr_review_comments", return_value=[])
        mocker.patch("auto_fixer.fetch_review_threads", return_value={})
        mocker.patch("auto_fixer.fetch_issue_comments", return_value=[])
        mocker.patch("auto_fixer.get_branch_compare_status", return_value=("ahead", 0))
        mocker.patch("auto_fixer.load_state_comment", return_value=make_state_comment())
        mocker.patch(
            "auto_fixer.update_done_label_if_completed", return_value=(False, False)
        )
        auto_fixer.process_repo(
            {"repo": "owner/repo"}, config=self._base_cfg(["user-a"])
        )

        out = capsys.readouterr().out
        assert "auto_merge_authors" not in out

    def test_non_matching_author_disables_merge(self, mocker, capsys):
        prs = [self._make_pr(1, author_login="other-user")]
        mocker.patch("auto_fixer.fetch_open_prs", return_value=prs)
        mocker.patch("auto_fixer.fetch_pr_details", return_value=self._pr_data())
        mocker.patch("auto_fixer.fetch_pr_review_comments", return_value=[])
        mocker.patch("auto_fixer.fetch_review_threads", return_value={})
        mocker.patch("auto_fixer.fetch_issue_comments", return_value=[])
        mocker.patch("auto_fixer.get_branch_compare_status", return_value=("ahead", 0))
        mocker.patch("auto_fixer.load_state_comment", return_value=make_state_comment())
        mocker.patch(
            "auto_fixer.update_done_label_if_completed", return_value=(False, False)
        )
        auto_fixer.process_repo(
            {"repo": "owner/repo"}, config=self._base_cfg(["user-a"])
        )

        assert "auto_merge_authors" in capsys.readouterr().out

    def test_non_matching_author_but_auto_merge_disabled(self, mocker, capsys):
        """auto_merge=False の場合は auto_merge_authors チェック自体が実行されない。"""
        prs = [self._make_pr(1, author_login="other-user")]
        mocker.patch("auto_fixer.fetch_open_prs", return_value=prs)
        mocker.patch("auto_fixer.fetch_pr_details", return_value=self._pr_data())
        mocker.patch("auto_fixer.fetch_pr_review_comments", return_value=[])
        mocker.patch("auto_fixer.fetch_review_threads", return_value={})
        mocker.patch("auto_fixer.fetch_issue_comments", return_value=[])
        mocker.patch("auto_fixer.get_branch_compare_status", return_value=("ahead", 0))
        mocker.patch("auto_fixer.load_state_comment", return_value=make_state_comment())
        mocker.patch(
            "auto_fixer.update_done_label_if_completed", return_value=(False, False)
        )
        auto_fixer.process_repo(
            {"repo": "owner/repo"},
            config=self._base_cfg(["user-a"], auto_merge=False),
        )

        assert "auto_merge_authors" not in capsys.readouterr().out

    def test_wildcard_pattern_disables_merge_for_non_match(self, mocker, capsys):
        prs = [self._make_pr(1, author_login="normal-user")]
        mocker.patch("auto_fixer.fetch_open_prs", return_value=prs)
        mocker.patch("auto_fixer.fetch_pr_details", return_value=self._pr_data())
        mocker.patch("auto_fixer.fetch_pr_review_comments", return_value=[])
        mocker.patch("auto_fixer.fetch_review_threads", return_value={})
        mocker.patch("auto_fixer.fetch_issue_comments", return_value=[])
        mocker.patch("auto_fixer.get_branch_compare_status", return_value=("ahead", 0))
        mocker.patch("auto_fixer.load_state_comment", return_value=make_state_comment())
        mocker.patch(
            "auto_fixer.update_done_label_if_completed", return_value=(False, False)
        )
        auto_fixer.process_repo({"repo": "owner/repo"}, config=self._base_cfg(["dep*"]))

        assert "auto_merge_authors" in capsys.readouterr().out


class TestErrorCollectorInProcessSinglePr:
    """_process_single_pr 内の各エラー箇所で ErrorCollector にエラーが記録されることを確認するテスト。"""

    _PR = {"number": 1, "title": "PR #1", "isDraft": False}
    _PR_DATA = {
        "headRefName": "feature",
        "baseRefName": "main",
        "title": "PR #1",
        "reviews": [],
        "comments": [],
    }

    def test_load_state_comment_failure_records_error(self, mocker):
        ec = ErrorCollector()
        mocker.patch("auto_fixer.fetch_open_prs", return_value=[self._PR])
        mocker.patch("auto_fixer.fetch_pr_details", return_value=self._PR_DATA)
        mocker.patch(
            "auto_fixer.load_state_comment",
            side_effect=RuntimeError("network error"),
        )
        auto_fixer.process_repo({"repo": "owner/repo"}, error_collector=ec)

        assert ec.has_errors
        assert any("owner/repo#1" == r.scope for r in ec._errors)
        assert any("Failed to load state comment" in r.message for r in ec._errors)

    def test_fetch_review_comments_failure_records_error(self, mocker):
        ec = ErrorCollector()
        mocker.patch("auto_fixer.fetch_open_prs", return_value=[self._PR])
        mocker.patch("auto_fixer.fetch_pr_details", return_value=self._PR_DATA)
        mocker.patch("auto_fixer.load_state_comment", return_value=make_state_comment())
        mocker.patch(
            "auto_fixer.fetch_pr_review_comments",
            side_effect=RuntimeError("fetch failed"),
        )
        auto_fixer.process_repo({"repo": "owner/repo"}, error_collector=ec)

        assert ec.has_errors
        assert any("owner/repo#1" == r.scope for r in ec._errors)
        assert any("Failed to fetch review comments" in r.message for r in ec._errors)

    def test_fetch_review_threads_failure_records_error(self, mocker):
        ec = ErrorCollector()
        mocker.patch("auto_fixer.fetch_open_prs", return_value=[self._PR])
        mocker.patch("auto_fixer.fetch_pr_details", return_value=self._PR_DATA)
        mocker.patch("auto_fixer.load_state_comment", return_value=make_state_comment())
        mocker.patch("auto_fixer.fetch_pr_review_comments", return_value=[])
        mocker.patch(
            "auto_fixer.fetch_review_threads",
            side_effect=RuntimeError("threads failed"),
        )
        auto_fixer.process_repo({"repo": "owner/repo"}, error_collector=ec)

        assert ec.has_errors
        assert any("owner/repo#1" == r.scope for r in ec._errors)
        assert any("Failed to fetch review threads" in r.message for r in ec._errors)

    def test_fetch_issue_comments_failure_records_error(self, mocker):
        ec = ErrorCollector()
        mocker.patch("auto_fixer.fetch_open_prs", return_value=[self._PR])
        mocker.patch("auto_fixer.fetch_pr_details", return_value=self._PR_DATA)
        mocker.patch("auto_fixer.load_state_comment", return_value=make_state_comment())
        mocker.patch("auto_fixer.fetch_pr_review_comments", return_value=[])
        mocker.patch("auto_fixer.fetch_review_threads", return_value={})
        mocker.patch(
            "auto_fixer.fetch_issue_comments",
            side_effect=RuntimeError("issue comments failed"),
        )
        auto_fixer.process_repo({"repo": "owner/repo"}, error_collector=ec)

        assert ec.has_errors
        assert any("owner/repo#1" == r.scope for r in ec._errors)
        assert any("Failed to fetch issue comments" in r.message for r in ec._errors)


class TestSaveResultLog:
    """Tests for the _save_result_log helper function."""

    def _make_state_comment(self, result_log_body: str = "") -> StateComment:
        return StateComment(
            github_comment_id=None,
            body="",
            entries=[],
            processed_ids=set(),
            archived_ids=set(),
            result_log_body=result_log_body,
        )

    def test_returns_false_when_no_blocks(self, mocker):
        mock_upsert = mocker.patch("auto_fixer.upsert_state_comment")
        mock_load = mocker.patch("auto_fixer.load_state_comment")

        result = auto_fixer._save_result_log(
            "owner/repo", 1, [], self._make_state_comment()
        )

        assert result is False
        mock_load.assert_not_called()
        mock_upsert.assert_not_called()

    def test_returns_true_on_success(self, mocker):
        fresh = self._make_state_comment("existing log")
        mocker.patch("auto_fixer.load_state_comment", return_value=fresh)
        mock_upsert = mocker.patch("auto_fixer.upsert_state_comment")

        result = auto_fixer._save_result_log(
            "owner/repo", 1, ["block1"], self._make_state_comment()
        )

        assert result is True
        call_kwargs = mock_upsert.call_args
        assert call_kwargs.kwargs["_preloaded_state"] is fresh

    def test_returns_false_on_upsert_failure(self, mocker):
        mocker.patch(
            "auto_fixer.load_state_comment",
            return_value=self._make_state_comment(),
        )
        mocker.patch(
            "auto_fixer.upsert_state_comment",
            side_effect=RuntimeError("upsert failed"),
        )
        ec = ErrorCollector()

        result = auto_fixer._save_result_log(
            "owner/repo", 1, ["block1"], self._make_state_comment(), ec
        )

        assert result is False
        assert ec.has_errors
        assert any("failed to save execution result" in r.message for r in ec._errors)

    def test_returns_false_on_load_failure(self, mocker):
        fallback = self._make_state_comment("fallback log")
        mocker.patch(
            "auto_fixer.load_state_comment",
            side_effect=RuntimeError("load failed"),
        )
        mock_upsert = mocker.patch("auto_fixer.upsert_state_comment")
        ec = ErrorCollector()

        result = auto_fixer._save_result_log("owner/repo", 1, ["block1"], fallback, ec)

        assert result is False
        mock_upsert.assert_not_called()
        assert ec.has_errors
        assert any("failed to reload state comment" in r.message for r in ec._errors)


class TestMainSinglePrMode:
    def _default_cfg(self):
        return {
            "models": {"summarize": "haiku", "fix": "sonnet"},
            "ci_log_max_lines": 120,
            "enabled_pr_labels": [
                "running",
                "done",
                "merged",
                "auto_merge_requested",
                "ci_pending",
            ],
            "repositories": [],
        }

    def test_single_pr_mode_calls_process_repo_with_target_pr(self, mocker, tmp_path):
        cfg = self._default_cfg()
        mocker.patch.object(
            sys, "argv", ["auto_fixer.py", "--repo", "owner/repo", "--pr", "42"]
        )
        mocker.patch("auto_fixer.load_dotenv")
        mocker.patch("auto_fixer.load_config_for_action", return_value=cfg)
        mock_process_repo = mocker.patch("auto_fixer.process_repo", return_value=[])

        auto_fixer.main()

        mock_process_repo.assert_called_once()
        call_kwargs = mock_process_repo.call_args.kwargs
        assert call_kwargs["target_pr_number"] == 42
        call_args = mock_process_repo.call_args.args
        assert call_args[0]["repo"] == "owner/repo"

    def test_single_pr_mode_passes_dry_run(self, mocker):
        cfg = self._default_cfg()
        mocker.patch.object(
            sys,
            "argv",
            ["auto_fixer.py", "--repo", "owner/repo", "--pr", "42", "--dry-run"],
        )
        mocker.patch("auto_fixer.load_dotenv")
        mocker.patch("auto_fixer.load_config_for_action", return_value=cfg)
        mock_process_repo = mocker.patch("auto_fixer.process_repo", return_value=[])

        auto_fixer.main()

        assert mock_process_repo.call_args.kwargs["dry_run"] is True

    def test_repo_without_pr_exits_with_error(self, mocker, capsys):
        mocker.patch.object(sys, "argv", ["auto_fixer.py", "--repo", "owner/repo"])
        mocker.patch("auto_fixer.load_dotenv")

        with pytest.raises(SystemExit) as exc_info:
            auto_fixer.main()

        assert exc_info.value.code == 1
        assert "--repo and --pr must be specified together" in capsys.readouterr().err

    def test_pr_without_repo_exits_with_error(self, mocker, capsys):
        mocker.patch.object(sys, "argv", ["auto_fixer.py", "--pr", "42"])
        mocker.patch("auto_fixer.load_dotenv")

        with pytest.raises(SystemExit) as exc_info:
            auto_fixer.main()

        assert exc_info.value.code == 1
        assert "--repo and --pr must be specified together" in capsys.readouterr().err


class TestProcessRepoSinglePrMode:
    def test_process_repo_fetches_single_pr_when_target_specified(self, mocker):
        pr_data = {
            "number": 42,
            "title": "Test PR",
            "isDraft": False,
            "author": {"login": "user"},
            "labels": [],
            "createdAt": "2024-01-01T00:00:00Z",
            "updatedAt": "2024-01-01T00:00:00Z",
        }
        mock_fetch_single = mocker.patch(
            "auto_fixer.fetch_single_pr", return_value=pr_data
        )
        mock_fetch_open = mocker.patch("auto_fixer.fetch_open_prs")
        mocker.patch(
            "auto_fixer._process_single_pr", return_value=(False, False, None, None)
        )

        auto_fixer.process_repo(
            {"repo": "owner/repo", "user_name": None, "user_email": None},
            target_pr_number=42,
        )

        mock_fetch_single.assert_called_once_with("owner/repo", 42)
        mock_fetch_open.assert_not_called()

    def test_process_repo_fetches_open_prs_when_no_target(self, mocker):
        mock_fetch_single = mocker.patch("auto_fixer.fetch_single_pr")
        mock_fetch_open = mocker.patch("auto_fixer.fetch_open_prs", return_value=[])

        auto_fixer.process_repo(
            {"repo": "owner/repo", "user_name": None, "user_email": None},
        )

        mock_fetch_open.assert_called_once_with("owner/repo", limit=1000)
        mock_fetch_single.assert_not_called()

    def test_process_repo_skips_backfill_in_single_pr_mode(self, mocker):
        pr_data = {
            "number": 99,
            "title": "PR",
            "isDraft": False,
            "author": {"login": "user"},
            "labels": [],
            "createdAt": "2024-01-01T00:00:00Z",
            "updatedAt": "2024-01-01T00:00:00Z",
        }
        mocker.patch("auto_fixer.fetch_single_pr", return_value=pr_data)
        mocker.patch(
            "auto_fixer._process_single_pr", return_value=(False, False, None, None)
        )
        mock_backfill = mocker.patch("auto_fixer.backfill_merged_labels")

        auto_fixer.process_repo(
            {"repo": "owner/repo", "user_name": None, "user_email": None},
            config={**auto_fixer.DEFAULT_CONFIG, "auto_merge": True},
            target_pr_number=99,
        )

        mock_backfill.assert_not_called()


class TestResolvePrsFromSha:
    def test_returns_pr_numbers_on_success(self, mocker):
        mock_result = mocker.MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "42\n43\n"
        mocker.patch("auto_fixer.run_command", return_value=mock_result)

        result = auto_fixer._resolve_prs_from_sha("owner/repo", "abc123")

        assert result == [42, 43]

    def test_raises_on_nonzero_returncode(self, mocker):
        mock_result = mocker.MagicMock()
        mock_result.returncode = 1
        mock_result.stdout = ""
        mock_result.stderr = "Not Found"
        mocker.patch("auto_fixer.run_command", return_value=mock_result)

        with pytest.raises(RuntimeError, match="_resolve_prs_from_sha"):
            auto_fixer._resolve_prs_from_sha("owner/repo", "abc123")

    def test_returns_empty_on_empty_output(self, mocker):
        mock_result = mocker.MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "  \n"
        mocker.patch("auto_fixer.run_command", return_value=mock_result)

        result = auto_fixer._resolve_prs_from_sha("owner/repo", "abc123")

        assert result == []

    def test_filters_non_digit_lines(self, mocker):
        mock_result = mocker.MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "42\nnot-a-number\n43\n"
        mocker.patch("auto_fixer.run_command", return_value=mock_result)

        result = auto_fixer._resolve_prs_from_sha("owner/repo", "abc123")

        assert result == [42, 43]


class TestPrHasCiPendingLabel:
    def test_returns_true_when_label_present(self, mocker):
        mock_result = mocker.MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "true"
        mocker.patch("auto_fixer.run_command", return_value=mock_result)

        assert auto_fixer._pr_has_ci_pending_label("owner/repo", 42) is True

    def test_returns_false_when_label_absent(self, mocker):
        mock_result = mocker.MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "false"
        mocker.patch("auto_fixer.run_command", return_value=mock_result)

        assert auto_fixer._pr_has_ci_pending_label("owner/repo", 42) is False

    def test_raises_on_nonzero_returncode(self, mocker):
        mock_result = mocker.MagicMock()
        mock_result.returncode = 1
        mock_result.stdout = ""
        mock_result.stderr = "Not Found"
        mocker.patch("auto_fixer.run_command", return_value=mock_result)

        with pytest.raises(RuntimeError, match="_pr_has_ci_pending_label"):
            auto_fixer._pr_has_ci_pending_label("owner/repo", 42)


class TestFetchCiPendingPrs:
    def test_returns_pr_numbers(self, mocker):
        mock_result = mocker.MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "10\n20\n"
        mocker.patch("auto_fixer.run_command", return_value=mock_result)

        result = auto_fixer._fetch_ci_pending_prs("owner/repo")

        assert result == [10, 20]

    def test_raises_on_failure(self, mocker):
        mock_result = mocker.MagicMock()
        mock_result.returncode = 1
        mock_result.stdout = ""
        mocker.patch("auto_fixer.run_command", return_value=mock_result)

        with pytest.raises(
            RuntimeError, match="_fetch_ci_pending_prs: gh pr list failed"
        ):
            auto_fixer._fetch_ci_pending_prs("owner/repo")


class TestResolveActionTargets:
    def test_pull_request_event_returns_pr_number(self, mocker, tmp_path):
        event_file = tmp_path / "event.json"
        event_file.write_text('{"pull_request": {"number": 99}}')
        mocker.patch.dict(
            "os.environ",
            {"GITHUB_EVENT_NAME": "pull_request", "GITHUB_EVENT_PATH": str(event_file)},
        )

        result = auto_fixer._resolve_action_targets("owner/repo")

        assert result == [99]

    def test_pull_request_review_event_returns_pr_number(self, mocker, tmp_path):
        event_file = tmp_path / "event.json"
        event_file.write_text('{"pull_request": {"number": 55}}')
        mocker.patch.dict(
            "os.environ",
            {
                "GITHUB_EVENT_NAME": "pull_request_review",
                "GITHUB_EVENT_PATH": str(event_file),
            },
        )

        result = auto_fixer._resolve_action_targets("owner/repo")

        assert result == [55]

    def test_pull_request_event_without_number_returns_empty(self, mocker, tmp_path):
        event_file = tmp_path / "event.json"
        event_file.write_text('{"pull_request": {}}')
        mocker.patch.dict(
            "os.environ",
            {"GITHUB_EVENT_NAME": "pull_request", "GITHUB_EVENT_PATH": str(event_file)},
        )

        result = auto_fixer._resolve_action_targets("owner/repo")

        assert result == []

    def test_check_suite_event_filters_by_ci_pending(self, mocker, tmp_path):
        event_file = tmp_path / "event.json"
        event_file.write_text('{"check_suite": {"head_sha": "abc123"}}')
        mocker.patch.dict(
            "os.environ",
            {"GITHUB_EVENT_NAME": "check_suite", "GITHUB_EVENT_PATH": str(event_file)},
        )
        mocker.patch("auto_fixer._resolve_prs_from_sha", return_value=[10, 20, 30])
        mocker.patch(
            "auto_fixer._pr_has_ci_pending_label",
            side_effect=lambda repo, n: n in (10, 30),
        )

        result = auto_fixer._resolve_action_targets("owner/repo")

        assert result == [10, 30]

    def test_check_suite_event_without_sha_returns_empty(self, mocker, tmp_path):
        event_file = tmp_path / "event.json"
        event_file.write_text('{"check_suite": {}}')
        mocker.patch.dict(
            "os.environ",
            {"GITHUB_EVENT_NAME": "check_suite", "GITHUB_EVENT_PATH": str(event_file)},
        )

        result = auto_fixer._resolve_action_targets("owner/repo")

        assert result == []

    def test_schedule_event_returns_ci_pending_prs(self, mocker, tmp_path):
        event_file = tmp_path / "event.json"
        event_file.write_text("{}")
        mocker.patch.dict(
            "os.environ",
            {"GITHUB_EVENT_NAME": "schedule", "GITHUB_EVENT_PATH": str(event_file)},
        )
        mocker.patch("auto_fixer._fetch_ci_pending_prs", return_value=[5, 6])

        result = auto_fixer._resolve_action_targets("owner/repo")

        assert result == [5, 6]

    def test_unsupported_event_returns_empty(self, mocker, tmp_path):
        event_file = tmp_path / "event.json"
        event_file.write_text("{}")
        mocker.patch.dict(
            "os.environ",
            {"GITHUB_EVENT_NAME": "push", "GITHUB_EVENT_PATH": str(event_file)},
        )

        result = auto_fixer._resolve_action_targets("owner/repo")

        assert result == []

    def test_workflow_dispatch_event_returns_pr_number(self, mocker, tmp_path):
        event_file = tmp_path / "event.json"
        event_file.write_text('{"inputs": {"pr": "42"}}')
        mocker.patch.dict(
            "os.environ",
            {
                "GITHUB_EVENT_NAME": "workflow_dispatch",
                "GITHUB_EVENT_PATH": str(event_file),
            },
        )

        result = auto_fixer._resolve_action_targets("owner/repo")

        assert result == [42]

    def test_workflow_dispatch_event_with_invalid_pr_returns_empty(
        self, mocker, tmp_path
    ):
        event_file = tmp_path / "event.json"
        event_file.write_text('{"inputs": {"pr": "abc"}}')
        mocker.patch.dict(
            "os.environ",
            {
                "GITHUB_EVENT_NAME": "workflow_dispatch",
                "GITHUB_EVENT_PATH": str(event_file),
            },
        )

        result = auto_fixer._resolve_action_targets("owner/repo")

        assert result == []

    def test_workflow_dispatch_event_without_pr_returns_empty(self, mocker, tmp_path):
        event_file = tmp_path / "event.json"
        event_file.write_text('{"inputs": {}}')
        mocker.patch.dict(
            "os.environ",
            {
                "GITHUB_EVENT_NAME": "workflow_dispatch",
                "GITHUB_EVENT_PATH": str(event_file),
            },
        )

        result = auto_fixer._resolve_action_targets("owner/repo")

        assert result == []

    def test_missing_env_vars_exits(self, mocker):
        mocker.patch.dict("os.environ", {}, clear=True)
        # GITHUB_EVENT_NAME/PATH が設定されていない場合は sys.exit(1)
        with pytest.raises(SystemExit) as exc_info:
            auto_fixer._resolve_action_targets("owner/repo")
        assert exc_info.value.code == 1


class TestMainActionMode:
    def _default_cfg(self):
        return {
            "models": {"summarize": "haiku", "fix": "sonnet"},
            "ci_log_max_lines": 120,
            "repositories": [],
        }

    def test_action_mode_processes_resolved_prs(self, mocker):
        cfg = self._default_cfg()
        mocker.patch.object(
            sys, "argv", ["auto_fixer.py", "--action", "--repo", "owner/repo"]
        )
        mocker.patch("auto_fixer.load_dotenv")
        mocker.patch("auto_fixer.load_config_for_action", return_value=cfg)
        mocker.patch("auto_fixer._resolve_action_targets", return_value=[10, 20])
        mock_process_repo = mocker.patch("auto_fixer.process_repo", return_value=[])

        auto_fixer.main()

        assert mock_process_repo.call_count == 2
        called_pr_numbers = [
            call.kwargs["target_pr_number"] for call in mock_process_repo.call_args_list
        ]
        assert called_pr_numbers == [10, 20]

    def test_action_mode_skips_when_no_targets(self, mocker, capsys):
        cfg = self._default_cfg()
        mocker.patch.object(
            sys, "argv", ["auto_fixer.py", "--action", "--repo", "owner/repo"]
        )
        mocker.patch("auto_fixer.load_dotenv")
        mocker.patch("auto_fixer.load_config_for_action", return_value=cfg)
        mocker.patch("auto_fixer._resolve_action_targets", return_value=[])
        mock_process_repo = mocker.patch("auto_fixer.process_repo", return_value=[])

        auto_fixer.main()

        mock_process_repo.assert_not_called()
        assert "No actionable PRs found" in capsys.readouterr().out

    def test_action_mode_uses_github_repository_env_when_no_repo_arg(self, mocker):
        cfg = self._default_cfg()
        mocker.patch.object(sys, "argv", ["auto_fixer.py", "--action"])
        mocker.patch("auto_fixer.load_dotenv")
        mocker.patch.dict("os.environ", {"GITHUB_REPOSITORY": "env/repo"})
        mocker.patch("auto_fixer.load_config_for_action", return_value=cfg)
        mock_resolve = mocker.patch(
            "auto_fixer._resolve_action_targets", return_value=[7]
        )
        mocker.patch("auto_fixer.process_repo", return_value=[])

        auto_fixer.main()

        mock_resolve.assert_called_once_with("env/repo")

    def test_action_mode_exits_when_no_repo(self, mocker, capsys):
        mocker.patch.object(sys, "argv", ["auto_fixer.py", "--action"])
        mocker.patch("auto_fixer.load_dotenv")
        mocker.patch.dict("os.environ", {}, clear=True)

        with pytest.raises(SystemExit) as exc_info:
            auto_fixer.main()

        assert exc_info.value.code == 1

    def test_action_mode_cleans_up_running_label_on_exception(self, mocker):
        cfg = self._default_cfg()
        mocker.patch.object(
            sys, "argv", ["auto_fixer.py", "--action", "--repo", "owner/repo"]
        )
        mocker.patch("auto_fixer.load_dotenv")
        mocker.patch("auto_fixer.load_config_for_action", return_value=cfg)
        mocker.patch("auto_fixer._resolve_action_targets", return_value=[42])
        mocker.patch(
            "auto_fixer.process_repo", side_effect=RuntimeError("unexpected error")
        )
        mock_edit_label = mocker.patch("auto_fixer.edit_pr_label")

        with pytest.raises(SystemExit):
            auto_fixer.main()

        mock_edit_label.assert_called_once()
        call_kwargs = mock_edit_label.call_args.kwargs
        assert call_kwargs["add"] is False
        assert call_kwargs["label"] == auto_fixer.REFIX_RUNNING_LABEL

    def test_single_pr_mode_cleans_up_running_label_on_exception(self, mocker):
        cfg = self._default_cfg()
        mocker.patch.object(
            sys, "argv", ["auto_fixer.py", "--repo", "owner/repo", "--pr", "42"]
        )
        mocker.patch("auto_fixer.load_dotenv")
        mocker.patch("auto_fixer.load_config_for_action", return_value=cfg)
        mocker.patch(
            "auto_fixer.process_repo", side_effect=RuntimeError("unexpected error")
        )
        mock_edit_label = mocker.patch("auto_fixer.edit_pr_label")

        with pytest.raises(SystemExit):
            auto_fixer.main()

        mock_edit_label.assert_called_once()
        call_kwargs = mock_edit_label.call_args.kwargs
        assert call_kwargs["add"] is False
        assert call_kwargs["label"] == auto_fixer.REFIX_RUNNING_LABEL

    def test_action_mode_cleans_up_running_label_on_claude_command_failed(self, mocker):
        """action モード: ClaudeCommandFailedError → running 除去 + sys.exit(1)"""
        cfg = self._default_cfg()
        mocker.patch.object(
            sys, "argv", ["auto_fixer.py", "--action", "--repo", "owner/repo"]
        )
        mocker.patch("auto_fixer.load_dotenv")
        mocker.patch("auto_fixer.load_config_for_action", return_value=cfg)
        mocker.patch("auto_fixer._resolve_action_targets", return_value=[42])
        mocker.patch(
            "auto_fixer.process_repo",
            side_effect=ClaudeCommandFailedError(
                phase="review_fix", returncode=1, stdout="out", stderr="err"
            ),
        )
        mock_edit_label = mocker.patch("auto_fixer.edit_pr_label")

        with pytest.raises(SystemExit) as exc_info:
            auto_fixer.main()

        assert exc_info.value.code == 1
        mock_edit_label.assert_called_once()
        call_kwargs = mock_edit_label.call_args.kwargs
        assert call_kwargs["add"] is False
        assert call_kwargs["label"] == auto_fixer.REFIX_RUNNING_LABEL

    def test_single_pr_mode_cleans_up_running_label_on_claude_command_failed(
        self, mocker
    ):
        """single-PR モード: ClaudeCommandFailedError → running 除去 + sys.exit(1)"""
        cfg = self._default_cfg()
        mocker.patch.object(
            sys, "argv", ["auto_fixer.py", "--repo", "owner/repo", "--pr", "42"]
        )
        mocker.patch("auto_fixer.load_dotenv")
        mocker.patch("auto_fixer.load_config_for_action", return_value=cfg)
        mocker.patch(
            "auto_fixer.process_repo",
            side_effect=ClaudeCommandFailedError(
                phase="review_fix", returncode=1, stdout="out", stderr="err"
            ),
        )
        mock_edit_label = mocker.patch("auto_fixer.edit_pr_label")

        with pytest.raises(SystemExit) as exc_info:
            auto_fixer.main()

        assert exc_info.value.code == 1
        mock_edit_label.assert_called_once()
        call_kwargs = mock_edit_label.call_args.kwargs
        assert call_kwargs["add"] is False
        assert call_kwargs["label"] == auto_fixer.REFIX_RUNNING_LABEL

    def test_action_mode_shares_counters_across_prs(self, mocker):
        """action モード: 複数 PR で同一カウンターセットが process_repo に渡される"""
        cfg = self._default_cfg()
        mocker.patch.object(
            sys, "argv", ["auto_fixer.py", "--action", "--repo", "owner/repo"]
        )
        mocker.patch("auto_fixer.load_dotenv")
        mocker.patch("auto_fixer.load_config_for_action", return_value=cfg)
        mocker.patch("auto_fixer._resolve_action_targets", return_value=[10, 20])
        mock_process_repo = mocker.patch("auto_fixer.process_repo", return_value=[])

        auto_fixer.main()

        assert mock_process_repo.call_count == 2
        call1_kwargs = mock_process_repo.call_args_list[0].kwargs
        call2_kwargs = mock_process_repo.call_args_list[1].kwargs
        for key in (
            "global_modified_prs",
            "global_committed_prs",
            "global_claude_prs",
            "global_coderabbit_resumed_prs",
        ):
            assert call1_kwargs[key] is call2_kwargs[key], (
                f"{key} は同一オブジェクトであるべき"
            )
