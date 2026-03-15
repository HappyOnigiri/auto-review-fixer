"""Unit tests for CodeRabbit rate limit helpers."""

from datetime import datetime, timedelta, timezone


import auto_fixer
import coderabbit
from coderabbit import RateLimitStatus, ReviewFailedStatus, ReviewSkippedStatus
from error_collector import ErrorCollector
from subprocess_helpers import SubprocessError
from type_defs import GitHubComment, PRData


class TestCodeRabbitRateLimitHelpers:
    RATE_LIMIT_BODY = """
> [!WARNING]
> ## Rate limit exceeded
>
> `@HappyOnigiri` has exceeded the limit for the number of commits that can be reviewed per hour. Please wait **5 minutes and 11 seconds** before requesting another review.
""".strip()
    REVIEW_FAILED_BODY = """
> [!CAUTION]
> ## Review failed
>
> The head commit changed during the review from 8c95504f7bdc7b6f178d693ad16194afa00240bd to 769422c80b767b53c7cd900db05a71bc8713b9a8.
""".strip()
    REVIEW_SKIPPED_DRAFT_BODY = """
> [!IMPORTANT]
> ## Review skipped
>
> Draft detected.
>
> Please check the settings in the CodeRabbit UI or the `.coderabbit.yaml` file in this repository. To trigger a single review, invoke the `@coderabbitai review` command.
""".strip()

    def test_extract_coderabbit_rate_limit_status(self):
        status = coderabbit._extract_coderabbit_rate_limit_status(
            {
                "id": 55,
                "body": self.RATE_LIMIT_BODY,
                "updated_at": "2026-03-11T12:00:00Z",
                "html_url": "https://github.com/owner/repo/issues/1#issuecomment-55",
            }
        )

        assert status is not None
        assert status.get("comment_id") == 55
        assert status.get("wait_text") == "5 minutes and 11 seconds"
        assert status.get("wait_seconds") == 311
        resume_after = status.get("resume_after")
        assert resume_after is not None
        assert resume_after.isoformat() == "2026-03-11T12:05:11+00:00"

    def test_get_active_coderabbit_rate_limit_ignores_stale_notice(self):
        pr_data: PRData = {
            "reviews": [
                {
                    "author": {"login": "coderabbitai"},
                    "submittedAt": "2026-03-11T12:10:00Z",
                }
            ]
        }
        issue_comments: list[GitHubComment] = [
            {
                "id": 55,
                "body": self.RATE_LIMIT_BODY,
                "user": {"login": "coderabbitai[bot]"},
                "updated_at": "2026-03-11T12:00:00Z",
            }
        ]

        status = auto_fixer.get_active_coderabbit_rate_limit(
            pr_data, [], issue_comments
        )
        assert status is None

    def test_get_active_coderabbit_rate_limit_keeps_active_when_only_issue_comment_after(
        self,
    ):
        """Rate limit stays active when CodeRabbit posts an issue comment (e.g. Nitpick)
        after the rate limit notice, but no new review submission.

        Issue comments can be from different runs; only a review submission indicates
        the rate limit is resolved. See: GamePortal PR #44.
        """
        pr_data: PRData = {"reviews": []}
        issue_comments: list[GitHubComment] = [
            {
                "id": 55,
                "body": self.RATE_LIMIT_BODY,
                "user": {"login": "coderabbitai[bot]"},
                "updated_at": "2026-03-11T12:00:00Z",
            },
            {
                "id": 56,
                "body": "Nitpick: consider adding a fallback link.",
                "user": {"login": "coderabbitai[bot]"},
                "updated_at": "2026-03-11T12:04:00Z",
            },
        ]

        status = auto_fixer.get_active_coderabbit_rate_limit(
            pr_data, [], issue_comments
        )
        assert status is not None
        assert status.get("comment_id") == 55

    def test_extract_coderabbit_review_failed_status(self):
        status = coderabbit._extract_coderabbit_review_failed_status(
            {
                "id": 77,
                "body": self.REVIEW_FAILED_BODY,
                "updated_at": "2026-03-11T12:00:00Z",
                "html_url": "https://github.com/owner/repo/issues/1#issuecomment-77",
            }
        )

        assert status is not None
        assert status.get("comment_id") == 77
        updated_at_77 = status.get("updated_at")
        assert updated_at_77 is not None
        assert updated_at_77.isoformat() == "2026-03-11T12:00:00+00:00"

    def test_extract_coderabbit_review_skipped_status(self):
        status = coderabbit._extract_coderabbit_review_skipped_status(
            {
                "id": 88,
                "body": self.REVIEW_SKIPPED_DRAFT_BODY,
                "updated_at": "2026-03-11T12:00:00Z",
                "html_url": "https://github.com/owner/repo/issues/1#issuecomment-88",
            }
        )

        assert status is not None
        assert status.get("comment_id") == 88
        assert status.get("reason") == "draft_detected"
        assert status.get("reason_label") == "Draft detected"
        updated_at_88 = status.get("updated_at")
        assert updated_at_88 is not None
        assert updated_at_88.isoformat() == "2026-03-11T12:00:00+00:00"

    def test_get_active_coderabbit_review_failed_ignores_stale_notice(self):
        pr_data: PRData = {
            "reviews": [
                {
                    "author": {"login": "coderabbitai"},
                    "submittedAt": "2026-03-11T12:10:00Z",
                }
            ]
        }
        issue_comments: list[GitHubComment] = [
            {
                "id": 77,
                "body": self.REVIEW_FAILED_BODY,
                "user": {"login": "coderabbitai[bot]"},
                "updated_at": "2026-03-11T12:00:00Z",
            }
        ]

        status = auto_fixer.get_active_coderabbit_review_failed(
            pr_data, [], issue_comments
        )
        assert status is None

    def test_get_active_coderabbit_review_skipped_ignores_stale_notice(self):
        pr_data: PRData = {
            "reviews": [
                {
                    "author": {"login": "coderabbitai"},
                    "submittedAt": "2026-03-11T12:10:00Z",
                }
            ]
        }
        issue_comments: list[GitHubComment] = [
            {
                "id": 88,
                "body": self.REVIEW_SKIPPED_DRAFT_BODY,
                "user": {"login": "coderabbitai[bot]"},
                "updated_at": "2026-03-11T12:00:00Z",
            }
        ]

        status = auto_fixer.get_active_coderabbit_review_skipped(
            pr_data, [], issue_comments
        )
        assert status is None

    def test_maybe_auto_resume_posts_comment_when_wait_elapsed(self, mocker):
        now = datetime.now(timezone.utc)
        status: RateLimitStatus = {
            "updated_at": now,
            "resume_after": now - timedelta(seconds=1),
        }
        mock_post = mocker.patch("coderabbit._post_issue_comment", return_value=True)
        posted = coderabbit.maybe_auto_resume_coderabbit_review(
            repo="owner/repo",
            pr_number=1,
            issue_comments=[],
            rate_limit_status=status,
            auto_resume_enabled=True,
            remaining_resume_posts=1,
            dry_run=False,
            summarize_only=False,
        )

        assert posted is True
        mock_post.assert_called_once_with(
            "owner/repo", 1, "@coderabbitai resume", error_collector=None
        )

    def test_maybe_auto_resume_skips_when_resume_already_exists(self, mocker):
        threshold = datetime(2026, 3, 11, 12, 0, tzinfo=timezone.utc)
        status: RateLimitStatus = {
            "updated_at": threshold,
            "resume_after": threshold,
        }
        issue_comments: list[GitHubComment] = [
            {
                "body": "@coderabbitai resume",
                "updated_at": "2026-03-11T12:01:00Z",
            }
        ]
        mock_post = mocker.patch("coderabbit._post_issue_comment")
        posted = coderabbit.maybe_auto_resume_coderabbit_review(
            repo="owner/repo",
            pr_number=1,
            issue_comments=issue_comments,
            rate_limit_status=status,
            auto_resume_enabled=True,
            remaining_resume_posts=1,
            dry_run=False,
            summarize_only=False,
        )

        assert posted is False
        mock_post.assert_not_called()

    def test_maybe_auto_resume_skips_when_per_run_limit_reached(self, mocker):
        threshold = datetime.now(timezone.utc)
        status: RateLimitStatus = {
            "updated_at": threshold,
            "resume_after": threshold,
        }
        mock_post = mocker.patch("coderabbit._post_issue_comment")
        posted = coderabbit.maybe_auto_resume_coderabbit_review(
            repo="owner/repo",
            pr_number=1,
            issue_comments=[],
            rate_limit_status=status,
            auto_resume_enabled=True,
            remaining_resume_posts=0,
            dry_run=False,
            summarize_only=False,
        )
        assert posted is False
        mock_post.assert_not_called()

    def test_maybe_auto_resume_review_failed_posts_comment(self, mocker):
        threshold = datetime.now(timezone.utc)
        status: ReviewFailedStatus = {
            "updated_at": threshold,
        }
        mock_post = mocker.patch("coderabbit._post_issue_comment", return_value=True)
        posted = coderabbit.maybe_auto_resume_coderabbit_review_failed(
            repo="owner/repo",
            pr_number=1,
            issue_comments=[],
            review_failed_status=status,
            auto_resume_enabled=True,
            remaining_resume_posts=1,
            dry_run=False,
            summarize_only=False,
        )

        assert posted is True
        mock_post.assert_called_once_with(
            "owner/repo", 1, "@coderabbitai resume", error_collector=None
        )

    def test_maybe_auto_resume_review_failed_skips_when_per_run_limit_reached(
        self, mocker
    ):
        threshold = datetime.now(timezone.utc)
        status: ReviewFailedStatus = {
            "updated_at": threshold,
        }
        mock_post = mocker.patch("coderabbit._post_issue_comment")
        posted = coderabbit.maybe_auto_resume_coderabbit_review_failed(
            repo="owner/repo",
            pr_number=1,
            issue_comments=[],
            review_failed_status=status,
            auto_resume_enabled=True,
            remaining_resume_posts=0,
            dry_run=False,
            summarize_only=False,
        )
        assert posted is False
        mock_post.assert_not_called()

    def test_maybe_auto_resume_skips_when_rate_limit_trigger_disabled(self, mocker):
        threshold = datetime.now(timezone.utc)
        status: RateLimitStatus = {
            "updated_at": threshold,
            "resume_after": threshold,
        }
        mock_post = mocker.patch("coderabbit._post_issue_comment")
        posted = coderabbit.maybe_auto_resume_coderabbit_review(
            repo="owner/repo",
            pr_number=1,
            issue_comments=[],
            rate_limit_status=status,
            auto_resume_enabled=True,
            remaining_resume_posts=1,
            dry_run=False,
            summarize_only=False,
            trigger_enabled=False,
        )
        assert posted is False
        mock_post.assert_not_called()

    def test_maybe_auto_trigger_review_skipped_posts_review_comment(self, mocker):
        threshold = datetime.now(timezone.utc)
        status: ReviewSkippedStatus = {
            "updated_at": threshold,
            "reason": "draft_detected",
            "reason_label": "Draft detected",
        }
        mock_post = mocker.patch("coderabbit._post_issue_comment", return_value=True)
        posted = coderabbit.maybe_auto_trigger_coderabbit_review_skipped(
            repo="owner/repo",
            pr_number=1,
            issue_comments=[],
            review_skipped_status=status,
            auto_resume_enabled=True,
            trigger_enabled=True,
            remaining_resume_posts=1,
            dry_run=False,
            summarize_only=False,
            is_draft=False,
        )

        assert posted is True
        mock_post.assert_called_once_with(
            "owner/repo", 1, "@coderabbitai review", error_collector=None
        )

    def test_maybe_auto_trigger_review_skipped_skips_when_pr_is_draft(self, mocker):
        threshold = datetime.now(timezone.utc)
        status: ReviewSkippedStatus = {
            "updated_at": threshold,
            "reason": "draft_detected",
            "reason_label": "Draft detected",
        }
        mock_post = mocker.patch("coderabbit._post_issue_comment")
        posted = coderabbit.maybe_auto_trigger_coderabbit_review_skipped(
            repo="owner/repo",
            pr_number=1,
            issue_comments=[],
            review_skipped_status=status,
            auto_resume_enabled=True,
            trigger_enabled=True,
            remaining_resume_posts=1,
            dry_run=False,
            summarize_only=False,
            is_draft=True,
        )

        assert posted is False
        mock_post.assert_not_called()

    def test_maybe_auto_resume_posts_comment_with_error_collector(
        self, mocker, make_cmd_result
    ):
        """error_collector が _post_issue_comment に伝わることを確認。"""
        now = datetime.now(timezone.utc)
        status: RateLimitStatus = {
            "updated_at": now,
            "resume_after": now - timedelta(seconds=1),
        }
        ec = ErrorCollector()
        mocker.patch(
            "coderabbit.run_command",
            return_value=make_cmd_result("", returncode=1, stderr="forbidden"),
        )
        posted = coderabbit.maybe_auto_resume_coderabbit_review(
            repo="owner/repo",
            pr_number=2,
            issue_comments=[],
            rate_limit_status=status,
            auto_resume_enabled=True,
            remaining_resume_posts=1,
            dry_run=False,
            summarize_only=False,
            error_collector=ec,
        )
        assert posted is False
        assert ec.has_errors
        assert ec._errors[0].scope == "owner/repo#2"

    def test_post_issue_comment_subprocess_error_adds_pr_error(self, mocker):
        ec = ErrorCollector()
        mocker.patch("coderabbit.run_command", side_effect=SubprocessError("net error"))
        result = coderabbit._post_issue_comment(
            "owner/repo", 5, "hello", error_collector=ec
        )
        assert result is False
        assert ec.has_errors
        assert ec._errors[0].scope == "owner/repo#5"
