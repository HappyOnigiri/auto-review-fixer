"""Unit tests for PR labeling helpers."""

import json
from unittest.mock import call


import coderabbit
import pr_label
from error_collector import ErrorCollector
from state_manager import StateComment
from subprocess_helpers import SubprocessError
from type_defs import PRData


class TestRefixLabeling:
    def test_ensure_repo_label_exists_creates_when_missing(
        self, mocker, make_cmd_result
    ):
        mock_run = mocker.patch(
            "pr_label.run_command",
            side_effect=[
                make_cmd_result("", returncode=1, stderr="404 Not Found"),
                make_cmd_result('{"name":"refix: running"}'),
            ],
        )
        ok = pr_label._ensure_repo_label_exists(
            "owner/repo",
            "refix: running",
            color="FBCA04",
            description="running label",
        )

        assert ok is True
        assert mock_run.call_count == 2
        create_call = mock_run.call_args_list[1].args[0]
        assert create_call[:3] == ["gh", "api", "repos/owner/repo/labels"]
        assert "name=refix: running" in create_call

    def test_set_pr_running_label_ensures_labels_before_edit(self, mocker):
        mock_ensure = mocker.patch("pr_label._ensure_refix_labels")
        mock_edit = mocker.patch("pr_label.edit_pr_label", return_value=True)
        pr_label.set_pr_running_label("owner/repo", 9)

        mock_ensure.assert_called_once_with("owner/repo", error_collector=None)
        mock_edit.assert_has_calls(
            [
                call(
                    "owner/repo",
                    9,
                    add=False,
                    label="refix: done",
                    error_collector=None,
                ),
                call(
                    "owner/repo",
                    9,
                    add=True,
                    label="refix: running",
                    error_collector=None,
                ),
            ]
        )

    def test_set_pr_running_label_skips_edit_when_already_has_running(self, mocker):
        """When PR already has refix: running and no refix: done, skip gh pr edit to avoid updating PR."""
        pr_data: PRData = {"labels": [{"name": "refix: running"}]}
        mock_ensure = mocker.patch("pr_label._ensure_refix_labels")
        mock_edit = mocker.patch("pr_label.edit_pr_label")
        pr_label.set_pr_running_label("owner/repo", 9, pr_data=pr_data)

        mock_ensure.assert_not_called()
        mock_edit.assert_not_called()

    def test_set_pr_done_label_skips_edit_when_already_has_done(self, mocker):
        """When PR already has refix: done and no refix: running, skip gh pr edit to avoid updating PR."""
        pr_data: PRData = {"labels": [{"name": "refix: done"}]}
        mock_ensure = mocker.patch("pr_label._ensure_refix_labels")
        mock_edit = mocker.patch("pr_label.edit_pr_label")
        pr_label._set_pr_done_label("owner/repo", 11, pr_data=pr_data)

        mock_ensure.assert_not_called()
        mock_edit.assert_not_called()

    def test_set_pr_done_label_ensures_labels_before_edit(self, mocker):
        mock_ensure = mocker.patch("pr_label._ensure_refix_labels")
        mock_edit = mocker.patch("pr_label.edit_pr_label", return_value=True)
        pr_label._set_pr_done_label("owner/repo", 11)

        mock_ensure.assert_called_once_with("owner/repo", error_collector=None)
        mock_edit.assert_has_calls(
            [
                call(
                    "owner/repo",
                    11,
                    add=False,
                    label="refix: running",
                    error_collector=None,
                ),
                call(
                    "owner/repo",
                    11,
                    add=True,
                    label="refix: done",
                    error_collector=None,
                ),
            ]
        )

    def test_set_pr_merged_label_ensures_labels_before_edit(self, mocker):
        mock_ensure = mocker.patch("pr_label._ensure_refix_labels")
        mock_edit = mocker.patch("pr_label.edit_pr_label", return_value=True)
        pr_label._set_pr_merged_label("owner/repo", 12)

        mock_ensure.assert_called_once_with("owner/repo", error_collector=None)
        mock_edit.assert_has_calls(
            [
                call(
                    "owner/repo",
                    12,
                    add=False,
                    label="refix: running",
                    error_collector=None,
                ),
                call(
                    "owner/repo",
                    12,
                    add=False,
                    label="refix: auto-merge-requested",
                    error_collector=None,
                ),
                call(
                    "owner/repo",
                    12,
                    add=True,
                    label="refix: merged",
                    error_collector=None,
                ),
            ]
        )

    def test_set_pr_running_label_noop_when_running_and_done_disabled(self, mocker):
        mock_ensure = mocker.patch("pr_label._ensure_refix_labels")
        mock_edit = mocker.patch("pr_label.edit_pr_label")
        pr_label.set_pr_running_label(
            "owner/repo",
            9,
            enabled_pr_label_keys={"merged", "auto_merge_requested"},
        )
        mock_ensure.assert_not_called()
        mock_edit.assert_not_called()

    def test_set_pr_running_label_removes_done_when_running_disabled(self, mocker):
        pr_data: PRData = {"labels": [{"name": "refix: done"}]}
        mock_ensure = mocker.patch("pr_label._ensure_refix_labels")
        mock_edit = mocker.patch("pr_label.edit_pr_label")
        pr_label.set_pr_running_label(
            "owner/repo",
            9,
            pr_data=pr_data,
            enabled_pr_label_keys={"done"},
        )
        mock_ensure.assert_called_once_with(
            "owner/repo", enabled_pr_label_keys={"done"}, error_collector=None
        )
        mock_edit.assert_called_once_with(
            "owner/repo",
            9,
            add=False,
            label="refix: done",
            enabled_pr_label_keys={"done"},
            error_collector=None,
        )

    def test_backfill_merged_labels_skips_when_no_merge_related_labels_enabled(
        self, mocker
    ):
        mock_run = mocker.patch("pr_label.run_command")
        count = pr_label.backfill_merged_labels(
            "owner/repo", enabled_pr_label_keys={"done"}
        )
        assert count == 0
        mock_run.assert_not_called()

    def test_trigger_pr_auto_merge_executes_gh_merge(self, mocker, make_cmd_result):
        mocker.patch(
            "pr_label._get_allowed_merge_methods",
            return_value=["merge", "squash"],
        )
        mock_run = mocker.patch(
            "pr_label.run_command",
            return_value=make_cmd_result(""),
        )
        merge_state_reached, _ = pr_label._trigger_pr_auto_merge("owner/repo", 7)

        assert merge_state_reached is True
        mock_run.assert_any_call(
            ["gh", "pr", "merge", "7", "--repo", "owner/repo", "--auto", "--merge"],
            check=False,
        )

    def test_trigger_pr_auto_merge_explicit_squash(self, mocker, make_cmd_result):
        mock_run = mocker.patch(
            "pr_label.run_command",
            return_value=make_cmd_result(""),
        )
        merge_state_reached, _ = pr_label._trigger_pr_auto_merge(
            "owner/repo", 7, merge_method="squash"
        )

        assert merge_state_reached is True
        mock_run.assert_any_call(
            ["gh", "pr", "merge", "7", "--repo", "owner/repo", "--auto", "--squash"],
            check=False,
        )

    def test_trigger_pr_auto_merge_auto_falls_back_on_method_not_allowed(
        self, mocker, make_cmd_result
    ):
        """auto モードでメソッド非対応エラーが返った場合、次のメソッドを試みる。"""
        mocker.patch("pr_label._get_allowed_merge_methods", return_value=None)
        mocker.patch(
            "pr_label.run_command",
            side_effect=[
                make_cmd_result("", returncode=1, stderr="merge method not allowed"),
                make_cmd_result(""),
            ],
        )
        mocker.patch("pr_label._ensure_refix_labels")
        mocker.patch("pr_label.edit_pr_label", return_value=False)
        merge_state_reached, _ = pr_label._trigger_pr_auto_merge(
            "owner/repo", 7, merge_method="auto"
        )

        assert merge_state_reached is True

    def test_trigger_pr_auto_merge_treats_already_merged_as_success(
        self, mocker, make_cmd_result
    ):
        mocker.patch(
            "pr_label.run_command",
            return_value=make_cmd_result(
                "", returncode=1, stderr="pull request is already merged"
            ),
        )
        merge_state_reached, _ = pr_label._trigger_pr_auto_merge("owner/repo", 8)

        assert merge_state_reached is True

    def test_mark_pr_merged_label_if_needed_adds_label_for_done_merged_pr(
        self, mocker, make_cmd_result
    ):
        pr_view = {
            "mergedAt": "2026-03-11T00:00:00Z",
            "labels": [
                {"name": "refix: done"},
                {"name": "refix: auto-merge-requested"},
            ],
        }
        mocker.patch(
            "pr_label.run_command",
            return_value=make_cmd_result(json.dumps(pr_view)),
        )
        mock_set_merged = mocker.patch(
            "pr_label._set_pr_merged_label", return_value=True
        )
        ok = pr_label._mark_pr_merged_label_if_needed("owner/repo", 21)
        assert ok is True
        mock_set_merged.assert_called_once_with(
            "owner/repo", 21, use_pr_labels=True, error_collector=None
        )

    def test_mark_pr_merged_label_if_needed_skips_when_not_merged(
        self, mocker, make_cmd_result
    ):
        pr_view = {
            "mergedAt": None,
            "labels": [
                {"name": "refix: done"},
                {"name": "refix: auto-merge-requested"},
            ],
        }
        mocker.patch(
            "pr_label.run_command",
            return_value=make_cmd_result(json.dumps(pr_view)),
        )
        mock_set_merged = mocker.patch("pr_label._set_pr_merged_label")
        ok = pr_label._mark_pr_merged_label_if_needed("owner/repo", 22)
        assert ok is False
        mock_set_merged.assert_not_called()

    def test_mark_pr_merged_label_if_needed_skips_when_auto_merge_not_requested(
        self, mocker, make_cmd_result
    ):
        pr_view = {
            "mergedAt": "2026-03-11T00:00:00Z",
            "labels": [{"name": "refix: done"}],
        }
        mocker.patch(
            "pr_label.run_command",
            return_value=make_cmd_result(json.dumps(pr_view)),
        )
        mock_set_merged = mocker.patch("pr_label._set_pr_merged_label")
        ok = pr_label._mark_pr_merged_label_if_needed("owner/repo", 23)
        assert ok is False
        mock_set_merged.assert_not_called()

    def test_backfill_merged_labels_applies_label_to_matching_prs(
        self, mocker, make_cmd_result
    ):
        merged_prs = [{"number": 31}, {"number": 32}]
        mocker.patch(
            "pr_label.run_command",
            return_value=make_cmd_result(json.dumps(merged_prs)),
        )
        mock_mark = mocker.patch(
            "pr_label._mark_pr_merged_label_if_needed", return_value=True
        )
        count = pr_label.backfill_merged_labels("owner/repo")
        assert count == 2
        mock_mark.assert_has_calls(
            [
                call("owner/repo", 31, error_collector=None),
                call("owner/repo", 32, error_collector=None),
            ]
        )

    def test_backfill_merged_labels_returns_zero_on_list_failure(
        self, mocker, make_cmd_result
    ):
        mocker.patch(
            "pr_label.run_command",
            return_value=make_cmd_result("", returncode=1, stderr="boom"),
        )
        mock_set_merged = mocker.patch("pr_label._set_pr_merged_label")
        count = pr_label.backfill_merged_labels("owner/repo")
        assert count == 0
        mock_set_merged.assert_not_called()

    def test_contains_coderabbit_processing_marker(self):
        pr_data: PRData = {
            "reviews": [],
            "comments": [
                {
                    "author": {"login": "coderabbitai"},
                    "body": "Currently processing new changes in this PR.",
                }
            ],
        }
        assert coderabbit.contains_coderabbit_processing_marker(pr_data, []) is True

    def test_update_done_label_sets_done_when_conditions_met(self, mocker):
        mocker.patch("pr_label.fetch_pr_review_comments", return_value=[])
        mocker.patch("pr_label.fetch_issue_comments", return_value=[])
        mocker.patch("pr_label.has_coderabbit_comments", return_value=True)
        mocker.patch(
            "pr_label.contains_coderabbit_processing_marker", return_value=False
        )
        mocker.patch("pr_label.are_all_ci_checks_successful", return_value=True)
        mock_set_done = mocker.patch("pr_label._set_pr_done_label")
        mock_set_running = mocker.patch("pr_label.set_pr_running_label")
        mock_auto_merge = mocker.patch("pr_label._trigger_pr_auto_merge")
        mocker.patch("pr_label.edit_pr_label", return_value=True)
        pr_label.update_done_label_if_completed(
            repo="owner/repo",
            pr_number=1,
            has_review_targets=False,
            review_fix_started=False,
            review_fix_added_commits=False,
            review_fix_failed=False,
            state_saved=True,
            commits_by_phase=[],
            pr_data={"reviews": [], "comments": []},
            review_comments=[],
            issue_comments=[],
            dry_run=False,
            summarize_only=False,
        )
        mock_set_done.assert_called_once_with(
            "owner/repo",
            1,
            pr_data={"reviews": [], "comments": []},
            use_pr_labels=True,
            state_comment=None,
            error_collector=None,
        )
        mock_set_running.assert_not_called()
        mock_auto_merge.assert_not_called()

    def test_update_done_label_triggers_auto_merge_when_enabled(self, mocker):
        mocker.patch("pr_label.fetch_pr_review_comments", return_value=[])
        mocker.patch("pr_label.fetch_issue_comments", return_value=[])
        mocker.patch("pr_label.has_coderabbit_comments", return_value=True)
        mocker.patch(
            "pr_label.contains_coderabbit_processing_marker", return_value=False
        )
        mocker.patch("pr_label.are_all_ci_checks_successful", return_value=True)
        mock_set_done = mocker.patch("pr_label._set_pr_done_label")
        mock_set_running = mocker.patch("pr_label.set_pr_running_label")
        mock_auto_merge = mocker.patch(
            "pr_label._trigger_pr_auto_merge", return_value=(True, False)
        )
        mock_mark_merged = mocker.patch("pr_label._mark_pr_merged_label_if_needed")
        mocker.patch("pr_label.edit_pr_label", return_value=True)
        pr_label.update_done_label_if_completed(
            repo="owner/repo",
            pr_number=3,
            has_review_targets=False,
            review_fix_started=False,
            review_fix_added_commits=False,
            review_fix_failed=False,
            state_saved=True,
            commits_by_phase=[],
            pr_data={"reviews": [], "comments": []},
            review_comments=[],
            issue_comments=[],
            dry_run=False,
            summarize_only=False,
            auto_merge_enabled=True,
        )
        mock_set_done.assert_called_once_with(
            "owner/repo",
            3,
            pr_data={"reviews": [], "comments": []},
            use_pr_labels=True,
            state_comment=None,
            error_collector=None,
        )
        mock_set_running.assert_not_called()
        mock_auto_merge.assert_called_once_with(
            "owner/repo",
            3,
            merge_method="auto",
            use_pr_labels=True,
            error_collector=None,
        )
        mock_mark_merged.assert_called_once_with(
            "owner/repo", 3, use_pr_labels=True, error_collector=None
        )

    def test_update_done_label_sets_running_when_review_fix_added_commit(self, mocker):
        mock_marker = mocker.patch("pr_label.contains_coderabbit_processing_marker")
        mock_ci = mocker.patch("pr_label.are_all_ci_checks_successful")
        mock_set_done = mocker.patch("pr_label._set_pr_done_label")
        mock_set_running = mocker.patch("pr_label.set_pr_running_label")
        mocker.patch("pr_label.edit_pr_label", return_value=True)
        pr_label.update_done_label_if_completed(
            repo="owner/repo",
            pr_number=1,
            has_review_targets=True,
            review_fix_started=True,
            review_fix_added_commits=True,
            review_fix_failed=False,
            state_saved=True,
            commits_by_phase=[],
            pr_data={"reviews": [], "comments": []},
            review_comments=[],
            issue_comments=[],
            dry_run=False,
            summarize_only=False,
        )
        mock_marker.assert_not_called()
        mock_ci.assert_not_called()
        mock_set_done.assert_not_called()
        mock_set_running.assert_called_once_with(
            "owner/repo",
            1,
            pr_data={"reviews": [], "comments": []},
            use_pr_labels=True,
            state_comment=None,
            error_collector=None,
        )

    def test_update_done_label_sets_running_when_ci_not_success(self, mocker):
        mocker.patch("pr_label.fetch_pr_review_comments", return_value=[])
        mocker.patch("pr_label.fetch_issue_comments", return_value=[])
        mocker.patch(
            "pr_label.contains_coderabbit_processing_marker", return_value=False
        )
        mocker.patch("pr_label.are_all_ci_checks_successful", return_value=False)
        mock_set_done = mocker.patch("pr_label._set_pr_done_label")
        mock_set_running = mocker.patch("pr_label.set_pr_running_label")
        mocker.patch("pr_label.edit_pr_label", return_value=True)
        pr_label.update_done_label_if_completed(
            repo="owner/repo",
            pr_number=2,
            has_review_targets=False,
            review_fix_started=False,
            review_fix_added_commits=False,
            review_fix_failed=False,
            state_saved=True,
            commits_by_phase=[],
            pr_data={"reviews": [], "comments": []},
            review_comments=[],
            issue_comments=[],
            dry_run=False,
            summarize_only=False,
        )
        mock_set_done.assert_not_called()
        mock_set_running.assert_called_once_with(
            "owner/repo",
            2,
            pr_data={"reviews": [], "comments": []},
            use_pr_labels=True,
            state_comment=None,
            error_collector=None,
        )

    def test_update_done_label_skips_when_review_fix_failed(self, mocker):
        mock_set_done = mocker.patch("pr_label._set_pr_done_label")
        mock_set_running = mocker.patch("pr_label.set_pr_running_label")
        mocker.patch("pr_label.edit_pr_label", return_value=True)
        pr_label.update_done_label_if_completed(
            repo="owner/repo",
            pr_number=1,
            has_review_targets=False,
            review_fix_started=False,
            review_fix_added_commits=False,
            review_fix_failed=True,
            state_saved=True,
            commits_by_phase=[],
            pr_data={"reviews": [], "comments": []},
            review_comments=[],
            issue_comments=[],
            dry_run=False,
            summarize_only=False,
        )
        mock_set_done.assert_not_called()
        mock_set_running.assert_called_once_with(
            "owner/repo",
            1,
            pr_data={"reviews": [], "comments": []},
            use_pr_labels=True,
            state_comment=None,
            error_collector=None,
        )

    def test_update_done_label_skips_when_dry_run(self, mocker):
        mock_set_done = mocker.patch("pr_label._set_pr_done_label")
        pr_label.update_done_label_if_completed(
            repo="owner/repo",
            pr_number=1,
            has_review_targets=False,
            review_fix_started=False,
            review_fix_added_commits=False,
            review_fix_failed=False,
            state_saved=True,
            commits_by_phase=[],
            pr_data={"reviews": [], "comments": []},
            review_comments=[],
            issue_comments=[],
            dry_run=True,
            summarize_only=False,
        )
        mock_set_done.assert_not_called()

    def test_update_done_label_skips_when_summarize_only(self, mocker):
        mock_set_done = mocker.patch("pr_label._set_pr_done_label")
        pr_label.update_done_label_if_completed(
            repo="owner/repo",
            pr_number=1,
            has_review_targets=False,
            review_fix_started=False,
            review_fix_added_commits=False,
            review_fix_failed=False,
            state_saved=True,
            commits_by_phase=[],
            pr_data={"reviews": [], "comments": []},
            review_comments=[],
            issue_comments=[],
            dry_run=False,
            summarize_only=True,
        )
        mock_set_done.assert_not_called()

    def test_update_done_label_skips_when_coderabbit_processing(self, mocker):
        mocker.patch("pr_label.fetch_pr_review_comments", return_value=[])
        mocker.patch("pr_label.fetch_issue_comments", return_value=[])
        mocker.patch(
            "pr_label.contains_coderabbit_processing_marker", return_value=True
        )
        mock_ci = mocker.patch("pr_label.are_all_ci_checks_successful")
        mock_set_done = mocker.patch("pr_label._set_pr_done_label")
        mock_set_running = mocker.patch("pr_label.set_pr_running_label")
        mocker.patch("pr_label.edit_pr_label", return_value=True)
        pr_label.update_done_label_if_completed(
            repo="owner/repo",
            pr_number=1,
            has_review_targets=False,
            review_fix_started=False,
            review_fix_added_commits=False,
            review_fix_failed=False,
            state_saved=True,
            commits_by_phase=[],
            pr_data={"reviews": [], "comments": []},
            review_comments=[],
            issue_comments=[],
            dry_run=False,
            summarize_only=False,
        )
        mock_ci.assert_not_called()
        mock_set_done.assert_not_called()
        mock_set_running.assert_called_once_with(
            "owner/repo",
            1,
            pr_data={"reviews": [], "comments": []},
            use_pr_labels=True,
            state_comment=None,
            error_collector=None,
        )

    def test_update_done_label_skips_when_coderabbit_rate_limit_active(self, mocker):
        mocker.patch("pr_label.fetch_pr_review_comments", return_value=[])
        mocker.patch("pr_label.fetch_issue_comments", return_value=[])
        mocker.patch(
            "pr_label.contains_coderabbit_processing_marker", return_value=False
        )
        mock_ci = mocker.patch("pr_label.are_all_ci_checks_successful")
        mock_set_done = mocker.patch("pr_label._set_pr_done_label")
        mock_set_running = mocker.patch("pr_label.set_pr_running_label")
        mocker.patch("pr_label.edit_pr_label", return_value=True)
        pr_label.update_done_label_if_completed(
            repo="owner/repo",
            pr_number=1,
            has_review_targets=False,
            review_fix_started=False,
            review_fix_added_commits=False,
            review_fix_failed=False,
            state_saved=True,
            commits_by_phase=[],
            pr_data={"reviews": [], "comments": []},
            review_comments=[],
            issue_comments=[],
            dry_run=False,
            summarize_only=False,
            coderabbit_rate_limit_active=True,
        )
        mock_ci.assert_not_called()
        mock_set_done.assert_not_called()
        mock_set_running.assert_called_once_with(
            "owner/repo",
            1,
            pr_data={"reviews": [], "comments": []},
            use_pr_labels=True,
            state_comment=None,
            error_collector=None,
        )

    def test_update_done_label_skips_when_coderabbit_review_skipped_active(
        self, mocker
    ):
        mocker.patch("pr_label.fetch_pr_review_comments", return_value=[])
        mocker.patch("pr_label.fetch_issue_comments", return_value=[])
        mocker.patch(
            "pr_label.contains_coderabbit_processing_marker", return_value=False
        )
        mock_ci = mocker.patch("pr_label.are_all_ci_checks_successful")
        mock_set_done = mocker.patch("pr_label._set_pr_done_label")
        mock_set_running = mocker.patch("pr_label.set_pr_running_label")
        mocker.patch("pr_label.edit_pr_label", return_value=True)
        pr_label.update_done_label_if_completed(
            repo="owner/repo",
            pr_number=1,
            has_review_targets=False,
            review_fix_started=False,
            review_fix_added_commits=False,
            review_fix_failed=False,
            state_saved=True,
            commits_by_phase=[],
            pr_data={"reviews": [], "comments": []},
            review_comments=[],
            issue_comments=[],
            dry_run=False,
            summarize_only=False,
            coderabbit_review_skipped_active=True,
        )
        mock_ci.assert_not_called()
        mock_set_done.assert_not_called()
        mock_set_running.assert_called_once_with(
            "owner/repo",
            1,
            pr_data={"reviews": [], "comments": []},
            use_pr_labels=True,
            state_comment=None,
            error_collector=None,
        )


