"""Unit tests for error_collector module."""

from unittest.mock import patch

import ci_log
from error_collector import ErrorCollector


def test_initial_no_errors():
    collector = ErrorCollector()
    assert not collector.has_errors


def test_add_repo_error():
    collector = ErrorCollector()
    collector.add_repo_error("owner/repo", "something failed")
    assert collector.has_errors
    assert len(collector._errors) == 1
    assert collector._errors[0].scope == "owner/repo"
    assert collector._errors[0].message == "something failed"


def test_add_pr_error_scope_format():
    collector = ErrorCollector()
    collector.add_pr_error("owner/repo", 42, "pr failed")
    assert collector._errors[0].scope == "owner/repo#42"
    assert collector._errors[0].message == "pr failed"


def test_has_errors_true_after_add():
    collector = ErrorCollector()
    assert not collector.has_errors
    collector.add_repo_error("owner/repo", "err")
    assert collector.has_errors


def test_print_summary_empty(capsys):
    collector = ErrorCollector()
    collector.print_summary()
    captured = capsys.readouterr()
    assert captured.out == ""


def test_print_summary_with_errors(capsys):
    collector = ErrorCollector()
    collector.add_repo_error("owner/repo", "fetch failed")
    collector.add_pr_error("owner/repo", 7, "pr error")

    with patch.object(ci_log, "_IS_CI", False):
        collector.print_summary()

    captured = capsys.readouterr()
    assert "Error summary (2 error(s))" in captured.out
    assert "fetch failed" in captured.err
    assert "owner/repo#7" in captured.err
    assert "pr error" in captured.err
