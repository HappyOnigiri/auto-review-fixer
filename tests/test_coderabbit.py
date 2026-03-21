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
        # updated_at を1分前に設定（wait=5分11秒 → resume_after は4分後でまだ有効）
        updated_at = datetime.now(timezone.utc) - timedelta(minutes=1)
        updated_at_str = updated_at.strftime("%Y-%m-%dT%H:%M:%SZ")
        nitpick_at = updated_at + timedelta(minutes=4)
        nitpick_str = nitpick_at.strftime("%Y-%m-%dT%H:%M:%SZ")
        pr_data: PRData = {"reviews": []}
        issue_comments: list[GitHubComment] = [
            {
                "id": 55,
                "body": self.RATE_LIMIT_BODY,
                "user": {"login": "coderabbitai[bot]"},
                "updated_at": updated_at_str,
            },
            {
                "id": 56,
                "body": "Nitpick: consider adding a fallback link.",
                "user": {"login": "coderabbitai[bot]"},
                "updated_at": nitpick_str,
            },
        ]

        status = auto_fixer.get_active_coderabbit_rate_limit(
            pr_data, [], issue_comments
        )
        assert status is not None
        assert status.get("comment_id") == 55

    def test_get_active_coderabbit_rate_limit_returns_none_when_resume_after_passed(
        self,
    ):
        """resume_after を過ぎた rate limit は None を返す（期限切れ扱い）。"""
        # updated_at を1時間前に設定し、wait は5分 → resume_after は55分前
        updated_at = datetime.now(timezone.utc) - timedelta(hours=1)
        updated_at_str = updated_at.strftime("%Y-%m-%dT%H:%M:%SZ")
        pr_data: PRData = {"reviews": []}
        issue_comments: list[GitHubComment] = [
            {
                "id": 55,
                "body": self.RATE_LIMIT_BODY,
                "user": {"login": "coderabbitai[bot]"},
                "updated_at": updated_at_str,
            }
        ]

        status = auto_fixer.get_active_coderabbit_rate_limit(
            pr_data, [], issue_comments
        )
        assert status is None

    def test_get_active_coderabbit_rate_limit_returns_status_when_resume_after_not_yet(
        self,
    ):
        """resume_after をまだ過ぎていない rate limit は status を返す（active 扱い）。"""
        # updated_at を1分前に設定し、wait は5分 → resume_after は4分後
        updated_at = datetime.now(timezone.utc) - timedelta(minutes=1)
        updated_at_str = updated_at.strftime("%Y-%m-%dT%H:%M:%SZ")
        pr_data: PRData = {"reviews": []}
        issue_comments: list[GitHubComment] = [
            {
                "id": 55,
                "body": self.RATE_LIMIT_BODY,
                "user": {"login": "coderabbitai[bot]"},
                "updated_at": updated_at_str,
            }
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
        now = datetime.now(timezone.utc)
        threshold = now - timedelta(minutes=5)
        fresh_resume_at = now - timedelta(minutes=1)
        status: RateLimitStatus = {
            "updated_at": threshold,
            "resume_after": threshold,
        }
        issue_comments: list[GitHubComment] = [
            {
                "body": "@coderabbitai resume",
                "updated_at": fresh_resume_at.isoformat().replace("+00:00", "Z"),
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

    def test_maybe_auto_resume_reposts_when_resume_is_stale(self, mocker):
        """stale_minutes 経過した resume コメントは鮮度切れとみなし再投稿する。"""
        now = datetime.now(timezone.utc)
        threshold = now - timedelta(hours=2)
        stale_posted_at = now - timedelta(minutes=35)  # 30分より古い
        status: RateLimitStatus = {
            "updated_at": threshold,
            "resume_after": threshold,
        }
        issue_comments: list[GitHubComment] = [
            {
                "body": "@coderabbitai resume",
                "updated_at": stale_posted_at.isoformat().replace("+00:00", "Z"),
            }
        ]
        mock_post = mocker.patch("coderabbit._post_issue_comment", return_value=True)
        posted = coderabbit.maybe_auto_resume_coderabbit_review(
            repo="owner/repo",
            pr_number=1,
            issue_comments=issue_comments,
            rate_limit_status=status,
            auto_resume_enabled=True,
            remaining_resume_posts=1,
            dry_run=False,
            summarize_only=False,
            stale_minutes=30,
        )

        assert posted is True
        mock_post.assert_called_once()

    def test_maybe_auto_resume_skips_when_resume_is_fresh(self, mocker):
        """stale_minutes 以内の resume コメントは新鮮とみなし再投稿しない。"""
        now = datetime.now(timezone.utc)
        threshold = now - timedelta(hours=2)
        fresh_posted_at = now - timedelta(minutes=5)  # 30分より新しい
        status: RateLimitStatus = {
            "updated_at": threshold,
            "resume_after": threshold,
        }
        issue_comments: list[GitHubComment] = [
            {
                "body": "@coderabbitai resume",
                "updated_at": fresh_posted_at.isoformat().replace("+00:00", "Z"),
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
            stale_minutes=30,
        )

        assert posted is False
        mock_post.assert_not_called()

    def test_maybe_auto_resume_review_failed_reposts_when_stale(self, mocker):
        """review_failed: stale_minutes 経過した resume は鮮度切れとみなし再投稿する。"""
        now = datetime.now(timezone.utc)
        threshold = now - timedelta(hours=2)
        stale_posted_at = now - timedelta(minutes=35)
        status: ReviewFailedStatus = {"updated_at": threshold}
        issue_comments: list[GitHubComment] = [
            {
                "body": "@coderabbitai resume",
                "updated_at": stale_posted_at.isoformat().replace("+00:00", "Z"),
            }
        ]
        mock_post = mocker.patch("coderabbit._post_issue_comment", return_value=True)
        posted = coderabbit.maybe_auto_resume_coderabbit_review_failed(
            repo="owner/repo",
            pr_number=1,
            issue_comments=issue_comments,
            review_failed_status=status,
            auto_resume_enabled=True,
            remaining_resume_posts=1,
            dry_run=False,
            summarize_only=False,
            stale_minutes=30,
        )

        assert posted is True
        mock_post.assert_called_once()

    def test_maybe_auto_trigger_review_skipped_reposts_when_stale(self, mocker):
        """review_skipped: stale_minutes 経過した review コメントは鮮度切れとみなし再投稿する。"""
        now = datetime.now(timezone.utc)
        threshold = now - timedelta(hours=2)
        stale_posted_at = now - timedelta(minutes=35)
        status: ReviewSkippedStatus = {
            "updated_at": threshold,
            "reason": "draft_detected",
            "reason_label": "Draft detected",
        }
        issue_comments: list[GitHubComment] = [
            {
                "body": "@coderabbitai review",
                "updated_at": stale_posted_at.isoformat().replace("+00:00", "Z"),
            }
        ]
        mock_post = mocker.patch("coderabbit._post_issue_comment", return_value=True)
        posted = coderabbit.maybe_auto_trigger_coderabbit_review_skipped(
            repo="owner/repo",
            pr_number=1,
            issue_comments=issue_comments,
            review_skipped_status=status,
            auto_resume_enabled=True,
            trigger_enabled=True,
            remaining_resume_posts=1,
            dry_run=False,
            summarize_only=False,
            is_draft=False,
            stale_minutes=30,
        )

        assert posted is True
        mock_post.assert_called_once()

    def test_has_issue_comment_with_body_after_max_age(self):
        """max_age が指定された場合、古いコメントを無視する。"""
        now = datetime.now(timezone.utc)
        threshold = now - timedelta(hours=2)
        stale_at = now - timedelta(minutes=35)
        fresh_at = now - timedelta(minutes=5)

        stale_comment: GitHubComment = {
            "body": "@coderabbitai resume",
            "updated_at": stale_at.isoformat().replace("+00:00", "Z"),
        }
        fresh_comment: GitHubComment = {
            "body": "@coderabbitai resume",
            "updated_at": fresh_at.isoformat().replace("+00:00", "Z"),
        }

        # 鮮度切れ: stale_minutes=30 で 35 分前は False
        assert (
            coderabbit._has_issue_comment_with_body_after(
                [stale_comment],
                threshold,
                "@coderabbitai resume",
                max_age=timedelta(minutes=30),
            )
            is False
        )

        # 新鮮: stale_minutes=30 で 5 分前は True
        assert (
            coderabbit._has_issue_comment_with_body_after(
                [fresh_comment],
                threshold,
                "@coderabbitai resume",
                max_age=timedelta(minutes=30),
            )
            is True
        )

        # max_age=None なら従来通り古くても True
        assert (
            coderabbit._has_issue_comment_with_body_after(
                [stale_comment],
                threshold,
                "@coderabbitai resume",
                max_age=None,
            )
            is True
        )


class TestHasCoderabbitComments:
    """has_coderabbit_comments() のテスト。"""

    def _make_pr_data(self, reviews=None, comments=None) -> PRData:
        return {"reviews": reviews or [], "comments": comments or []}

    def test_no_comments_returns_false(self):
        pr_data = self._make_pr_data()
        assert coderabbit.has_coderabbit_comments(pr_data, [], []) is False

    def test_review_by_coderabbit_returns_true(self):
        pr_data = self._make_pr_data(
            reviews=[{"author": {"login": "coderabbitai"}, "body": "LGTM"}]
        )
        assert coderabbit.has_coderabbit_comments(pr_data, [], []) is True

    def test_review_by_coderabbit_bot_suffix_returns_true(self):
        pr_data = self._make_pr_data(
            reviews=[{"author": {"login": "coderabbitai[bot]"}, "body": "Nice!"}]
        )
        assert coderabbit.has_coderabbit_comments(pr_data, [], []) is True

    def test_comment_in_pr_comments_returns_true(self):
        pr_data = self._make_pr_data(
            comments=[{"author": {"login": "coderabbitai"}, "body": "Note"}]
        )
        assert coderabbit.has_coderabbit_comments(pr_data, [], []) is True

    def test_review_comment_returns_true(self):
        pr_data = self._make_pr_data()
        review_comments: list[GitHubComment] = [
            {"user": {"login": "coderabbitai"}, "body": "Inline note"}
        ]
        assert coderabbit.has_coderabbit_comments(pr_data, review_comments, []) is True

    def test_issue_comment_returns_true(self):
        pr_data = self._make_pr_data()
        issue_comments: list[GitHubComment] = [
            {"user": {"login": "coderabbitai[bot]"}, "body": "Rate limited"}
        ]
        assert coderabbit.has_coderabbit_comments(pr_data, [], issue_comments) is True

    def test_only_non_coderabbit_comments_returns_false(self):
        pr_data = self._make_pr_data(
            reviews=[{"author": {"login": "other-user"}, "body": "Review"}]
        )
        issue_comments: list[GitHubComment] = [
            {"user": {"login": "someone-else"}, "body": "Comment"}
        ]
        assert coderabbit.has_coderabbit_comments(pr_data, [], issue_comments) is False

    def test_issue_comments_none_does_not_raise(self):
        pr_data = self._make_pr_data()
        assert coderabbit.has_coderabbit_comments(pr_data, [], None) is False