class TestErrorCollectorIntegration:
    def test_ensure_repo_label_exists_subprocess_error_adds_repo_error(self, mocker):
        ec = ErrorCollector()
        mocker.patch(
            "pr_label.run_command", side_effect=SubprocessError("network error")
        )
        ok = pr_label._ensure_repo_label_exists(
            "owner/repo",
            "refix: running",
            color="FBCA04",
            description="desc",
            error_collector=ec,
        )
        assert ok is False
        assert ec.has_errors
        assert ec._errors[0].scope == "owner/repo"
        assert "failed to check label" in ec._errors[0].message

    def test_ensure_repo_label_exists_unexpected_api_error_adds_repo_error(
        self, mocker, make_cmd_result
    ):
        ec = ErrorCollector()
        mocker.patch(
            "pr_label.run_command",
            return_value=make_cmd_result("", returncode=1, stderr="403 Forbidden"),
        )
        ok = pr_label._ensure_repo_label_exists(
            "owner/repo",
            "refix: running",
            color="FBCA04",
            description="desc",
            error_collector=ec,
        )
        assert ok is False
        assert ec.has_errors
        assert "failed to verify label" in ec._errors[0].message

    def test_edit_pr_label_failure_adds_pr_error(self, mocker, make_cmd_result):
        ec = ErrorCollector()
        mocker.patch(
            "pr_label.run_command",
            return_value=make_cmd_result("", returncode=1, stderr="API error"),
        )
        ok = pr_label.edit_pr_label(
            "owner/repo",
            42,
            add=True,
            label="refix: running",
            error_collector=ec,
        )
        assert ok is False
        assert ec.has_errors
        assert ec._errors[0].scope == "owner/repo#42"

    def test_backfill_merged_labels_list_failure_adds_repo_error(
        self, mocker, make_cmd_result
    ):
        ec = ErrorCollector()
        mocker.patch(
            "pr_label.run_command",
            return_value=make_cmd_result("", returncode=1, stderr="boom"),
        )
        count = pr_label.backfill_merged_labels("owner/repo", error_collector=ec)
        assert count == 0
        assert ec.has_errors
        assert ec._errors[0].scope == "owner/repo"

    def test_trigger_pr_auto_merge_failure_adds_pr_error(self, mocker, make_cmd_result):
        ec = ErrorCollector()
        mocker.patch(
            "pr_label.run_command",
            return_value=make_cmd_result("", returncode=1, stderr="forbidden"),
        )
        merge_state_reached, _ = pr_label._trigger_pr_auto_merge(
            "owner/repo", 7, error_collector=ec
        )
        assert ec.has_errors
        assert ec._errors[0].scope == "owner/repo#7"


