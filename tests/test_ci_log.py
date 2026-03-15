"""Unit tests for ci_log module."""

import ci_log


def test_log_group_prints_when_ci(capsys, mocker):
    mocker.patch.object(ci_log, "_IS_CI", True)
    ci_log.log_group("my group")

    captured = capsys.readouterr()
    assert captured.out == "::group::my group\n"


def test_log_group_silent_when_not_ci(capsys, mocker):
    mocker.patch.object(ci_log, "_IS_CI", False)
    ci_log.log_group("my group")

    captured = capsys.readouterr()
    assert captured.out == ""


def test_log_endgroup_prints_when_ci(capsys, mocker):
    mocker.patch.object(ci_log, "_IS_CI", True)
    ci_log.log_endgroup()

    captured = capsys.readouterr()
    assert captured.out == "::endgroup::\n"


def test_log_endgroup_silent_when_not_ci(capsys, mocker):
    mocker.patch.object(ci_log, "_IS_CI", False)
    ci_log.log_endgroup()

    captured = capsys.readouterr()
    assert captured.out == ""


def test_log_error_ci(capsys, mocker):
    mocker.patch.object(ci_log, "_IS_CI", True)
    ci_log.log_error("something broke")

    captured = capsys.readouterr()
    assert captured.out == "::error::something broke\n"


def test_log_error_non_ci(capsys, mocker):
    mocker.patch.object(ci_log, "_IS_CI", False)
    ci_log.log_error("something broke")

    captured = capsys.readouterr()
    assert captured.err == "ERROR: something broke\n"


def test_log_error_with_title_ci(capsys, mocker):
    mocker.patch.object(ci_log, "_IS_CI", True)
    ci_log.log_error("something broke", title="owner/repo")

    captured = capsys.readouterr()
    assert captured.out == "::error title=owner/repo::something broke\n"


def test_log_error_with_title_non_ci(capsys, mocker):
    mocker.patch.object(ci_log, "_IS_CI", False)
    ci_log.log_error("something broke", title="owner/repo")

    captured = capsys.readouterr()
    assert captured.err == "ERROR: [owner/repo] something broke\n"


def test_log_warning_ci(capsys, mocker):
    mocker.patch.object(ci_log, "_IS_CI", True)
    ci_log.log_warning("watch out")

    captured = capsys.readouterr()
    assert captured.out == "::warning::watch out\n"


def test_log_warning_non_ci(capsys, mocker):
    mocker.patch.object(ci_log, "_IS_CI", False)
    ci_log.log_warning("watch out")

    captured = capsys.readouterr()
    assert captured.err == "WARNING: watch out\n"


def test_log_warning_with_title_ci(capsys, mocker):
    mocker.patch.object(ci_log, "_IS_CI", True)
    ci_log.log_warning("watch out", title="owner/repo")

    captured = capsys.readouterr()
    assert captured.out == "::warning title=owner/repo::watch out\n"


def test_log_warning_with_title_non_ci(capsys, mocker):
    mocker.patch.object(ci_log, "_IS_CI", False)
    ci_log.log_warning("watch out", title="owner/repo")

    captured = capsys.readouterr()
    assert captured.err == "WARNING: [owner/repo] watch out\n"
