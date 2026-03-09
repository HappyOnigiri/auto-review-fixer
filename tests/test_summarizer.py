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

    def test_success_logs_raw_output_in_foldable_group(self, capsys):
        """Successful summarization also prints raw output in group logs."""
        fake_stdout = '[{"id": "r1", "summary": "s1"}]'
        with (
            patch("summarizer.subprocess.run") as mock_run,
            patch("summarizer._log_group") as mock_group,
            patch("summarizer._log_endgroup") as mock_endgroup,
        ):
            mock_run.return_value = MagicMock(
                returncode=0,
                stdout=fake_stdout,
                stderr="raw-stderr",
            )
            result = summarizer.summarize_reviews(
                [{"id": "r1", "body": "x"}],
                [],
                silent=False,
            )

        assert result == {"r1": "s1"}
        mock_group.assert_any_call("Summarizer raw output (exit 0)")
        mock_endgroup.assert_called()
        out = capsys.readouterr().out
        assert "--- stdout ---" in out
        assert fake_stdout in out
        assert "--- stderr ---" in out
        assert "raw-stderr" in out

    def test_success_silent_does_not_log_raw_output(self):
        """Silent mode suppresses raw output logs even on success."""
        fake_stdout = '[{"id": "r1", "summary": "s1"}]'
        with (
            patch("summarizer.subprocess.run") as mock_run,
            patch("summarizer._log_group") as mock_group,
            patch("summarizer._log_endgroup") as mock_endgroup,
        ):
            mock_run.return_value = MagicMock(
                returncode=0,
                stdout=fake_stdout,
                stderr="raw-stderr",
            )
            result = summarizer.summarize_reviews(
                [{"id": "r1", "body": "x"}],
                [],
                silent=True,
            )

        assert result == {"r1": "s1"}
        mock_group.assert_called_once_with("Summarizer command details")
        mock_endgroup.assert_called_once()

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

    def test_failure_logs_raw_output_in_foldable_group(self, capsys):
        """Failed summarization (returncode=1) still prints raw output in group logs."""
        fake_stdout = "some partial output"
        fake_stderr = "raw-stderr-error"
        with (
            patch("summarizer.subprocess.run") as mock_run,
            patch("summarizer._log_group") as mock_group,
            patch("summarizer._log_endgroup") as mock_endgroup,
        ):
            mock_run.return_value = MagicMock(
                returncode=1,
                stdout=fake_stdout,
                stderr=fake_stderr,
            )
            result = summarizer.summarize_reviews(
                [{"id": "r1", "body": "x"}],
                [],
                silent=False,
            )

        assert result == {}
        mock_group.assert_any_call("Summarizer raw output (exit 1)")
        mock_endgroup.assert_called()
        out = capsys.readouterr().out
        assert "--- stderr ---" in out
        assert fake_stderr in out

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