class TestCiPendingLabel:
    def test_ci_pending_label_constant(self):
        assert pr_label.REFIX_CI_PENDING_LABEL == "refix: ci-pending"
        assert "ci_pending" in pr_label.PR_LABEL_KEY_TO_NAME
        assert pr_label.PR_LABEL_KEY_TO_NAME["ci_pending"] == "refix: ci-pending"

    def test_ensure_refix_labels_creates_ci_pending_when_enabled(
        self, mocker, make_cmd_result
    ):
        mock_run = mocker.patch(
            "pr_label.run_command",
            return_value=make_cmd_result('{"name":"refix: ci-pending"}'),
        )
        pr_label._ensure_refix_labels(
            "owner/repo", enabled_pr_label_keys={"ci_pending"}
        )
        # Should have called gh api to check label existence
        assert mock_run.called
        # The label name is URL-encoded in the API path
        full_cmd = " ".join(mock_run.call_args_list[0].args[0])
        assert "ci-pending" in full_cmd

    def test_update_done_label_adds_ci_pending_when_ci_blocks(self, mocker):
        mocker.patch("pr_label.fetch_pr_review_comments", return_value=[])
        mocker.patch("pr_label.fetch_issue_comments", return_value=[])
        mocker.patch("pr_label.has_coderabbit_comments", return_value=True)
        mocker.patch(
            "pr_label.contains_coderabbit_processing_marker", return_value=False
        )
        mocker.patch("pr_label.are_all_ci_checks_successful", return_value=False)
        mocker.patch("pr_label._set_pr_done_label")
        mocker.patch("pr_label.set_pr_running_label", return_value=True)
        mock_edit = mocker.patch("pr_label.edit_pr_label", return_value=True)

        pr_label.update_done_label_if_completed(
            repo="owner/repo",
            pr_number=5,
            has_review_targets=False,
            review_fix_started=False,
            review_fix_added_commits=False,
            review_fix_failed=False,
            state_saved=True,
            commits_by_phase=[],
            pr_data={"reviews": [], "comments": []},
            review_comments=[],
            issue_comments=[],
            dry_run=False,
            summarize_only=False,
        )

        # ci-pending should be added (add=True) when CI is blocking
        ci_pending_calls = [
            c
            for c in mock_edit.call_args_list
            if c.kwargs.get("label") == pr_label.REFIX_CI_PENDING_LABEL
        ]
        assert len(ci_pending_calls) == 1
        assert ci_pending_calls[0].kwargs["add"] is True

    def test_update_done_label_removes_ci_pending_when_non_ci_blocks(self, mocker):
        mocker.patch(
            "pr_label.contains_coderabbit_processing_marker", return_value=False
        )
        mocker.patch("pr_label._set_pr_done_label")
        mocker.patch("pr_label.set_pr_running_label", return_value=True)
        mock_edit = mocker.patch("pr_label.edit_pr_label", return_value=True)

        # review_fix_failed=True → non-CI block; pr_data has ci-pending label already
        pr_data_with_label: PRData = {
            "reviews": [],
            "comments": [],
            "labels": [{"name": pr_label.REFIX_CI_PENDING_LABEL}],
        }
        pr_label.update_done_label_if_completed(
            repo="owner/repo",
            pr_number=6,
            has_review_targets=False,
            review_fix_started=False,
            review_fix_added_commits=False,
            review_fix_failed=True,
            state_saved=True,
            commits_by_phase=[],
            pr_data=pr_data_with_label,
            review_comments=[],
            issue_comments=[],
            dry_run=False,
            summarize_only=False,
        )

        # ci-pending should be removed (add=False) when non-CI is blocking and label is present
        ci_pending_calls = [
            c
            for c in mock_edit.call_args_list
            if c.kwargs.get("label") == pr_label.REFIX_CI_PENDING_LABEL
        ]
        assert len(ci_pending_calls) == 1
        assert ci_pending_calls[0].kwargs["add"] is False

    def _call_update_done_label(self, mocker, **overrides):
        """update_done_label_if_completed のデフォルト引数ヘルパー。"""
        mocker.patch("pr_label.fetch_pr_review_comments", return_value=[])
        mocker.patch("pr_label.fetch_issue_comments", return_value=[])
        mocker.patch("pr_label.has_coderabbit_comments", return_value=True)
        defaults = dict(
            repo="owner/repo",
            pr_number=10,
            has_review_targets=False,
            review_fix_started=False,
            review_fix_added_commits=False,
            review_fix_failed=False,
            state_saved=True,
            commits_by_phase=[],
            pr_data={"reviews": [], "comments": []},
            review_comments=[],
            issue_comments=[],
            dry_run=False,
            summarize_only=False,
        )
        defaults.update(overrides)
        return pr_label.update_done_label_if_completed(**defaults)  # type: ignore[arg-type]

    def test_ci_pending_added_when_commits_only_blocking(self, mocker):
        """commits_by_phase 非空 + 他ブロックなし → ci-pending 付与"""
        mocker.patch(
            "pr_label.contains_coderabbit_processing_marker", return_value=False
        )
        mocker.patch("pr_label.are_all_ci_checks_successful", return_value=True)
        mocker.patch("pr_label._set_pr_done_label")
        mocker.patch("pr_label.set_pr_running_label", return_value=True)
        mocker.patch("pr_label._trigger_pr_auto_merge")
        mock_edit = mocker.patch("pr_label.edit_pr_label", return_value=True)

        self._call_update_done_label(mocker, commits_by_phase=[("review", "abc123")])

        ci_pending_calls = [
            c
            for c in mock_edit.call_args_list
            if c.kwargs.get("label") == pr_label.REFIX_CI_PENDING_LABEL
        ]
        assert len(ci_pending_calls) == 1
        assert ci_pending_calls[0].kwargs["add"] is True

    def test_ci_pending_not_added_when_commits_and_review_fix_failed(self, mocker):
        """commits_by_phase 非空 + review_fix_failed=True → ci-pending 除去"""
        mocker.patch(
            "pr_label.contains_coderabbit_processing_marker", return_value=False
        )
        mocker.patch("pr_label._set_pr_done_label")
        mocker.patch("pr_label.set_pr_running_label", return_value=True)
        mock_edit = mocker.patch("pr_label.edit_pr_label", return_value=True)

        self._call_update_done_label(
            mocker,
            commits_by_phase=[("review", "abc123")],
            review_fix_failed=True,
            pr_data={
                "reviews": [],
                "comments": [],
                "labels": [{"name": pr_label.REFIX_CI_PENDING_LABEL}],
            },
        )

        ci_pending_calls = [
            c
            for c in mock_edit.call_args_list
            if c.kwargs.get("label") == pr_label.REFIX_CI_PENDING_LABEL
        ]
        assert len(ci_pending_calls) == 1
        assert ci_pending_calls[0].kwargs["add"] is False

    def test_ci_pending_not_added_when_commits_and_unstarted_review(self, mocker):
        """commits_by_phase 非空 + has_review_targets=True, review_fix_started=False → ci-pending 除去"""
        mocker.patch(
            "pr_label.contains_coderabbit_processing_marker", return_value=False
        )
        mocker.patch("pr_label._set_pr_done_label")
        mocker.patch("pr_label.set_pr_running_label", return_value=True)
        mock_edit = mocker.patch("pr_label.edit_pr_label", return_value=True)

        self._call_update_done_label(
            mocker,
            commits_by_phase=[("review", "abc123")],
            has_review_targets=True,
            review_fix_started=False,
            pr_data={
                "reviews": [],
                "comments": [],
                "labels": [{"name": pr_label.REFIX_CI_PENDING_LABEL}],
            },
        )

        ci_pending_calls = [
            c
            for c in mock_edit.call_args_list
            if c.kwargs.get("label") == pr_label.REFIX_CI_PENDING_LABEL
        ]
        assert len(ci_pending_calls) == 1
        assert ci_pending_calls[0].kwargs["add"] is False

    def test_ci_pending_not_added_when_commits_and_state_not_saved(self, mocker):
        """commits_by_phase 非空 + state_saved=False → ci-pending 除去"""
        mocker.patch(
            "pr_label.contains_coderabbit_processing_marker", return_value=False
        )
        mocker.patch("pr_label._set_pr_done_label")
        mocker.patch("pr_label.set_pr_running_label", return_value=True)
        mock_edit = mocker.patch("pr_label.edit_pr_label", return_value=True)

        self._call_update_done_label(
            mocker,
            commits_by_phase=[("review", "abc123")],
            state_saved=False,
            pr_data={
                "reviews": [],
                "comments": [],
                "labels": [{"name": pr_label.REFIX_CI_PENDING_LABEL}],
            },
        )

        ci_pending_calls = [
            c
            for c in mock_edit.call_args_list
            if c.kwargs.get("label") == pr_label.REFIX_CI_PENDING_LABEL
        ]
        assert len(ci_pending_calls) == 1
        assert ci_pending_calls[0].kwargs["add"] is False

    def test_ci_pending_added_when_ci_grace_pending(self, mocker):
        """are_all_ci_checks_successful → None → ci_is_blocking=True, ci_grace_pending=True"""
        mocker.patch(
            "pr_label.contains_coderabbit_processing_marker", return_value=False
        )
        mocker.patch("pr_label.are_all_ci_checks_successful", return_value=None)
        mocker.patch("pr_label._set_pr_done_label")
        mocker.patch("pr_label.set_pr_running_label", return_value=True)
        mock_edit = mocker.patch("pr_label.edit_pr_label", return_value=True)

        _, ci_grace_pending = self._call_update_done_label(mocker)

        assert ci_grace_pending is True
        ci_pending_calls = [
            c
            for c in mock_edit.call_args_list
            if c.kwargs.get("label") == pr_label.REFIX_CI_PENDING_LABEL
        ]
        assert len(ci_pending_calls) == 1
        assert ci_pending_calls[0].kwargs["add"] is True

    def test_update_done_label_removes_ci_pending_when_completed(self, mocker):
        mocker.patch("pr_label.fetch_pr_review_comments", return_value=[])
        mocker.patch("pr_label.fetch_issue_comments", return_value=[])
        mocker.patch(
            "pr_label.contains_coderabbit_processing_marker", return_value=False
        )
        mocker.patch("pr_label.are_all_ci_checks_successful", return_value=True)
        mocker.patch("pr_label._set_pr_done_label", return_value=True)
        mocker.patch("pr_label.set_pr_running_label")
        mocker.patch("pr_label._trigger_pr_auto_merge")
        mock_edit = mocker.patch("pr_label.edit_pr_label", return_value=True)

        pr_label.update_done_label_if_completed(
            repo="owner/repo",
            pr_number=7,
            has_review_targets=False,
            review_fix_started=False,
            review_fix_added_commits=False,
            review_fix_failed=False,
            state_saved=True,
            commits_by_phase=[],
            pr_data={
                "reviews": [],
                "comments": [],
                "labels": [{"name": pr_label.REFIX_CI_PENDING_LABEL}],
            },
            review_comments=[],
            issue_comments=[],
            dry_run=False,
            summarize_only=False,
        )

        # ci-pending should be removed (add=False) when PR is completed
        ci_pending_calls = [
            c
            for c in mock_edit.call_args_list
            if c.kwargs.get("label") == pr_label.REFIX_CI_PENDING_LABEL
        ]
        assert len(ci_pending_calls) == 1
        assert ci_pending_calls[0].kwargs["add"] is False


