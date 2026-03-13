"""Unit tests for PR labeling helpers."""

import json
from unittest.mock import Mock, call, patch


import coderabbit
import pr_label


class TestRefixLabeling:
    def test_ensure_repo_label_exists_creates_when_missing(self):
        get_result = Mock(returncode=1, stdout="", stderr="404 Not Found")
        create_result = Mock(returncode=0, stdout='{"name":"refix:running"}', stderr="")
        with patch(
            "pr_label.run_command", side_effect=[get_result, create_result]
        ) as mock_run:
            ok = pr_label._ensure_repo_label_exists(
                "owner/repo",
                "refix:running",
                color="FBCA04",
                description="running label",
            )

        assert ok is True
        assert mock_run.call_count == 2
        create_call = mock_run.call_args_list[1].args[0]
        assert create_call[:3] == ["gh", "api", "repos/owner/repo/labels"]
        assert "name=refix:running" in create_call

    def test_set_pr_running_label_ensures_labels_before_edit(self):
        with (
            patch("pr_label._ensure_refix_labels") as mock_ensure,
            patch("pr_label.edit_pr_label", return_value=True) as mock_edit,
        ):
            pr_label.set_pr_running_label("owner/repo", 9)

        mock_ensure.assert_called_once_with("owner/repo")
        mock_edit.assert_has_calls(
            [
                call("owner/repo", 9, add=False, label="refix:done"),
                call("owner/repo", 9, add=True, label="refix:running"),
            ]
        )

    def test_set_pr_running_label_skips_edit_when_already_has_running(self):
        """When PR already has refix:running and no refix:done, skip gh pr edit to avoid updating PR."""
        pr_data = {"labels": [{"name": "refix:running"}]}
        with (
            patch("pr_label._ensure_refix_labels") as mock_ensure,
            patch("pr_label.edit_pr_label") as mock_edit,
        ):
            pr_label.set_pr_running_label("owner/repo", 9, pr_data=pr_data)

        mock_ensure.assert_not_called()
        mock_edit.assert_not_called()

    def test_set_pr_done_label_skips_edit_when_already_has_done(self):
        """When PR already has refix:done and no refix:running, skip gh pr edit to avoid updating PR."""
        pr_data = {"labels": [{"name": "refix:done"}]}
        with (
            patch("pr_label._ensure_refix_labels") as mock_ensure,
            patch("pr_label.edit_pr_label") as mock_edit,
        ):
            pr_label._set_pr_done_label("owner/repo", 11, pr_data=pr_data)

        mock_ensure.assert_not_called()
        mock_edit.assert_not_called()

    def test_set_pr_done_label_ensures_labels_before_edit(self):
        with (
            patch("pr_label._ensure_refix_labels") as mock_ensure,
            patch("pr_label.edit_pr_label", return_value=True) as mock_edit,
        ):
            pr_label._set_pr_done_label("owner/repo", 11)

        mock_ensure.assert_called_once_with("owner/repo")
        mock_edit.assert_has_calls(
            [
                call("owner/repo", 11, add=False, label="refix:running"),
                call("owner/repo", 11, add=True, label="refix:done"),
            ]
        )

    def test_set_pr_merged_label_ensures_labels_before_edit(self):
        with (
            patch("pr_label._ensure_refix_labels") as mock_ensure,
            patch("pr_label.edit_pr_label", return_value=True) as mock_edit,
        ):
            pr_label._set_pr_merged_label("owner/repo", 12)

        mock_ensure.assert_called_once_with("owner/repo")
        mock_edit.assert_has_calls(
            [
                call("owner/repo", 12, add=False, label="refix:running"),
                call("owner/repo", 12, add=False, label="refix:auto-merge-requested"),
                call("owner/repo", 12, add=True, label="refix:merged"),
            ]
        )

    def test_set_pr_running_label_noop_when_running_and_done_disabled(self):
        with (
            patch("pr_label._ensure_refix_labels") as mock_ensure,
            patch("pr_label.edit_pr_label") as mock_edit,
        ):
            pr_label.set_pr_running_label(
                "owner/repo",
                9,
                enabled_pr_label_keys={"merged", "auto_merge_requested"},
            )
        mock_ensure.assert_not_called()
        mock_edit.assert_not_called()

    def test_set_pr_running_label_removes_done_when_running_disabled(self):
        pr_data = {"labels": [{"name": "refix:done"}]}
        with (
            patch("pr_label._ensure_refix_labels") as mock_ensure,
            patch("pr_label.edit_pr_label") as mock_edit,
        ):
            pr_label.set_pr_running_label(
                "owner/repo",
                9,
                pr_data=pr_data,
                enabled_pr_label_keys={"done"},
            )
        mock_ensure.assert_called_once_with(
            "owner/repo", enabled_pr_label_keys={"done"}
        )
        mock_edit.assert_called_once_with(
            "owner/repo",
            9,
            add=False,
            label="refix:done",
            enabled_pr_label_keys={"done"},
        )

    def test_backfill_merged_labels_skips_when_no_merge_related_labels_enabled(self):
        with patch("pr_label.run_command") as mock_run:
            count = pr_label.backfill_merged_labels(
                "owner/repo", enabled_pr_label_keys={"done"}
            )
        assert count == 0
        mock_run.assert_not_called()

    def test_trigger_pr_auto_merge_executes_gh_merge(self):
        with patch(
            "pr_label.run_command",
            return_value=Mock(returncode=0, stdout="", stderr=""),
        ) as mock_run:
            merge_state_reached, _ = pr_label._trigger_pr_auto_merge("owner/repo", 7)

        assert merge_state_reached is True
        mock_run.assert_any_call(
            ["gh", "pr", "merge", "7", "--repo", "owner/repo", "--auto", "--merge"],
            check=False,
        )

    def test_trigger_pr_auto_merge_treats_already_merged_as_success(self):
        with patch(
            "pr_label.run_command",
            return_value=Mock(
                returncode=1, stdout="", stderr="pull request is already merged"
            ),
        ):
            merge_state_reached, _ = pr_label._trigger_pr_auto_merge("owner/repo", 8)

        assert merge_state_reached is True

    def test_mark_pr_merged_label_if_needed_adds_label_for_done_merged_pr(self):
        pr_view = {
            "mergedAt": "2026-03-11T00:00:00Z",
            "labels": [{"name": "refix:done"}, {"name": "refix:auto-merge-requested"}],
        }
        with (
            patch(
                "pr_label.run_command",
                return_value=Mock(returncode=0, stdout=json.dumps(pr_view), stderr=""),
            ),
            patch(
                "pr_label._set_pr_merged_label", return_value=True
            ) as mock_set_merged,
        ):
            ok = pr_label._mark_pr_merged_label_if_needed("owner/repo", 21)
        assert ok is True
        mock_set_merged.assert_called_once_with("owner/repo", 21)

    def test_mark_pr_merged_label_if_needed_skips_when_not_merged(self):
        pr_view = {
            "mergedAt": None,
            "labels": [{"name": "refix:done"}, {"name": "refix:auto-merge-requested"}],
        }
        with (
            patch(
                "pr_label.run_command",
                return_value=Mock(returncode=0, stdout=json.dumps(pr_view), stderr=""),
            ),
            patch("pr_label._set_pr_merged_label") as mock_set_merged,
        ):
            ok = pr_label._mark_pr_merged_label_if_needed("owner/repo", 22)
        assert ok is False
        mock_set_merged.assert_not_called()

    def test_mark_pr_merged_label_if_needed_skips_when_auto_merge_not_requested(self):
        pr_view = {
            "mergedAt": "2026-03-11T00:00:00Z",
            "labels": [{"name": "refix:done"}],
        }
        with (
            patch(
                "pr_label.run_command",
                return_value=Mock(returncode=0, stdout=json.dumps(pr_view), stderr=""),
            ),
            patch("pr_label._set_pr_merged_label") as mock_set_merged,
        ):
            ok = pr_label._mark_pr_merged_label_if_needed("owner/repo", 23)
        assert ok is False
        mock_set_merged.assert_not_called()

    def test_backfill_merged_labels_applies_label_to_matching_prs(self):
        merged_prs = [{"number": 31}, {"number": 32}]
        with (
            patch(
                "pr_label.run_command",
                return_value=Mock(
                    returncode=0, stdout=json.dumps(merged_prs), stderr=""
                ),
            ),
            patch(
                "pr_label._mark_pr_merged_label_if_needed", return_value=True
            ) as mock_mark,
        ):
            count = pr_label.backfill_merged_labels("owner/repo")
        assert count == 2
        mock_mark.assert_has_calls([call("owner/repo", 31), call("owner/repo", 32)])

    def test_backfill_merged_labels_returns_zero_on_list_failure(self):
        with (
            patch(
                "pr_label.run_command",
                return_value=Mock(returncode=1, stdout="", stderr="boom"),
            ),
            patch("pr_label._set_pr_merged_label") as mock_set_merged,
        ):
            count = pr_label.backfill_merged_labels("owner/repo")
        assert count == 0
        mock_set_merged.assert_not_called()

    def test_contains_coderabbit_processing_marker(self):
        pr_data = {
            "reviews": [],
            "comments": [
                {
                    "author": {"login": "coderabbitai"},
                    "body": "Currently processing new changes in this PR.",
                }
            ],
        }
        assert coderabbit.contains_coderabbit_processing_marker(pr_data, []) is True

    def test_update_done_label_sets_done_when_conditions_met(self):
        with (
            patch("pr_label.contains_coderabbit_processing_marker", return_value=False),
            patch("pr_label.are_all_ci_checks_successful", return_value=True),
            patch("pr_label._set_pr_done_label") as mock_set_done,
            patch("pr_label.set_pr_running_label") as mock_set_running,
            patch("pr_label._trigger_pr_auto_merge") as mock_auto_merge,
        ):
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
            "owner/repo", 1, pr_data={"reviews": [], "comments": []}
        )
        mock_set_running.assert_not_called()
        mock_auto_merge.assert_not_called()

    def test_update_done_label_triggers_auto_merge_when_enabled(self):
        with (
            patch("pr_label.contains_coderabbit_processing_marker", return_value=False),
            patch("pr_label.are_all_ci_checks_successful", return_value=True),
            patch("pr_label._set_pr_done_label") as mock_set_done,
            patch("pr_label.set_pr_running_label") as mock_set_running,
            patch(
                "pr_label._trigger_pr_auto_merge", return_value=(True, False)
            ) as mock_auto_merge,
            patch("pr_label._mark_pr_merged_label_if_needed") as mock_mark_merged,
        ):
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
            "owner/repo", 3, pr_data={"reviews": [], "comments": []}
        )
        mock_set_running.assert_not_called()
        mock_auto_merge.assert_called_once_with("owner/repo", 3)
        mock_mark_merged.assert_called_once_with("owner/repo", 3)

    def test_update_done_label_sets_running_when_review_fix_added_commit(self):
        with (
            patch("pr_label.contains_coderabbit_processing_marker") as mock_marker,
            patch("pr_label.are_all_ci_checks_successful") as mock_ci,
            patch("pr_label._set_pr_done_label") as mock_set_done,
            patch("pr_label.set_pr_running_label") as mock_set_running,
        ):
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
            "owner/repo", 1, pr_data={"reviews": [], "comments": []}
        )

    def test_update_done_label_sets_running_when_ci_not_success(self):
        with (
            patch("pr_label.contains_coderabbit_processing_marker", return_value=False),
            patch("pr_label.are_all_ci_checks_successful", return_value=False),
            patch("pr_label._set_pr_done_label") as mock_set_done,
            patch("pr_label.set_pr_running_label") as mock_set_running,
        ):
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
            "owner/repo", 2, pr_data={"reviews": [], "comments": []}
        )

    def test_update_done_label_skips_when_review_fix_failed(self):
        with (
            patch("pr_label._set_pr_done_label") as mock_set_done,
            patch("pr_label.set_pr_running_label") as mock_set_running,
        ):
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
            "owner/repo", 1, pr_data={"reviews": [], "comments": []}
        )

    def test_update_done_label_skips_when_dry_run(self):
        with patch("pr_label._set_pr_done_label") as mock_set_done:
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

    def test_update_done_label_skips_when_summarize_only(self):
        with patch("pr_label._set_pr_done_label") as mock_set_done:
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

    def test_update_done_label_skips_when_coderabbit_processing(self):
        with (
            patch("pr_label.contains_coderabbit_processing_marker", return_value=True),
            patch("pr_label.are_all_ci_checks_successful") as mock_ci,
            patch("pr_label._set_pr_done_label") as mock_set_done,
            patch("pr_label.set_pr_running_label") as mock_set_running,
        ):
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
            "owner/repo", 1, pr_data={"reviews": [], "comments": []}
        )

    def test_update_done_label_skips_when_coderabbit_rate_limit_active(self):
        with (
            patch("pr_label.contains_coderabbit_processing_marker", return_value=False),
            patch("pr_label.are_all_ci_checks_successful") as mock_ci,
            patch("pr_label._set_pr_done_label") as mock_set_done,
            patch("pr_label.set_pr_running_label") as mock_set_running,
        ):
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
            "owner/repo", 1, pr_data={"reviews": [], "comments": []}
        )
