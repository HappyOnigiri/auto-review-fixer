"""Unit tests for summarizer module."""

from unittest.mock import MagicMock, patch

import pytest

import summarizer


class TestSummarizeReviews:
    """Tests for summarize_reviews(). subprocess is always mocked."""

    def test_empty_input_returns_empty_dict_no_subprocess(self):
        """Zero items returns {} without calling claude."""
        with patch("summarizer.subprocess.run") as mock_run:
            result = summarizer.summarize_reviews([], [])
            assert result == {}
            mock_run.assert_not_called()

    def test_valid_json_array_success(self):
        """Pure JSON array parses correctly."""
        fake_stdout = '[{"id": "r1", "summary": "s1"}, {"id": "discussion_r2", "summary": "s2"}]'
        with patch("summarizer.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0,
                stdout=fake_stdout,
                stderr="",
            )
            result = summarizer.summarize_reviews(
                [{"id": "r1", "body": "x"}],
                [{"id": 2, "body": "y"}],
            )
            assert result == {"r1": "s1", "discussion_r2": "s2"}

    def test_prefixed_text_extracts_first_json_array(self):
        """Text before JSON array is skipped, first [..] is parsed."""
        fake_stdout = 'Here is the result:\n[{"id": "a", "summary": "b"}]'
        with patch("summarizer.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0,
                stdout=fake_stdout,
                stderr="",
            )
            result = summarizer.summarize_reviews(
                [{"id": "a", "body": "x"}],
                [],
            )
            assert result == {"a": "b"}

    def test_returncode_nonzero_returns_empty_dict(self):
        """Failed subprocess returns {}."""
        with patch("summarizer.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=1,
                stdout="",
                stderr="error",
            )
            result = summarizer.summarize_reviews(
                [{"id": "r1", "body": "x"}],
                [],
            )
            assert result == {}

    def test_invalid_json_returns_empty_dict(self):
        """Malformed JSON falls back to {}."""
        with patch("summarizer.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0,
                stdout="not valid json at all {{{",
                stderr="",
            )
            result = summarizer.summarize_reviews(
                [{"id": "r1", "body": "x"}],
                [],
            )
            assert result == {}

    def test_subprocess_exception_returns_empty_dict(self):
        """subprocess.run raising an exception falls back to {}."""
        with patch("summarizer.subprocess.run") as mock_run:
            mock_run.side_effect = FileNotFoundError("claude not found")
            result = summarizer.summarize_reviews(
                [{"id": "r1", "body": "x"}],
                [],
            )
            assert result == {}

    def test_subprocess_timeout_returns_empty_dict(self):
        """subprocess.run raising TimeoutError falls back to {}."""
        with patch("summarizer.subprocess.run") as mock_run:
            mock_run.side_effect = TimeoutError("timed out")
            result = summarizer.summarize_reviews(
                [{"id": "r1", "body": "x"}],
                [],
            )
            assert result == {}

    def test_comment_id_normalized_to_discussion_r(self):
        """Inline comment id becomes discussion_r<id>."""
        fake_stdout = '[{"id": "discussion_r99", "summary": "ok"}]'
        with patch("summarizer.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0,
                stdout=fake_stdout,
                stderr="",
            )
            result = summarizer.summarize_reviews(
                [],
                [{"id": 99, "body": "comment"}],
            )
            assert result == {"discussion_r99": "ok"}