class TestCoderabbitRequireReview:
    """coderabbit_require_review / coderabbit_block_while_processing のテスト。"""

    def _base_mocks(self, mocker):
        mocker.patch("pr_label.fetch_pr_review_comments", return_value=[])
        mocker.patch("pr_label.fetch_issue_comments", return_value=[])
        mocker.patch("pr_label._set_pr_done_label")
        mocker.patch("pr_label.set_pr_running_label", return_value=True)
        mocker.patch("pr_label.edit_pr_label", return_value=True)
        mocker.patch("pr_label._trigger_pr_auto_merge")
        mocker.patch("pr_label.are_all_ci_checks_successful", return_value=True)

    def _call(self, mocker, **overrides):
        defaults = dict(
            repo="owner/repo",
            pr_number=10,
            has_review_targets=False,
            review_fix_started=False,
            review_fix_added_commits=False,
            review_fix_failed=False,
            state_saved=True,
            commits_by_phase=[],
            pr_data={"reviews": [], "comments": []},
            review_comments=[],
            issue_comments=[],
            dry_run=False,
            summarize_only=False,
        )
        defaults.update(overrides)
        return pr_label.update_done_label_if_completed(**defaults)  # type: ignore[arg-type]

    def test_require_review_blocks_when_no_coderabbit_comment(self, mocker):
        """coderabbit_require_review=True かつ CR コメントなし → done にならない。"""
        self._base_mocks(mocker)
        mocker.patch("pr_label.has_coderabbit_comments", return_value=False)
        mocker.patch(
            "pr_label.contains_coderabbit_processing_marker", return_value=False
        )
        mock_done = mocker.patch("pr_label._set_pr_done_label")

        self._call(mocker, coderabbit_require_review=True)

        mock_done.assert_not_called()

    def test_require_review_allows_when_coderabbit_comment_exists(self, mocker):
        """coderabbit_require_review=True かつ CR コメントあり → done になれる。"""
        self._base_mocks(mocker)
        mocker.patch("pr_label.has_coderabbit_comments", return_value=True)
        mocker.patch(
            "pr_label.contains_coderabbit_processing_marker", return_value=False
        )
        mock_done = mocker.patch("pr_label._set_pr_done_label")

        self._call(mocker, coderabbit_require_review=True)

        mock_done.assert_called_once()

    def test_require_review_false_skips_check(self, mocker):
        """coderabbit_require_review=False → CR コメントなくても done になれる。"""
        self._base_mocks(mocker)
        mock_has = mocker.patch("pr_label.has_coderabbit_comments", return_value=False)
        mocker.patch(
            "pr_label.contains_coderabbit_processing_marker", return_value=False
        )
        mock_done = mocker.patch("pr_label._set_pr_done_label")

        self._call(mocker, coderabbit_require_review=False)

        mock_has.assert_not_called()
        mock_done.assert_called_once()

    def test_block_while_processing_blocks_when_marker_present(self, mocker):
        """coderabbit_block_while_processing=True かつ processing マーカーあり → done にならない。"""
        self._base_mocks(mocker)
        mocker.patch("pr_label.has_coderabbit_comments", return_value=True)
        mocker.patch(
            "pr_label.contains_coderabbit_processing_marker", return_value=True
        )
        mock_done = mocker.patch("pr_label._set_pr_done_label")

        self._call(
            mocker,
            coderabbit_require_review=True,
            coderabbit_block_while_processing=True,
        )

        mock_done.assert_not_called()

    def test_block_while_processing_false_ignores_marker(self, mocker):
        """coderabbit_block_while_processing=False → processing マーカーがあっても done になれる。"""
        self._base_mocks(mocker)
        mocker.patch("pr_label.has_coderabbit_comments", return_value=True)
        mock_marker = mocker.patch(
            "pr_label.contains_coderabbit_processing_marker", return_value=True
        )
        mock_done = mocker.patch("pr_label._set_pr_done_label")

        self._call(
            mocker,
            coderabbit_require_review=True,
            coderabbit_block_while_processing=False,
        )

        mock_marker.assert_not_called()
        mock_done.assert_called_once()


