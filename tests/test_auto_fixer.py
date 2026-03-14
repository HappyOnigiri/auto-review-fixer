"""Unit tests for auto_fixer module."""

import sys
from pathlib import Path
from unittest.mock import ANY, Mock, patch

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
    def test_load_config_error_exits_with_error(self):
        with (
            patch.object(sys, "argv", ["auto_fixer.py"]),
            patch("auto_fixer.load_dotenv"),
            patch("auto_fixer.load_config", side_effect=SystemExit(1)),
        ):
            with pytest.raises(SystemExit) as exc_info:
                auto_fixer.main()
        assert exc_info.value.code == 1

    def test_main_passes_loaded_config_to_process_repo(self):
        cfg = {
            "models": {"summarize": "haiku", "fix": "sonnet"},
            "ci_log_max_lines": 120,
            "repositories": [
                {"repo": "owner/repo", "user_name": None, "user_email": None}
            ],
        }
        with (
            patch.object(sys, "argv", ["auto_fixer.py", "--config", "custom.yaml"]),
            patch("auto_fixer.load_dotenv"),
            patch("auto_fixer.load_config", return_value=cfg) as mock_load_config,
            patch("auto_fixer.process_repo", return_value=[]) as mock_process_repo,
        ):
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

    def test_main_prints_resumed_prs_before_commit_list(self, capsys):
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

        with (
            patch.object(sys, "argv", ["auto_fixer.py", "--config", "config.yaml"]),
            patch("auto_fixer.load_dotenv"),
            patch("auto_fixer.load_config", return_value=cfg),
            patch("auto_fixer.process_repo", side_effect=_process_repo_side_effect),
        ):
            auto_fixer.main()

        out = capsys.readouterr().out
        assert "CodeRabbit を再トリガした PR 一覧:" in out
        assert "  - owner/repo PR #123" in out
        assert "コミットを追加した PR 一覧:" in out
        assert out.index("CodeRabbit を再トリガした PR 一覧:") < out.index(
            "コミットを追加した PR 一覧:"
        )

    def test_main_skips_resumed_prs_section_when_empty(self, capsys):
        cfg = {
            "models": {"summarize": "haiku", "fix": "sonnet"},
            "ci_log_max_lines": 120,
            "repositories": [
                {"repo": "owner/repo", "user_name": None, "user_email": None}
            ],
        }
        with (
            patch.object(sys, "argv", ["auto_fixer.py", "--config", "config.yaml"]),
            patch("auto_fixer.load_dotenv"),
            patch("auto_fixer.load_config", return_value=cfg),
            patch("auto_fixer.process_repo", return_value=[]),
        ):
            auto_fixer.main()

        out = capsys.readouterr().out
        assert "CodeRabbit を再トリガした PR 一覧:" not in out

    def test_usage_limit_exits_nonzero_immediately(self, capsys):
        cfg = {
            "models": {"summarize": "haiku", "fix": "sonnet"},
            "ci_log_max_lines": 120,
            "repositories": [
                {"repo": "owner/repo", "user_name": None, "user_email": None}
            ],
        }
        with (
            patch.object(sys, "argv", ["auto_fixer.py", "--config", "config.yaml"]),
            patch("auto_fixer.load_dotenv"),
            patch("auto_fixer.load_config", return_value=cfg),
            patch(
                "auto_fixer.process_repo",
                side_effect=ClaudeUsageLimitError(
                    phase="review-fix",
                    returncode=1,
                    stdout="You've hit your limit",
                    stderr="",
                ),
            ),
        ):
            with pytest.raises(SystemExit) as exc_info:
                auto_fixer.main()

        assert exc_info.value.code == 1
        err = capsys.readouterr().err
        assert "Failing CI immediately" in err

    def test_claude_nonzero_exits_nonzero_immediately(self, capsys):
        cfg = {
            "models": {"summarize": "haiku", "fix": "sonnet"},
            "ci_log_max_lines": 120,
            "repositories": [
                {"repo": "owner/repo", "user_name": None, "user_email": None}
            ],
        }
        with (
            patch.object(sys, "argv", ["auto_fixer.py", "--config", "config.yaml"]),
            patch("auto_fixer.load_dotenv"),
            patch("auto_fixer.load_config", return_value=cfg),
            patch(
                "auto_fixer.process_repo",
                side_effect=ClaudeCommandFailedError(
                    phase="review-fix",
                    returncode=1,
                    stdout="API Error",
                    stderr="bad headers",
                ),
            ),
        ):
            with pytest.raises(SystemExit) as exc_info:
                auto_fixer.main()

        assert exc_info.value.code == 1
        err = capsys.readouterr().err
        assert "Failing CI immediately" in err
        assert "stdout: API Error" in err
        assert "stderr: bad headers" in err

    def test_empty_repos_exits_nonzero(self):
        cfg = {
            "models": {"summarize": "haiku", "fix": "sonnet"},
            "ci_log_max_lines": 120,
            "repositories": [],
        }
        with (
            patch.object(sys, "argv", ["auto_fixer.py", "--config", "config.yaml"]),
            patch("auto_fixer.load_dotenv"),
            patch("auto_fixer.load_config", return_value=cfg),
            patch("auto_fixer.expand_repositories", return_value=[]),
        ):
            with pytest.raises(SystemExit) as exc_info:
                auto_fixer.main()

        assert exc_info.value.code == 1

    def test_repo_error_exits_nonzero_with_summary(self, capsys):
        cfg = {
            "models": {"summarize": "haiku", "fix": "sonnet"},
            "ci_log_max_lines": 120,
            "repositories": [
                {"repo": "owner/repo", "user_name": None, "user_email": None}
            ],
        }
        with (
            patch.object(sys, "argv", ["auto_fixer.py", "--config", "config.yaml"]),
            patch("auto_fixer.load_dotenv"),
            patch("auto_fixer.load_config", return_value=cfg),
            patch(
                "auto_fixer.process_repo", side_effect=RuntimeError("connection error")
            ),
        ):
            with pytest.raises(SystemExit) as exc_info:
                auto_fixer.main()

        assert exc_info.value.code == 1
        out = capsys.readouterr().out
        assert "Error summary" in out


