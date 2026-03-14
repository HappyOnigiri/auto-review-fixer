"""Unit tests for CodeRabbit rate limit helpers."""

from datetime import datetime, timedelta, timezone
from unittest.mock import Mock, patch


import auto_fixer
import coderabbit
from error_collector import ErrorCollector
from subprocess_helpers import SubprocessError


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
        assert status["comment_id"] == 55
        assert status["wait_text"] == "5 minutes and 11 seconds"
        assert status["wait_seconds"] == 311
        assert status["resume_after"].isoformat() == "2026-03-11T12:05:11+00:00"

    def test_get_active_coderabbit_rate_limit_ignores_stale_notice(self):
        pr_data = {
            "reviews": [
                {
                    "author": {"login": "coderabbitai"},
                    "submittedAt": "2026-03-11T12:10:00Z",
                }
            ]
        }
        issue_comments = [
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
        pr_data = {"reviews": []}
        issue_comments = [
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
        assert status["comment_id"] == 55

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
        assert status["comment_id"] == 77
        assert status["updated_at"].isoformat() == "2026-03-11T12:00:00+00:00"

    def test_get_active_coderabbit_review_failed_ignores_stale_notice(self):
        pr_data = {
            "reviews": [
                {
                    "author": {"login": "coderabbitai"},
                    "submittedAt": "2026-03-11T12:10:00Z",
                }
            ]
        }
        issue_comments = [
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

    def test_maybe_auto_resume_posts_comment_when_wait_elapsed(self):
        now = datetime.now(timezone.utc)
        status = {
            "updated_at": now,
            "resume_after": now - timedelta(seconds=1),
        }
        with patch("coderabbit._post_issue_comment", return_value=True) as mock_post:
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

    def test_maybe_auto_resume_skips_when_resume_already_exists(self):
        threshold = datetime(2026, 3, 11, 12, 0, tzinfo=timezone.utc)
        status = {
            "updated_at": threshold,
            "resume_after": threshold,
        }
        issue_comments = [
            {
                "body": "@coderabbitai resume",
                "updated_at": "2026-03-11T12:01:00Z",
            }
        ]
        with patch("coderabbit._post_issue_comment") as mock_post:
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

    def test_maybe_auto_resume_skips_when_per_run_limit_reached(self):
        threshold = datetime.now(timezone.utc)
        status = {
            "updated_at": threshold,
            "resume_after": threshold,
        }
        with patch("coderabbit._post_issue_comment") as mock_post:
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

    def test_maybe_auto_resume_review_failed_posts_comment(self):
        threshold = datetime.now(timezone.utc)
        status = {
            "updated_at": threshold,
        }
        with patch("coderabbit._post_issue_comment", return_value=True) as mock_post:
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

    def test_maybe_auto_resume_review_failed_skips_when_per_run_limit_reached(self):
        threshold = datetime.now(timezone.utc)
        status = {
            "updated_at": threshold,
        }
        with patch("coderabbit._post_issue_comment") as mock_post:
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

    def test_maybe_auto_resume_posts_comment_with_error_collector(self):
        """error_collector が _post_issue_comment に伝わることを確認。"""
        now = datetime.now(timezone.utc)
        status = {
            "updated_at": now,
            "resume_after": now - timedelta(seconds=1),
        }
        ec = ErrorCollector()
        with patch(
            "coderabbit.run_command",
            return_value=Mock(returncode=1, stdout="", stderr="forbidden"),
        ):
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

    def test_post_issue_comment_subprocess_error_adds_pr_error(self):
        ec = ErrorCollector()
        with patch("coderabbit.run_command", side_effect=SubprocessError("net error")):
            result = coderabbit._post_issue_comment(
                "owner/repo", 5, "hello", error_collector=ec
            )
        assert result is False
        assert ec.has_errors
        assert ec._errors[0].scope == "owner/repo#5"