class TestUpdateDoneLabelRefetch:
    """update_done_label_if_completed のコメント再取得ロジックのテスト。"""

    def _base_call(self, mocker, **overrides):
        defaults = dict(
            repo="owner/repo",
            pr_number=10,
            has_review_targets=False,
            review_fix_started=False,
            review_fix_added_commits=False,
            review_fix_failed=False,
            state_saved=True,
            commits_by_phase=[],
            pr_data={"reviews": [], "comments": []},
            review_comments=[],
            issue_comments=[],
            dry_run=False,
            summarize_only=False,
        )
        defaults.update(overrides)
        return pr_label.update_done_label_if_completed(**defaults)  # type: ignore[arg-type]

    def test_refetch_detects_processing_marker_in_fresh_data(self, mocker):
        """stale データにはマーカーなし、再取得データにマーカーあり → done にならない。"""
        # review_comments は user.login キーを使用
        processing_comment = {
            "user": {"login": "coderabbitai"},
            "body": "Currently processing new changes in this PR.",
        }
        mocker.patch(
            "pr_label.fetch_pr_review_comments", return_value=[processing_comment]
        )
        mocker.patch("pr_label.fetch_issue_comments", return_value=[])
        mocker.patch("pr_label.are_all_ci_checks_successful", return_value=True)
        mocker.patch("pr_label.edit_pr_label", return_value=True)
        mock_done = mocker.patch("pr_label._set_pr_done_label")
        mock_running = mocker.patch("pr_label.set_pr_running_label")
        # has_coderabbit_comments を True にしておく（require_review はパス）
        mocker.patch("pr_label.has_coderabbit_comments", return_value=True)
        # contains_coderabbit_processing_marker は実装を通す（モックしない）
        # → 再取得した review_comments の processing_comment を検出するはず

        self._base_call(
            mocker,
            coderabbit_require_review=True,
            coderabbit_block_while_processing=True,
        )

        mock_done.assert_not_called()
        mock_running.assert_called_once()

    def test_no_refetch_when_already_blocked(self, mocker):
        """review_fix_failed=True で is_completed=False → fetch 関数が呼ばれない。"""
        mock_fetch_review = mocker.patch("pr_label.fetch_pr_review_comments")
        mock_fetch_issue = mocker.patch("pr_label.fetch_issue_comments")
        mocker.patch("pr_label.edit_pr_label", return_value=True)
        mocker.patch("pr_label._set_pr_done_label")
        mocker.patch("pr_label.set_pr_running_label")

        self._base_call(mocker, review_fix_failed=True)

        mock_fetch_review.assert_not_called()
        mock_fetch_issue.assert_not_called()

    def test_no_refetch_when_coderabbit_checks_disabled(self, mocker):
        """coderabbit_require_review=False かつ coderabbit_block_while_processing=False → fetch しない。"""
        mock_fetch_review = mocker.patch("pr_label.fetch_pr_review_comments")
        mock_fetch_issue = mocker.patch("pr_label.fetch_issue_comments")
        mocker.patch("pr_label.are_all_ci_checks_successful", return_value=True)
        mocker.patch("pr_label.edit_pr_label", return_value=True)
        mocker.patch("pr_label._set_pr_done_label")
        mocker.patch("pr_label.set_pr_running_label")

        self._base_call(
            mocker,
            coderabbit_require_review=False,
            coderabbit_block_while_processing=False,
        )

        mock_fetch_review.assert_not_called()
        mock_fetch_issue.assert_not_called()

    def test_refetch_failure_falls_back_to_stale_data(self, mocker):
        """fetch が例外 → stale データで続行（done になる）。"""
        mocker.patch(
            "pr_label.fetch_pr_review_comments",
            side_effect=Exception("network error"),
        )
        mocker.patch(
            "pr_label.fetch_issue_comments",
            side_effect=Exception("network error"),
        )
        mocker.patch("pr_label.are_all_ci_checks_successful", return_value=True)
        mocker.patch("pr_label.edit_pr_label", return_value=True)
        mocker.patch("pr_label.has_coderabbit_comments", return_value=True)
        mocker.patch(
            "pr_label.contains_coderabbit_processing_marker", return_value=False
        )
        mock_done = mocker.patch("pr_label._set_pr_done_label")
        mocker.patch("pr_label.set_pr_running_label")

        self._base_call(
            mocker,
            coderabbit_require_review=True,
            coderabbit_block_while_processing=True,
        )

        # stale データで処理が続行され done になる
        mock_done.assert_called_once()


