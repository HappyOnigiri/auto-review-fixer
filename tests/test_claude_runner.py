"""Unit tests for claude_runner and run_claude_prompt."""

import json
import os
from unittest.mock import Mock, patch

import pytest

import auto_fixer
import claude_runner
from claude_limit import ClaudeCommandFailedError, ClaudeUsageLimitError


class TestSetupClaudeSettings:
    """Tests for setup_claude_settings()."""

    def _make_works_dir(self, tmp_path):
        works_dir = tmp_path / "works" / "owner" / "repo"
        works_dir.mkdir(parents=True)
        git_info = works_dir / ".git" / "info"
        git_info.mkdir(parents=True)
        return works_dir

    def test_default_settings_written(self, tmp_path):
        works_dir = self._make_works_dir(tmp_path)
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("REFIX_CLAUDE_SETTINGS", None)
            claude_runner.setup_claude_settings(works_dir)
        settings = json.loads(
            (works_dir / ".claude" / "settings.local.json").read_text()
        )
        assert settings["includeCoAuthoredBy"] is False
        assert settings["attribution"] == {"commit": "", "pr": ""}

    def test_env_override_merges(self, tmp_path):
        works_dir = self._make_works_dir(tmp_path)
        override = json.dumps({"includeCoAuthoredBy": True})
        with patch.dict(os.environ, {"REFIX_CLAUDE_SETTINGS": override}, clear=False):
            claude_runner.setup_claude_settings(works_dir)
        settings = json.loads(
            (works_dir / ".claude" / "settings.local.json").read_text()
        )
        assert settings["includeCoAuthoredBy"] is True
        assert "attribution" in settings

    def test_env_override_deep_merges_nested(self, tmp_path):
        works_dir = self._make_works_dir(tmp_path)
        override = json.dumps({"attribution": {"commit": "custom"}})
        with patch.dict(os.environ, {"REFIX_CLAUDE_SETTINGS": override}, clear=False):
            claude_runner.setup_claude_settings(works_dir)
        settings = json.loads(
            (works_dir / ".claude" / "settings.local.json").read_text()
        )
        assert settings["attribution"]["commit"] == "custom"
        assert settings["attribution"]["pr"] == ""

    def test_exclude_entry_added(self, tmp_path):
        works_dir = self._make_works_dir(tmp_path)
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("REFIX_CLAUDE_SETTINGS", None)
            claude_runner.setup_claude_settings(works_dir)
        exclude = (works_dir / ".git" / "info" / "exclude").read_text()
        assert ".claude/settings.local.json" in exclude

    def test_exclude_not_duplicated(self, tmp_path):
        works_dir = self._make_works_dir(tmp_path)
        exclude_file = works_dir / ".git" / "info" / "exclude"
        exclude_file.write_text(".claude/settings.local.json\n")
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("REFIX_CLAUDE_SETTINGS", None)
            claude_runner.setup_claude_settings(works_dir)
        lines = [
            line
            for line in exclude_file.read_text().splitlines()
            if line == ".claude/settings.local.json"
        ]
        assert len(lines) == 1

    def test_existing_settings_merged(self, tmp_path):
        works_dir = self._make_works_dir(tmp_path)
        claude_dir = works_dir / ".claude"
        claude_dir.mkdir(exist_ok=True)
        existing = {"customKey": "customValue", "includeCoAuthoredBy": True}
        (claude_dir / "settings.local.json").write_text(
            json.dumps(existing), encoding="utf-8"
        )
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("REFIX_CLAUDE_SETTINGS", None)
            claude_runner.setup_claude_settings(works_dir)
        settings = json.loads(
            (claude_dir / "settings.local.json").read_text(encoding="utf-8")
        )
        assert settings["customKey"] == "customValue"
        assert settings["includeCoAuthoredBy"] is False
        assert settings["attribution"] == {"commit": "", "pr": ""}

    def test_invalid_env_json_raises(self, tmp_path):
        works_dir = self._make_works_dir(tmp_path)
        with patch.dict(
            os.environ, {"REFIX_CLAUDE_SETTINGS": "{not-json"}, clear=False
        ):
            with pytest.raises(ValueError):
                claude_runner.setup_claude_settings(works_dir)

    def test_non_object_env_json_raises(self, tmp_path):
        works_dir = self._make_works_dir(tmp_path)
        with patch.dict(os.environ, {"REFIX_CLAUDE_SETTINGS": "[]"}, clear=False):
            with pytest.raises(ValueError):
                claude_runner.setup_claude_settings(works_dir)


