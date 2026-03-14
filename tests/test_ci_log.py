"""Unit tests for ci_log module."""

from unittest.mock import patch

import ci_log


def test_log_group_prints_when_ci(capsys):
    with patch.object(ci_log, "_IS_CI", True):
        ci_log.log_group("my group")

    captured = capsys.readouterr()
    assert captured.out == "::group::my group\n"


def test_log_group_silent_when_not_ci(capsys):
    with patch.object(ci_log, "_IS_CI", False):
        ci_log.log_group("my group")

    captured = capsys.readouterr()
    assert captured.out == ""


def test_log_endgroup_prints_when_ci(capsys):
    with patch.object(ci_log, "_IS_CI", True):
        ci_log.log_endgroup()

    captured = capsys.readouterr()
    assert captured.out == "::endgroup::\n"


def test_log_endgroup_silent_when_not_ci(capsys):
    with patch.object(ci_log, "_IS_CI", False):
        ci_log.log_endgroup()

    captured = capsys.readouterr()
    assert captured.out == ""


def test_log_error_exists():
    # TODO: log_error が未実装のため失敗する（ErrorCollector 動作検証用）
    assert hasattr(ci_log, "log_error"), "log_error is not implemented yet"


def test_log_warning_exists():
    # TODO: log_warning が未実装のため失敗する（ErrorCollector 動作検証用）
    assert hasattr(ci_log, "log_warning"), "log_warning is not implemented yet"