def _make_state_comment(workflow_status: str = "") -> StateComment:
    return StateComment(
        github_comment_id=None,
        body="",
        entries=[],
        processed_ids=set(),
        archived_ids=set(),
        workflow_status=workflow_status,
    )


class TestResolveWorkflowStatus:
    def test_returns_comment_status(self):
        sc = _make_state_comment("done")
        result = pr_label.resolve_workflow_status(sc, {"labels": []})
        assert result == "done"

    def test_falls_back_to_label(self):
        sc = _make_state_comment("")
        pr_data: PRData = {"labels": [{"name": "refix: running"}]}
        result = pr_label.resolve_workflow_status(sc, pr_data)
        assert result == "running"

    def test_returns_empty_without_status_or_label(self):
        sc = _make_state_comment("")
        result = pr_label.resolve_workflow_status(sc, {"labels": []})
        assert result == ""


class TestUsePrLabelsFlag:
    def test_set_pr_running_label_skips_label_when_use_pr_labels_false(self, mocker):
        mock_update_status = mocker.patch("pr_label.update_workflow_status")
        mock_edit = mocker.patch("pr_label.edit_pr_label")
        sc = _make_state_comment()

        pr_label.set_pr_running_label(
            "owner/repo", 1, use_pr_labels=False, state_comment=sc
        )

        mock_edit.assert_not_called()
        mock_update_status.assert_called_once_with(
            "owner/repo", 1, "running", _preloaded_state=sc
        )

    def test_set_pr_done_label_skips_label_when_use_pr_labels_false(self, mocker):
        mock_update_status = mocker.patch("pr_label.update_workflow_status")
        mock_edit = mocker.patch("pr_label.edit_pr_label")
        sc = _make_state_comment()

        pr_label._set_pr_done_label(
            "owner/repo", 1, use_pr_labels=False, state_comment=sc
        )

        mock_edit.assert_not_called()
        mock_update_status.assert_called_once_with(
            "owner/repo", 1, "done", _preloaded_state=sc
        )

    def test_set_pr_merged_label_skips_label_when_use_pr_labels_false(self, mocker):
        mock_update_status = mocker.patch("pr_label.update_workflow_status")
        mock_edit = mocker.patch("pr_label.edit_pr_label")
        sc = _make_state_comment()

        pr_label._set_pr_merged_label(
            "owner/repo", 1, use_pr_labels=False, state_comment=sc
        )

        mock_edit.assert_not_called()
        mock_update_status.assert_called_once_with(
            "owner/repo", 1, "merged", _preloaded_state=sc
        )

    def test_update_done_label_skips_labels_when_use_pr_labels_false(self, mocker):
        mocker.patch("pr_label.fetch_pr_review_comments", return_value=[])
        mocker.patch("pr_label.fetch_issue_comments", return_value=[])
        mocker.patch("pr_label.has_coderabbit_comments", return_value=True)
        mocker.patch(
            "pr_label.contains_coderabbit_processing_marker", return_value=False
        )
        mocker.patch("pr_label.are_all_ci_checks_successful", return_value=True)
        mock_set_done = mocker.patch("pr_label._set_pr_done_label")
        mock_edit = mocker.patch("pr_label.edit_pr_label")
        mocker.patch("pr_label.update_workflow_status")

        pr_label.update_done_label_if_completed(
            repo="owner/repo",
            pr_number=1,
            has_review_targets=False,
            review_fix_started=False,
            review_fix_added_commits=False,
            review_fix_failed=False,
            state_saved=True,
            commits_by_phase=[],
            pr_data={"labels": [], "reviews": [], "comments": []},
            review_comments=[],
            issue_comments=[],
            dry_run=False,
            summarize_only=False,
            use_pr_labels=False,
        )

        mock_set_done.assert_called_once()
        call_kwargs = mock_set_done.call_args
        assert call_kwargs.kwargs.get("use_pr_labels") is False
        mock_edit.assert_not_called()
