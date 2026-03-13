"""Unit tests for summarizer module."""

from unittest.mock import MagicMock, patch

import pytest

import summarizer
from claude_limit import ClaudeCommandFailedError, ClaudeUsageLimitError


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
        fake_stdout = (
            '[{"id": "r1", "summary": "s1"}, {"id": "discussion_r2", "summary": "s2"}]'
        )
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

    def test_markdown_code_block_with_first_to_last_bracket_extraction(self):
        """JSON wrapped in ```json block and/or with prefix/suffix messages parses via first [ to last ]."""
        fake_stdout = """Here is the summarized result:

```json
[
  {"id": "r1", "summary": "summary one"},
  {"id": "r2", "summary": "summary two"}
]
```

Hope this helps!"""
        with patch("summarizer.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0,
                stdout=fake_stdout,
                stderr="",
            )
            result = summarizer.summarize_reviews(
                [{"id": "r1", "body": "x"}, {"id": "r2", "body": "y"}],
                [],
            )
            assert result == {"r1": "summary one", "r2": "summary two"}

    def test_success_logs_raw_output_in_foldable_group(self, capsys):
        """Successful summarization also prints raw output in group logs."""
        fake_stdout = '[{"id": "r1", "summary": "s1"}]'
        with (
            patch("summarizer.subprocess.run") as mock_run,
            patch("summarizer.log_group") as mock_group,
            patch("summarizer.log_endgroup") as mock_endgroup,
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

    def test_raw_output_preserves_leading_trailing_whitespace(self, capsys):
        """Raw output preserves leading/trailing whitespace without stripping."""
        fake_stdout = '\n  [{"id": "r1", "summary": "s1"}]  \n'
        fake_stderr = "\nerr\n"
        with (
            patch("summarizer.subprocess.run") as mock_run,
            patch("summarizer.log_group"),
            patch("summarizer.log_endgroup"),
        ):
            mock_run.return_value = MagicMock(
                returncode=0,
                stdout=fake_stdout,
                stderr=fake_stderr,
            )
            summarizer.summarize_reviews(
                [{"id": "r1", "body": "x"}],
                [],
                silent=False,
            )
        out = capsys.readouterr().out
        stdout_marker = "  --- stdout ---\n"
        stderr_marker = "  --- stderr ---\n"
        idx_stdout = out.index(stdout_marker) + len(stdout_marker)
        idx_stderr = out.index(stderr_marker)
        assert out[idx_stdout:idx_stderr] == fake_stdout
        idx_stderr_content = idx_stderr + len(stderr_marker)
        assert (
            out[idx_stderr_content : idx_stderr_content + len(fake_stderr)]
            == fake_stderr
        )

    def test_success_silent_does_not_log_raw_output(self):
        """Silent mode suppresses raw output logs even on success."""
        fake_stdout = '[{"id": "r1", "summary": "s1"}]'
        with (
            patch("summarizer.subprocess.run") as mock_run,
            patch("summarizer.log_group") as mock_group,
            patch("summarizer.log_endgroup") as mock_endgroup,
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

    def test_returncode_nonzero_raises(self):
        """Failed subprocess must fail fast."""
        with patch("summarizer.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=1,
                stdout="",
                stderr="error",
            )
            with pytest.raises(ClaudeCommandFailedError):
                summarizer.summarize_reviews(
                    [{"id": "r1", "body": "x"}],
                    [],
                )

    def test_usage_limit_nonzero_raises(self):
        """Usage limit must fail fast instead of fallback."""
        with patch("summarizer.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=1,
                stdout="You've hit your limit · resets Mar 13, 4pm (UTC)",
                stderr="",
            )
            with pytest.raises(ClaudeUsageLimitError):
                summarizer.summarize_reviews(
                    [{"id": "r1", "body": "x"}],
                    [],
                )

    def test_usage_limit_phrase_in_success_output_does_not_raise(self):
        """Success output containing marker phrase should not be misclassified."""
        fake_stdout = (
            "note: claude usage limit reached is one of known markers\n"
            '[{"id": "r1", "summary": "ok"}]'
        )
        with patch("summarizer.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0,
                stdout=fake_stdout,
                stderr="",
            )
            result = summarizer.summarize_reviews(
                [{"id": "r1", "body": "x"}],
                [],
            )
            assert result == {"r1": "ok"}

    def test_failure_logs_raw_output_in_foldable_group(self, capsys):
        """Failed summarization (returncode=1) prints raw output then raises."""
        fake_stdout = "some partial output"
        fake_stderr = "raw-stderr-error"
        with (
            patch("summarizer.subprocess.run") as mock_run,
            patch("summarizer.log_group") as mock_group,
            patch("summarizer.log_endgroup") as mock_endgroup,
        ):
            mock_run.return_value = MagicMock(
                returncode=1,
                stdout=fake_stdout,
                stderr=fake_stderr,
            )
            with pytest.raises(ClaudeCommandFailedError):
                summarizer.summarize_reviews(
                    [{"id": "r1", "body": "x"}],
                    [],
                    silent=False,
                )

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

    def test_subprocess_exception_raises(self):
        """subprocess.run raising an exception fails fast."""
        with patch("summarizer.subprocess.run") as mock_run:
            mock_run.side_effect = FileNotFoundError("claude not found")
            with pytest.raises(ClaudeCommandFailedError):
                summarizer.summarize_reviews(
                    [{"id": "r1", "body": "x"}],
                    [],
                )

    def test_subprocess_timeout_raises(self):
        """subprocess.run raising TimeoutError fails fast."""
        with patch("summarizer.subprocess.run") as mock_run:
            mock_run.side_effect = TimeoutError("timed out")
            with pytest.raises(ClaudeCommandFailedError):
                summarizer.summarize_reviews(
                    [{"id": "r1", "body": "x"}],
                    [],
                )

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