class TestProcessRepo:
    """Thin orchestration tests for process_repo(). All external deps mocked."""

    def test_empty_prs_returns_early(self, capsys):
        """No open PRs -> early return, no git/claude calls."""
        with (
            patch("auto_fixer.fetch_open_prs", return_value=[]),
            patch("auto_fixer.subprocess.run") as mock_run,
            patch("auto_fixer.subprocess.Popen") as mock_popen,
        ):
            auto_fixer.process_repo({"repo": "owner/repo"})
            out = capsys.readouterr().out
            assert "No open PRs found" in out
            mock_run.assert_not_called()
            mock_popen.assert_not_called()

    def test_auto_merge_enabled_backfills_merged_labels_even_without_open_prs(self):
        cfg = {
            "models": {"summarize": "haiku", "fix": "sonnet"},
            "ci_log_max_lines": 120,
            "auto_merge": True,
            "process_draft_prs": False,
            "repositories": [
                {"repo": "owner/repo", "user_name": None, "user_email": None}
            ],
        }
        with (
            patch("auto_fixer.fetch_open_prs", return_value=[]),
            patch("auto_fixer.backfill_merged_labels") as mock_backfill,
        ):
            auto_fixer.process_repo({"repo": "owner/repo"}, config=cfg)
        mock_backfill.assert_called_once_with(
            "owner/repo",
            limit=100,
            enabled_pr_label_keys={"running", "done", "merged", "auto_merge_requested"},
            error_collector=None,
        )

    def test_draft_pr_is_skipped_by_default(self):
        prs = [{"number": 1, "title": "Draft PR", "isDraft": True}]
        with (
            patch("auto_fixer.fetch_open_prs", return_value=prs),
            patch("auto_fixer.fetch_pr_details") as mock_fetch_pr_details,
        ):
            auto_fixer.process_repo({"repo": "owner/repo"})

        mock_fetch_pr_details.assert_not_called()

    def test_draft_pr_is_processed_when_enabled(self):
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
        with (
            patch("auto_fixer.fetch_open_prs", return_value=prs),
            patch(
                "auto_fixer.fetch_pr_details", return_value=pr_data
            ) as mock_fetch_pr_details,
            patch("auto_fixer.fetch_pr_review_comments", return_value=[]),
            patch("auto_fixer.fetch_review_threads", return_value={}),
            patch("auto_fixer.fetch_issue_comments", return_value=[]),
            patch("auto_fixer.get_branch_compare_status", return_value=("ahead", 0)),
            patch("auto_fixer.load_state_comment", return_value=make_state_comment()),
            patch(
                "auto_fixer.update_done_label_if_completed",
                return_value=(False, False),
            ),
        ):
            auto_fixer.process_repo(
                {"repo": "owner/repo"},
                config=cfg,
                global_modified_prs=set(),
                global_committed_prs=set(),
                global_claude_prs=set(),
            )

        mock_fetch_pr_details.assert_called_once_with("owner/repo", 1)

    def test_dry_run_no_external_commands(self, tmp_path, capsys):
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
        with (
            patch("auto_fixer.fetch_open_prs", return_value=prs),
            patch("auto_fixer.fetch_pr_details", return_value=pr_data),
            patch("auto_fixer.fetch_pr_review_comments", return_value=[]),
            patch("auto_fixer.fetch_review_threads", return_value={}),
            patch("auto_fixer.fetch_issue_comments", return_value=[]),
            patch("auto_fixer.get_branch_compare_status", return_value=("ahead", 0)),
            patch("auto_fixer.load_state_comment", return_value=make_state_comment()),
            patch("auto_fixer.prepare_repository", return_value=tmp_path),
            patch("auto_fixer.summarize_reviews") as mock_summarize,
            patch("auto_fixer.subprocess.Popen") as mock_popen,
            patch("auto_fixer.upsert_state_comment"),
            patch("auto_fixer.resolve_review_thread"),
        ):
            auto_fixer.process_repo({"repo": "owner/repo"}, dry_run=True)
            mock_summarize.assert_not_called()
            mock_popen.assert_not_called()
            out = capsys.readouterr().out
            assert "[DRY RUN]" in out
            assert "follow only the top-level <instructions> section" in out

    def test_processes_multiple_targets_in_single_claude_run(self, tmp_path):
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
                return Mock(returncode=0, stdout="abc123\n", stderr="")
            if (
                cmd[:4] == ["git", "log", "--oneline", "--first-parent"]
                and cmd[4] == "abc123..HEAD"
            ) or (cmd[:3] == ["git", "log", "--oneline"] and cmd[3] == "abc123..HEAD"):
                return Mock(returncode=0, stdout="deadbee fix\n", stderr="")
            if cmd == ["git", "status", "--porcelain"]:
                return Mock(returncode=0, stdout="", stderr="")
            if cmd[:3] == ["git", "push", "origin"]:
                return Mock(returncode=0, stdout="", stderr="")
            if cmd == ["git", "log", "origin/feature..HEAD", "--oneline"]:
                return Mock(returncode=0, stdout="", stderr="")
            raise AssertionError(f"Unexpected subprocess.run call: {cmd}")

        process_mock = Mock(returncode=0)
        process_mock.communicate.return_value = ("ok", "")

        captured_prompts: list[str] = []

        def popen_side_effect(cmd, **kwargs):
            cwd = kwargs.get("cwd", "")
            if cwd:
                pf = Path(cwd) / "_review_prompt.md"
                if pf.exists():
                    captured_prompts.append(pf.read_text())
            return process_mock

        with (
            patch("auto_fixer.fetch_open_prs", return_value=prs),
            patch("auto_fixer.fetch_pr_details", return_value=pr_data),
            patch("auto_fixer.fetch_pr_review_comments", return_value=review_comments),
            patch("auto_fixer.fetch_review_threads", return_value=thread_map),
            patch("auto_fixer.fetch_issue_comments", return_value=[]),
            patch("auto_fixer.get_branch_compare_status", return_value=("ahead", 0)),
            patch("auto_fixer.load_state_comment", return_value=make_state_comment()),
            patch("auto_fixer.prepare_repository", return_value=tmp_path),
            patch(
                "auto_fixer.summarize_reviews",
                return_value={
                    "r1": "review summary",
                    "discussion_r10": "comment summary",
                },
            ),
            patch("auto_fixer.subprocess.run", side_effect=_run_side_effect),
            patch(
                "auto_fixer.subprocess.Popen", side_effect=popen_side_effect
            ) as mock_popen,
            patch("auto_fixer.set_pr_running_label"),
            patch("auto_fixer.upsert_state_comment") as mock_upsert_state_comment,
            patch(
                "auto_fixer.resolve_review_thread", return_value=True
            ) as mock_resolve_thread,
        ):
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

    def test_ci_fix_runs_before_merge_and_review_fix(self, tmp_path):
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
                return Mock(returncode=0, stdout="", stderr="")
            if cmd[:3] == ["git", "push", "origin"]:
                return Mock(returncode=0, stdout="", stderr="")
            if cmd == ["git", "log", "origin/feature..HEAD", "--oneline"]:
                return Mock(returncode=0, stdout="", stderr="")
            raise AssertionError(f"Unexpected subprocess.run call: {cmd}")

        with (
            patch("auto_fixer.fetch_open_prs", return_value=prs),
            patch("auto_fixer.fetch_pr_details", return_value=pr_data),
            patch("auto_fixer.fetch_pr_review_comments", return_value=[]),
            patch("auto_fixer.fetch_review_threads", return_value={}),
            patch("auto_fixer.fetch_issue_comments", return_value=[]),
            patch("auto_fixer.get_branch_compare_status", return_value=("behind", 1)),
            patch("auto_fixer.load_state_comment", return_value=make_state_comment()),
            patch("auto_fixer.prepare_repository", return_value=tmp_path),
            patch("auto_fixer.collect_ci_failure_materials", return_value=[]),
            patch("auto_fixer.merge_base_branch", side_effect=merge_side_effect),
            patch(
                "auto_fixer.summarize_reviews", return_value={"r1": "review summary"}
            ),
            patch("auto_fixer.run_claude_prompt", side_effect=run_claude_side_effect),
            patch("auto_fixer.set_pr_running_label"),
            patch("auto_fixer.subprocess.run", side_effect=run_side_effect),
            patch("auto_fixer.upsert_state_comment") as mock_upsert_state_comment,
        ):
            auto_fixer.process_repo({"repo": "owner/repo"})

        assert call_order == ["ci-fix", "merge-base", "review-fix"]
        mock_upsert_state_comment.assert_called_once()
        args = mock_upsert_state_comment.call_args.args
        assert args[:2] == ("owner/repo", 1)
        assert [(entry.comment_id, entry.url) for entry in args[2]] == [
            ("r1", "https://github.com/owner/repo/pull/1#discussion_r1"),
        ]

    def test_ci_only_path_when_no_reviews_and_not_behind(self, tmp_path):
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

        with (
            patch("auto_fixer.fetch_open_prs", return_value=prs),
            patch("auto_fixer.fetch_pr_details", return_value=pr_data),
            patch("auto_fixer.fetch_pr_review_comments", return_value=[]),
            patch("auto_fixer.fetch_review_threads", return_value={}),
            patch("auto_fixer.fetch_issue_comments", return_value=[]),
            patch("auto_fixer.get_branch_compare_status", return_value=("ahead", 0)),
            patch("auto_fixer.load_state_comment", return_value=make_state_comment()),
            patch("auto_fixer.prepare_repository", return_value=tmp_path),
            patch("auto_fixer.collect_ci_failure_materials", return_value=[]),
            patch("auto_fixer.run_claude_prompt", side_effect=run_claude_side_effect),
            patch(
                "auto_fixer.subprocess.run",
                return_value=Mock(returncode=0, stdout="", stderr=""),
            ),
            patch("auto_fixer.upsert_state_comment") as mock_upsert_state_comment,
        ):
            auto_fixer.process_repo({"repo": "owner/repo"})

        assert call_order == ["ci-fix"]
        # write_result_to_comment defaults to True, so the result log is written to state comment
        mock_upsert_state_comment.assert_called_once()

    def test_ci_only_path_records_result_log_in_state_comment_when_enabled(
        self, tmp_path
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

        with (
            patch("auto_fixer.fetch_open_prs", return_value=prs),
            patch("auto_fixer.fetch_pr_details", return_value=pr_data),
            patch("auto_fixer.fetch_pr_review_comments", return_value=[]),
            patch("auto_fixer.fetch_review_threads", return_value={}),
            patch("auto_fixer.fetch_issue_comments", return_value=[]),
            patch("auto_fixer.get_branch_compare_status", return_value=("ahead", 0)),
            patch("auto_fixer.load_state_comment", return_value=make_state_comment()),
            patch("auto_fixer.prepare_repository", return_value=tmp_path),
            patch("auto_fixer.collect_ci_failure_materials", return_value=[]),
            patch(
                "auto_fixer.run_claude_prompt",
                return_value=("aaa111 ci fix", "CI stdout output"),
            ),
            patch(
                "auto_fixer.subprocess.run",
                return_value=Mock(returncode=0, stdout="", stderr=""),
            ),
            patch("auto_fixer.upsert_state_comment") as mock_upsert,
            patch(
                "auto_fixer.update_done_label_if_completed",
                return_value=(False, False),
            ),
        ):
            auto_fixer.process_repo({"repo": "owner/repo"}, config=cfg)

        # CI-only パスでは upsert_state_comment で result_log_body が保存される
        mock_upsert.assert_called_once()
        call_kwargs = mock_upsert.call_args.kwargs
        assert "result_log_body" in call_kwargs
        assert "CI stdout output" in call_kwargs["result_log_body"]

    def test_rate_limit_skips_review_fix_but_runs_ci_and_merge_base(self, tmp_path):
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

        with (
            patch("auto_fixer.fetch_open_prs", return_value=prs),
            patch("auto_fixer.fetch_pr_details", return_value=pr_data),
            patch("auto_fixer.fetch_pr_review_comments", return_value=[]),
            patch("auto_fixer.fetch_review_threads", return_value={}),
            patch("auto_fixer.fetch_issue_comments", return_value=issue_comments),
            patch("auto_fixer.get_branch_compare_status", return_value=("behind", 1)),
            patch("auto_fixer.load_state_comment", return_value=make_state_comment()),
            patch("auto_fixer.prepare_repository", return_value=tmp_path),
            patch("auto_fixer.collect_ci_failure_materials", return_value=[]),
            patch("auto_fixer.merge_base_branch", side_effect=merge_side_effect),
            patch("auto_fixer.run_claude_prompt", side_effect=run_claude_side_effect),
            patch("auto_fixer.set_pr_running_label"),
            patch(
                "auto_fixer.subprocess.run",
                return_value=Mock(returncode=0, stdout="", stderr=""),
            ),
            patch(
                "auto_fixer.update_done_label_if_completed",
                return_value=(False, False),
            ) as mock_update_done,
            patch("auto_fixer.summarize_reviews") as mock_summarize,
        ):
            auto_fixer.process_repo({"repo": "owner/repo"})

        assert call_order == ["ci-fix", "merge-base"]
        mock_summarize.assert_not_called()
        assert mock_update_done.call_args.kwargs["coderabbit_rate_limit_active"] is True

    def test_review_failed_auto_resume_counts_toward_per_run_limit(self):
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

        with (
            patch("auto_fixer.fetch_open_prs", return_value=prs),
            patch("auto_fixer.fetch_pr_details", return_value=pr_data),
            patch("auto_fixer.fetch_pr_review_comments", return_value=[]),
            patch("auto_fixer.fetch_review_threads", return_value={}),
            patch(
                "auto_fixer.fetch_issue_comments",
                side_effect=lambda _repo, pr_number: issue_comments_by_pr[pr_number],
            ),
            patch("auto_fixer.get_branch_compare_status", return_value=("ahead", 0)),
            patch("auto_fixer.load_state_comment", return_value=make_state_comment()),
            patch("auto_fixer.set_pr_running_label"),
            patch(
                "auto_fixer.update_done_label_if_completed",
                return_value=(False, False),
            ),
            patch(
                "coderabbit._post_issue_comment", return_value=True
            ) as mock_post_issue_comment,
        ):
            auto_fixer.process_repo(
                {"repo": "owner/repo"},
                config=cfg,
                global_coderabbit_resumed_prs=global_resumed_prs,
                auto_resume_run_state=auto_resume_run_state,
            )

        assert mock_post_issue_comment.call_count == 1
        assert auto_resume_run_state["posted"] == 1
        assert len(global_resumed_prs) == 1

    def test_review_skipped_draft_detected_triggers_single_review(self):
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
        with (
            patch("auto_fixer.fetch_open_prs", return_value=prs),
            patch("auto_fixer.fetch_pr_details", return_value=pr_data),
            patch("auto_fixer.fetch_pr_review_comments", return_value=[]),
            patch("auto_fixer.fetch_review_threads", return_value={}),
            patch(
                "auto_fixer.fetch_issue_comments",
                return_value=[
                    {
                        "id": 111,
                        "body": _REVIEW_SKIPPED_DRAFT_BODY,
                        "user": {"login": "coderabbitai[bot]"},
                        "updated_at": "2026-03-11T12:00:00Z",
                    }
                ],
            ),
            patch("auto_fixer.get_branch_compare_status", return_value=("ahead", 0)),
            patch("auto_fixer.load_state_comment", return_value=make_state_comment()),
            patch("auto_fixer.set_pr_running_label"),
            patch(
                "auto_fixer.update_done_label_if_completed",
                return_value=(False, False),
            ) as mock_update_done,
            patch("coderabbit._post_issue_comment", return_value=True) as mock_post,
        ):
            auto_fixer.process_repo({"repo": "owner/repo"}, config=cfg)

        mock_post.assert_called_once_with(
            "owner/repo", 1, "@coderabbitai review", error_collector=None
        )
        assert (
            mock_update_done.call_args.kwargs["coderabbit_review_skipped_active"] is True
        )

    def test_review_skipped_draft_detected_does_not_trigger_while_pr_is_draft(self):
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
        with (
            patch("auto_fixer.fetch_open_prs", return_value=prs),
            patch("auto_fixer.fetch_pr_details", return_value=pr_data),
            patch("auto_fixer.fetch_pr_review_comments", return_value=[]),
            patch("auto_fixer.fetch_review_threads", return_value={}),
            patch(
                "auto_fixer.fetch_issue_comments",
                return_value=[
                    {
                        "id": 111,
                        "body": _REVIEW_SKIPPED_DRAFT_BODY,
                        "user": {"login": "coderabbitai[bot]"},
                        "updated_at": "2026-03-11T12:00:00Z",
                    }
                ],
            ),
            patch("auto_fixer.get_branch_compare_status", return_value=("ahead", 0)),
            patch("auto_fixer.load_state_comment", return_value=make_state_comment()),
            patch("auto_fixer.set_pr_running_label"),
            patch(
                "auto_fixer.update_done_label_if_completed",
                return_value=(False, False),
            ) as mock_update_done,
            patch("coderabbit._post_issue_comment") as mock_post,
        ):
            auto_fixer.process_repo({"repo": "owner/repo"}, config=cfg)

        mock_post.assert_not_called()
        assert (
            mock_update_done.call_args.kwargs["coderabbit_review_skipped_active"] is True
        )

    def test_summarize_only_stops_before_fix_and_state_update(self, tmp_path, capsys):
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
        with (
            patch("auto_fixer.fetch_open_prs", return_value=prs),
            patch("auto_fixer.fetch_pr_details", return_value=pr_data),
            patch("auto_fixer.fetch_pr_review_comments", return_value=[]),
            patch("auto_fixer.fetch_review_threads", return_value={}),
            patch("auto_fixer.fetch_issue_comments", return_value=[]),
            patch("auto_fixer.get_branch_compare_status", return_value=("ahead", 0)),
            patch("auto_fixer.load_state_comment", return_value=make_state_comment()),
            patch("auto_fixer.prepare_repository", return_value=tmp_path),
            patch("auto_fixer.summarize_reviews", return_value={"r1": "summary"}),
            patch("auto_fixer.subprocess.Popen") as mock_popen,
            patch("auto_fixer.upsert_state_comment"),
        ):
            auto_fixer.process_repo({"repo": "owner/repo"}, summarize_only=True)
            mock_popen.assert_not_called()
            out = capsys.readouterr().out
            assert "Summarize-only mode" in out

    def test_summarize_only_reports_raw_text_fallback(self, capsys):
        prs = [{"number": 1, "title": "Test"}]
        pr_data = {
            "headRefName": "feature",
            "baseRefName": "main",
            "title": "Test",
            "reviews": [
                {"id": "r1", "body": "fix", "author": {"login": "coderabbitai"}}
            ],
        }
        with (
            patch("auto_fixer.fetch_open_prs", return_value=prs),
            patch("auto_fixer.fetch_pr_details", return_value=pr_data),
            patch("auto_fixer.fetch_pr_review_comments", return_value=[]),
            patch("auto_fixer.fetch_review_threads", return_value={}),
            patch("auto_fixer.fetch_issue_comments", return_value=[]),
            patch("auto_fixer.get_branch_compare_status", return_value=("ahead", 0)),
            patch("auto_fixer.load_state_comment", return_value=make_state_comment()),
            patch("auto_fixer.summarize_reviews", return_value={}),
            patch("auto_fixer.subprocess.Popen") as mock_popen,
            patch("auto_fixer.upsert_state_comment"),
        ):
            auto_fixer.process_repo({"repo": "owner/repo"}, summarize_only=True)
            mock_popen.assert_not_called()
            out = capsys.readouterr().out
            assert "falling back to raw review text for all 1 item(s)" in out

    def test_summarize_only_usage_limit_raises(self):
        prs = [{"number": 1, "title": "Test"}]
        pr_data = {
            "headRefName": "feature",
            "baseRefName": "main",
            "title": "Test",
            "reviews": [
                {"id": "r1", "body": "fix", "author": {"login": "coderabbitai"}}
            ],
        }
        with (
            patch("auto_fixer.fetch_open_prs", return_value=prs),
            patch("auto_fixer.fetch_pr_details", return_value=pr_data),
            patch("auto_fixer.fetch_pr_review_comments", return_value=[]),
            patch("auto_fixer.fetch_review_threads", return_value={}),
            patch("auto_fixer.fetch_issue_comments", return_value=[]),
            patch("auto_fixer.get_branch_compare_status", return_value=("ahead", 0)),
            patch("auto_fixer.load_state_comment", return_value=make_state_comment()),
            patch(
                "auto_fixer.summarize_reviews",
                side_effect=ClaudeUsageLimitError(
                    phase="summarization",
                    returncode=1,
                    stdout="You've hit your limit",
                    stderr="",
                ),
            ),
        ):
            with pytest.raises(ClaudeUsageLimitError):
                auto_fixer.process_repo({"repo": "owner/repo"}, summarize_only=True)

    def test_behind_merge_runs_push_no_claude(self, tmp_path, capsys):
        """behind PR with no review targets -> merge runs, push happens, no Claude called."""
        prs = [{"number": 1, "title": "Test"}]
        pr_data = {
            "headRefName": "feature/test",
            "baseRefName": "main",
            "title": "Test",
            "reviews": [],
        }
        with (
            patch("auto_fixer.fetch_open_prs", return_value=prs),
            patch("auto_fixer.fetch_pr_details", return_value=pr_data),
            patch("auto_fixer.fetch_pr_review_comments", return_value=[]),
            patch("auto_fixer.fetch_review_threads", return_value={}),
            patch("auto_fixer.fetch_issue_comments", return_value=[]),
            patch("auto_fixer.get_branch_compare_status", return_value=("behind", 0)),
            patch("auto_fixer.load_state_comment", return_value=make_state_comment()),
            patch("auto_fixer.prepare_repository", return_value=tmp_path),
            patch("auto_fixer.merge_base_branch", return_value=(True, False)),
            patch("auto_fixer.subprocess.run") as mock_run,
            patch("auto_fixer.subprocess.Popen") as mock_popen,
            patch("auto_fixer.upsert_state_comment"),
        ):
            mock_run.return_value = Mock(
                returncode=0, stdout="abc1234 Merge main\n", stderr=""
            )
            result = auto_fixer.process_repo({"repo": "owner/repo"})
            mock_popen.assert_not_called()
            push_calls = [
                c for c in mock_run.call_args_list if c.args and "push" in c.args[0]
            ]
            assert push_calls, "git push should be called after clean merge"
            assert result, "should report the merge commit in commits_added_to"
            out = capsys.readouterr().out
            assert "behind" in out.lower()

    def test_done_label_does_not_skip_processing_when_behind(self, tmp_path):
        prs = [{"number": 1, "title": "Test", "labels": [{"name": "refix: done"}]}]
        pr_data = {
            "headRefName": "feature/test",
            "baseRefName": "main",
            "title": "Test",
            "reviews": [],
            "comments": [],
        }
        with (
            patch("auto_fixer.fetch_open_prs", return_value=prs),
            patch("auto_fixer.fetch_pr_details", return_value=pr_data),
            patch("auto_fixer.fetch_pr_review_comments", return_value=[]),
            patch("auto_fixer.fetch_review_threads", return_value={}),
            patch("auto_fixer.fetch_issue_comments", return_value=[]),
            patch("auto_fixer.get_branch_compare_status", return_value=("behind", 1)),
            patch("auto_fixer.load_state_comment", return_value=make_state_comment()),
            patch(
                "auto_fixer.prepare_repository", return_value=tmp_path
            ) as mock_prepare,
            patch("auto_fixer.merge_base_branch", return_value=(False, False)),
            patch(
                "auto_fixer.update_done_label_if_completed",
                return_value=(False, False),
            ),
        ):
            auto_fixer.process_repo({"repo": "owner/repo"})

        mock_prepare.assert_called_once()

    def test_review_fix_start_sets_running_label(self, tmp_path):
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
        with (
            patch("auto_fixer.fetch_open_prs", return_value=prs),
            patch("auto_fixer.fetch_pr_details", return_value=pr_data),
            patch("auto_fixer.fetch_pr_review_comments", return_value=[]),
            patch("auto_fixer.fetch_review_threads", return_value={}),
            patch("auto_fixer.fetch_issue_comments", return_value=[]),
            patch("auto_fixer.get_branch_compare_status", return_value=("ahead", 0)),
            patch("auto_fixer.load_state_comment", return_value=make_state_comment()),
            patch("auto_fixer.prepare_repository", return_value=tmp_path),
            patch("auto_fixer.summarize_reviews", return_value={"r1": "summary"}),
            patch("auto_fixer.run_claude_prompt", return_value=("", "")),
            patch("auto_fixer.set_pr_running_label") as mock_set_running,
            patch(
                "auto_fixer.update_done_label_if_completed",
                return_value=(False, False),
            ),
            patch("auto_fixer.upsert_state_comment"),
            patch(
                "auto_fixer.subprocess.run",
                return_value=Mock(returncode=0, stdout="", stderr=""),
            ),
        ):
            auto_fixer.process_repo({"repo": "owner/repo"})

        mock_set_running.assert_called_once_with(
            "owner/repo",
            1,
            pr_data=pr_data,
            enabled_pr_label_keys={"running", "done", "merged", "auto_merge_requested"},
        )

    def test_process_repo_passes_state_comment_timezone_to_create_state_entry(
        self, tmp_path
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
                return Mock(returncode=0, stdout="", stderr="")
            if cmd == ["git", "log", "origin/feature..HEAD", "--oneline"]:
                return Mock(returncode=0, stdout="", stderr="")
            return Mock(returncode=0, stdout="", stderr="")

        with (
            patch("auto_fixer.fetch_open_prs", return_value=prs),
            patch("auto_fixer.fetch_pr_details", return_value=pr_data),
            patch("auto_fixer.fetch_pr_review_comments", return_value=[]),
            patch("auto_fixer.fetch_review_threads", return_value={}),
            patch("auto_fixer.fetch_issue_comments", return_value=[]),
            patch("auto_fixer.get_branch_compare_status", return_value=("ahead", 0)),
            patch("auto_fixer.load_state_comment", return_value=make_state_comment()),
            patch("auto_fixer.prepare_repository", return_value=tmp_path),
            patch("auto_fixer.summarize_reviews", return_value={"r1": "summary"}),
            patch("auto_fixer.run_claude_prompt", return_value=("", "")),
            patch("auto_fixer.set_pr_running_label"),
            patch(
                "auto_fixer.update_done_label_if_completed",
                return_value=(False, False),
            ),
            patch("auto_fixer.upsert_state_comment"),
            patch("auto_fixer.subprocess.run", side_effect=_run_side_effect),
            patch(
                "auto_fixer.create_state_entry",
                side_effect=_create_state_entry_side_effect,
            ),
        ):
            auto_fixer.process_repo({"repo": "owner/repo"}, config=cfg)

        assert captured_timezones == ["UTC"]

    def test_fetch_open_prs_failure_records_in_error_collector(self):
        """fetch_open_prs 失敗時に error_collector にエラーが記録される。"""
        from error_collector import ErrorCollector

        collector = ErrorCollector()
        with patch(
            "auto_fixer.fetch_open_prs", side_effect=RuntimeError("network error")
        ):
            auto_fixer.process_repo({"repo": "owner/repo"}, error_collector=collector)

        assert collector.has_errors
        assert any("owner/repo" == r.scope for r in collector._errors)
        assert any("Failed to fetch PRs" in r.message for r in collector._errors)

    def test_pr_exception_records_in_error_collector(self):
        """PR ループ内でエラー時に error_collector にエラーが記録される。"""
        from error_collector import ErrorCollector

        prs = [{"number": 1, "title": "PR #1", "isDraft": False}]
        collector = ErrorCollector()
        with (
            patch("auto_fixer.fetch_open_prs", return_value=prs),
            patch(
                "auto_fixer.fetch_pr_details",
                side_effect=RuntimeError("API error"),
            ),
        ):
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

    def test_max_modified_prs_skips_after_limit(self, capsys):
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
        with (
            patch("auto_fixer.fetch_open_prs", return_value=prs),
            patch(
                "auto_fixer.fetch_pr_details",
                side_effect=[
                    self._make_pr_data(1),
                    self._make_pr_data(2),
                ],
            ),
            patch("auto_fixer.fetch_pr_review_comments", return_value=[]),
            patch("auto_fixer.fetch_review_threads", return_value={}),
            patch("auto_fixer.fetch_issue_comments", return_value=[]),
            patch("auto_fixer.get_branch_compare_status", return_value=("ahead", 0)),
            patch("auto_fixer.load_state_comment", return_value=make_state_comment()),
            patch(
                "auto_fixer.update_done_label_if_completed", return_value=(True, False)
            ),
        ):
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

    def test_max_committed_prs_skips_claude_and_push(self, capsys, tmp_path):
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

        with (
            patch("auto_fixer.fetch_open_prs", return_value=prs),
            patch("auto_fixer.fetch_pr_details", side_effect=[pr_data_1, pr_data_2]),
            patch("auto_fixer.fetch_pr_review_comments", return_value=[]),
            patch("auto_fixer.fetch_review_threads", return_value={}),
            patch("auto_fixer.fetch_issue_comments", return_value=[]),
            patch("auto_fixer.get_branch_compare_status", return_value=("ahead", 0)),
            patch("auto_fixer.load_state_comment", return_value=make_state_comment()),
            patch("auto_fixer.prepare_repository", return_value=works_dir),
            patch("auto_fixer.summarize_reviews", return_value={}),
            patch(
                "auto_fixer.run_claude_prompt",
                return_value=("abc123 review fix", "review stdout"),
            ) as mock_claude,
            patch("auto_fixer.set_pr_running_label"),
            patch("auto_fixer.edit_pr_label"),
            patch("auto_fixer.upsert_state_comment"),
            patch(
                "auto_fixer.update_done_label_if_completed",
                return_value=(False, False),
            ),
            patch("auto_fixer.subprocess.run", side_effect=mock_run_side_effect),
            patch("auto_fixer.subprocess.Popen", return_value=mock_popen),
        ):
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

    def test_exclude_authors_exact_match_skips_pr(self, capsys):
        prs = [self._make_pr(1, author_login="renovate-bot")]
        cfg = {
            "models": {"summarize": "haiku", "fix": "sonnet"},
            "ci_log_max_lines": 120,
            "exclude_authors": ["renovate-bot"],
            "repositories": [
                {"repo": "owner/repo", "user_name": None, "user_email": None}
            ],
        }
        with (
            patch("auto_fixer.fetch_open_prs", return_value=prs),
            patch("auto_fixer.fetch_pr_details") as mock_fetch,
        ):
            auto_fixer.process_repo({"repo": "owner/repo"}, config=cfg)

        mock_fetch.assert_not_called()
        assert "exclude_authors" in capsys.readouterr().out

    def test_exclude_authors_wildcard_matches(self, capsys):
        prs = [self._make_pr(1, author_login="dependabot-app")]
        cfg = {
            "models": {"summarize": "haiku", "fix": "sonnet"},
            "ci_log_max_lines": 120,
            "exclude_authors": ["dependabot*"],
            "repositories": [
                {"repo": "owner/repo", "user_name": None, "user_email": None}
            ],
        }
        with (
            patch("auto_fixer.fetch_open_prs", return_value=prs),
            patch("auto_fixer.fetch_pr_details") as mock_fetch,
        ):
            auto_fixer.process_repo({"repo": "owner/repo"}, config=cfg)

        mock_fetch.assert_not_called()
        assert "exclude_authors" in capsys.readouterr().out

    def test_exclude_labels_exact_match_skips_pr(self, capsys):
        prs = [self._make_pr(1, labels=["do-not-merge"])]
        cfg = {
            "models": {"summarize": "haiku", "fix": "sonnet"},
            "ci_log_max_lines": 120,
            "exclude_labels": ["do-not-merge"],
            "repositories": [
                {"repo": "owner/repo", "user_name": None, "user_email": None}
            ],
        }
        with (
            patch("auto_fixer.fetch_open_prs", return_value=prs),
            patch("auto_fixer.fetch_pr_details") as mock_fetch,
        ):
            auto_fixer.process_repo({"repo": "owner/repo"}, config=cfg)

        mock_fetch.assert_not_called()
        assert "exclude_labels" in capsys.readouterr().out

    def test_exclude_labels_wildcard_matches(self, capsys):
        prs = [self._make_pr(1, labels=["autorelease: tagged"])]
        cfg = {
            "models": {"summarize": "haiku", "fix": "sonnet"},
            "ci_log_max_lines": 120,
            "exclude_labels": ["autorelease: *"],
            "repositories": [
                {"repo": "owner/repo", "user_name": None, "user_email": None}
            ],
        }
        with (
            patch("auto_fixer.fetch_open_prs", return_value=prs),
            patch("auto_fixer.fetch_pr_details") as mock_fetch,
        ):
            auto_fixer.process_repo({"repo": "owner/repo"}, config=cfg)

        mock_fetch.assert_not_called()
        assert "exclude_labels" in capsys.readouterr().out

    def test_no_match_processes_normally(self):
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
        with (
            patch("auto_fixer.fetch_open_prs", return_value=prs),
            patch("auto_fixer.fetch_pr_details", return_value=pr_data) as mock_fetch,
            patch("auto_fixer.fetch_pr_review_comments", return_value=[]),
            patch("auto_fixer.fetch_review_threads", return_value={}),
            patch("auto_fixer.fetch_issue_comments", return_value=[]),
            patch("auto_fixer.get_branch_compare_status", return_value=("ahead", 0)),
            patch("auto_fixer.load_state_comment", return_value=make_state_comment()),
            patch(
                "auto_fixer.update_done_label_if_completed", return_value=(False, False)
            ),
        ):
            auto_fixer.process_repo({"repo": "owner/repo"}, config=cfg)

        mock_fetch.assert_called_once()


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

    def test_load_state_comment_failure_records_error(self):
        ec = ErrorCollector()
        with (
            patch("auto_fixer.fetch_open_prs", return_value=[self._PR]),
            patch("auto_fixer.fetch_pr_details", return_value=self._PR_DATA),
            patch(
                "auto_fixer.load_state_comment",
                side_effect=RuntimeError("network error"),
            ),
        ):
            auto_fixer.process_repo({"repo": "owner/repo"}, error_collector=ec)

        assert ec.has_errors
        assert any("owner/repo#1" == r.scope for r in ec._errors)
        assert any("Failed to load state comment" in r.message for r in ec._errors)

    def test_fetch_review_comments_failure_records_error(self):
        ec = ErrorCollector()
        with (
            patch("auto_fixer.fetch_open_prs", return_value=[self._PR]),
            patch("auto_fixer.fetch_pr_details", return_value=self._PR_DATA),
            patch("auto_fixer.load_state_comment", return_value=make_state_comment()),
            patch(
                "auto_fixer.fetch_pr_review_comments",
                side_effect=RuntimeError("fetch failed"),
            ),
        ):
            auto_fixer.process_repo({"repo": "owner/repo"}, error_collector=ec)

        assert ec.has_errors
        assert any("owner/repo#1" == r.scope for r in ec._errors)
        assert any("Failed to fetch review comments" in r.message for r in ec._errors)

    def test_fetch_review_threads_failure_records_error(self):
        ec = ErrorCollector()
        with (
            patch("auto_fixer.fetch_open_prs", return_value=[self._PR]),
            patch("auto_fixer.fetch_pr_details", return_value=self._PR_DATA),
            patch("auto_fixer.load_state_comment", return_value=make_state_comment()),
            patch("auto_fixer.fetch_pr_review_comments", return_value=[]),
            patch(
                "auto_fixer.fetch_review_threads",
                side_effect=RuntimeError("threads failed"),
            ),
        ):
            auto_fixer.process_repo({"repo": "owner/repo"}, error_collector=ec)

        assert ec.has_errors
        assert any("owner/repo#1" == r.scope for r in ec._errors)
        assert any("Failed to fetch review threads" in r.message for r in ec._errors)

    def test_fetch_issue_comments_failure_records_error(self):
        ec = ErrorCollector()
        with (
            patch("auto_fixer.fetch_open_prs", return_value=[self._PR]),
            patch("auto_fixer.fetch_pr_details", return_value=self._PR_DATA),
            patch("auto_fixer.load_state_comment", return_value=make_state_comment()),
            patch("auto_fixer.fetch_pr_review_comments", return_value=[]),
            patch("auto_fixer.fetch_review_threads", return_value={}),
            patch(
                "auto_fixer.fetch_issue_comments",
                side_effect=RuntimeError("issue comments failed"),
            ),
        ):
            auto_fixer.process_repo({"repo": "owner/repo"}, error_collector=ec)

        assert ec.has_errors
        assert any("owner/repo#1" == r.scope for r in ec._errors)
        assert any("Failed to fetch issue comments" in r.message for r in ec._errors)