class TestRunClaudePrompt:
    def test_usage_limit_raises(self, tmp_path, capsys):
        process = Mock()
        process.communicate.return_value = (
            "You've hit your limit · resets Mar 13, 4pm (UTC)",
            "",
        )
        process.returncode = 1
        report_path = tmp_path / "pr_1_review-fix.md"

        def popen_side_effect(*args, **kwargs):
            report_path.write_text("- setup failed once", encoding="utf-8")
            return process

        with (
            patch(
                "claude_runner.subprocess.run",
                return_value=Mock(returncode=0, stdout="abc123\n", stderr=""),
            ),
            patch("claude_runner.subprocess.Popen", side_effect=popen_side_effect),
            patch("auto_fixer.log_group"),
            patch("auto_fixer.log_endgroup"),
        ):
            with pytest.raises(ClaudeUsageLimitError):
                auto_fixer.run_claude_prompt(
                    works_dir=tmp_path,
                    prompt="<instructions>fix</instructions>",
                    report_path=str(report_path.resolve()),
                    report_enabled=True,
                    model="sonnet",
                    silent=True,
                    phase_label="review-fix",
                )
        err = capsys.readouterr().err
        assert "[report review-fix]" in err
        assert "setup failed once" in err

    def test_nonzero_exit_raises_command_failed(self, tmp_path, capsys):
        process = Mock()
        process.communicate.return_value = ("API Error: invalid header", "")
        process.returncode = 1
        report_path = tmp_path / "pr_1_review-fix.md"

        def popen_side_effect(*args, **kwargs):
            report_path.write_text("- missing context file", encoding="utf-8")
            return process

        with (
            patch(
                "claude_runner.subprocess.run",
                return_value=Mock(returncode=0, stdout="abc123\n", stderr=""),
            ),
            patch("claude_runner.subprocess.Popen", side_effect=popen_side_effect),
            patch("auto_fixer.log_group"),
            patch("auto_fixer.log_endgroup"),
        ):
            with pytest.raises(ClaudeCommandFailedError):
                auto_fixer.run_claude_prompt(
                    works_dir=tmp_path,
                    prompt="<instructions>fix</instructions>",
                    report_path=str(report_path.resolve()),
                    report_enabled=True,
                    model="sonnet",
                    silent=True,
                    phase_label="review-fix",
                )
        err = capsys.readouterr().err
        assert "[report review-fix]" in err
        assert "missing context file" in err

    def test_success_output_with_limit_phrase_does_not_raise(self, tmp_path):
        process = Mock()
        process.communicate.return_value = (
            "markers include: claude usage limit reached",
            "",
        )
        process.returncode = 0
        captured_prompt = ""

        def popen_side_effect(*args, **kwargs):
            nonlocal captured_prompt
            prompt_file = tmp_path / "_review_prompt.md"
            captured_prompt = prompt_file.read_text(encoding="utf-8")
            return process

        report_path = str((tmp_path / "pr_1_review-fix.md").resolve())
        with (
            patch(
                "claude_runner.subprocess.run",
                side_effect=[
                    Mock(returncode=0, stdout="abc123\n", stderr=""),
                    Mock(returncode=0, stdout="", stderr=""),
                ],
            ),
            patch("claude_runner.subprocess.Popen", side_effect=popen_side_effect),
            patch("auto_fixer.log_group"),
            patch("auto_fixer.log_endgroup"),
        ):
            result = auto_fixer.run_claude_prompt(
                works_dir=tmp_path,
                prompt="<instructions>fix</instructions>",
                report_path=report_path,
                report_enabled=True,
                model="sonnet",
                silent=True,
                phase_label="review-fix",
            )
            assert result == ""
            assert "<runtime_pain_report>" in captured_prompt
            assert report_path in captured_prompt
            assert "### YYYY-MM-DD hh:mm:ss UTC {file_path} {title}" in captured_prompt

    def test_success_shows_report_when_not_silent_with_content(self, tmp_path, capsys):
        """When silent=False and report has content, report is shown in stderr."""
        process = Mock()
        process.communicate.return_value = ("", "")
        process.returncode = 0
        report_path = tmp_path / "pr_1_review-fix.md"

        def popen_side_effect(*args, **kwargs):
            report_path.write_text(
                "- ambiguous review comment\n- missing file", encoding="utf-8"
            )
            return process

        with (
            patch(
                "claude_runner.subprocess.run",
                return_value=Mock(returncode=0, stdout="", stderr=""),
            ),
            patch("claude_runner.subprocess.Popen", side_effect=popen_side_effect),
            patch("auto_fixer.log_group"),
            patch("auto_fixer.log_endgroup"),
        ):
            auto_fixer.run_claude_prompt(
                works_dir=tmp_path,
                prompt="<instructions>fix</instructions>",
                report_path=str(report_path.resolve()),
                report_enabled=True,
                model="sonnet",
                silent=False,
                phase_label="review-fix",
            )
        err = capsys.readouterr().err
        assert "[report review-fix]" in err
        assert "ambiguous review comment" in err
        assert "missing file" in err

    def test_success_shows_empty_when_not_silent_with_empty_report(
        self, tmp_path, capsys
    ):
        """When silent=False and report is empty, a neutral message is shown."""
        process = Mock()
        process.communicate.return_value = ("", "")
        process.returncode = 0
        report_path = tmp_path / "pr_1_review-fix.md"
        report_path.write_text("", encoding="utf-8")

        with (
            patch(
                "claude_runner.subprocess.run",
                return_value=Mock(returncode=0, stdout="", stderr=""),
            ),
            patch("claude_runner.subprocess.Popen", return_value=process),
            patch("auto_fixer.log_group"),
            patch("auto_fixer.log_endgroup"),
        ):
            auto_fixer.run_claude_prompt(
                works_dir=tmp_path,
                prompt="<instructions>fix</instructions>",
                report_path=str(report_path.resolve()),
                report_enabled=True,
                model="sonnet",
                silent=False,
                phase_label="review-fix",
            )
        err = capsys.readouterr().err
        assert "[report review-fix]" in err
        assert "レポート出力なし" in err

    def test_success_does_not_show_report_when_silent(self, tmp_path, capsys):
        """When silent=True and success, report is not shown."""
        process = Mock()
        process.communicate.return_value = ("", "")
        process.returncode = 0
        report_path = tmp_path / "pr_1_review-fix.md"
        report_path.write_text("- some content", encoding="utf-8")

        with (
            patch(
                "claude_runner.subprocess.run",
                return_value=Mock(returncode=0, stdout="", stderr=""),
            ),
            patch("claude_runner.subprocess.Popen", return_value=process),
            patch("auto_fixer.log_group"),
            patch("auto_fixer.log_endgroup"),
        ):
            auto_fixer.run_claude_prompt(
                works_dir=tmp_path,
                prompt="<instructions>fix</instructions>",
                report_path=str(report_path.resolve()),
                report_enabled=True,
                model="sonnet",
                silent=True,
                phase_label="review-fix",
            )
        err = capsys.readouterr().err
        assert "[report]" not in err

    def test_report_instruction_is_omitted_when_disabled(self, tmp_path):
        process = Mock()
        process.communicate.return_value = ("", "")
        process.returncode = 0
        captured_prompt = ""

        def popen_side_effect(*args, **kwargs):
            nonlocal captured_prompt
            captured_prompt = (tmp_path / "_review_prompt.md").read_text(
                encoding="utf-8"
            )
            return process

        with (
            patch(
                "claude_runner.subprocess.run",
                side_effect=[
                    Mock(returncode=0, stdout="abc123\n", stderr=""),
                    Mock(returncode=0, stdout="", stderr=""),
                ],
            ),
            patch("claude_runner.subprocess.Popen", side_effect=popen_side_effect),
            patch("auto_fixer.log_group"),
            patch("auto_fixer.log_endgroup"),
        ):
            auto_fixer.run_claude_prompt(
                works_dir=tmp_path,
                prompt="<instructions>fix</instructions>",
                report_path=None,
                report_enabled=False,
                model="sonnet",
                silent=True,
                phase_label="review-fix",
            )

        assert "<runtime_pain_report>" not in captured_prompt
