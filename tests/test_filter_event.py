"""Unit tests for filter_event.py."""

import json


import filter_event as fe
from filter_event import filter_event


def _make_event(
    *,
    is_pr_comment: bool = True,
    author: str = "coderabbitai[bot]",
) -> dict:
    """テスト用 issue_comment イベント JSON を生成する。"""
    issue: dict = {"number": 1}
    if is_pr_comment:
        issue["pull_request"] = {
            "url": "https://api.github.com/repos/owner/repo/pulls/1"
        }
    return {
        "issue": issue,
        "comment": {
            "id": 1,
            "user": {"login": author},
            "body": "test comment",
        },
    }


def _write_event(tmp_path, event: dict) -> str:
    p = tmp_path / "event.json"
    p.write_text(json.dumps(event), encoding="utf-8")
    return str(p)


def _write_config(tmp_path, content: str) -> str:
    p = tmp_path / ".refix.yaml"
    p.write_text(content, encoding="utf-8")
    return str(p)


class TestFilterEvent:
    def test_pr_comment_from_coderabbitai_is_allowed_by_default(self, tmp_path):
        event_path = _write_event(tmp_path, _make_event(author="coderabbitai[bot]"))
        output_path = str(tmp_path / "output")

        skip = filter_event(event_path, config_path=None, github_output=output_path)

        assert skip is False
        assert (tmp_path / "output").read_text() == "skip=false\n"

    def test_pr_comment_from_other_user_is_skipped_by_default(self, tmp_path):
        event_path = _write_event(tmp_path, _make_event(author="some-user"))
        output_path = str(tmp_path / "output")

        skip = filter_event(event_path, config_path=None, github_output=output_path)

        assert skip is True
        assert (tmp_path / "output").read_text() == "skip=true\n"

    def test_issue_comment_not_on_pr_is_skipped(self, tmp_path):
        event_path = _write_event(
            tmp_path, _make_event(is_pr_comment=False, author="coderabbitai[bot]")
        )
        output_path = str(tmp_path / "output")

        skip = filter_event(event_path, config_path=None, github_output=output_path)

        assert skip is True
        assert (tmp_path / "output").read_text() == "skip=true\n"

    def test_allowed_author_in_config_is_not_skipped(self, tmp_path):
        config_path = _write_config(
            tmp_path,
            "triggers:\n  issue_comment:\n    authors:\n      - coderabbitai[bot]\n      - other-bot[bot]\n",
        )
        event_path = _write_event(tmp_path, _make_event(author="other-bot[bot]"))
        output_path = str(tmp_path / "output")

        skip = filter_event(
            event_path, config_path=config_path, github_output=output_path
        )

        assert skip is False
        assert (tmp_path / "output").read_text() == "skip=false\n"

    def test_non_allowed_author_in_config_is_skipped(self, tmp_path):
        config_path = _write_config(
            tmp_path,
            "triggers:\n  issue_comment:\n    authors:\n      - coderabbitai[bot]\n",
        )
        event_path = _write_event(tmp_path, _make_event(author="someone-else"))
        output_path = str(tmp_path / "output")

        skip = filter_event(
            event_path, config_path=config_path, github_output=output_path
        )

        assert skip is True
        assert (tmp_path / "output").read_text() == "skip=true\n"

    def test_config_with_no_authors_falls_back_to_default(self, tmp_path):
        # triggers.issue_comment は存在するが authors が未設定 → デフォルト動作
        config_path = _write_config(
            tmp_path,
            "triggers:\n  issue_comment: {}\n",
        )
        event_path = _write_event(tmp_path, _make_event(author="coderabbitai[bot]"))
        output_path = str(tmp_path / "output")

        skip = filter_event(
            event_path, config_path=config_path, github_output=output_path
        )

        assert skip is False

    def test_config_with_no_authors_blocks_non_default_author(self, tmp_path):
        config_path = _write_config(
            tmp_path,
            "triggers:\n  issue_comment: {}\n",
        )
        event_path = _write_event(tmp_path, _make_event(author="some-user"))
        output_path = str(tmp_path / "output")

        skip = filter_event(
            event_path, config_path=config_path, github_output=output_path
        )

        assert skip is True

    def test_no_github_output_does_not_raise(self, tmp_path):
        event_path = _write_event(tmp_path, _make_event(author="coderabbitai[bot]"))

        skip = filter_event(event_path, config_path=None, github_output=None)

        assert skip is False

    def test_nonexistent_config_path_falls_back_to_default(self, tmp_path):
        event_path = _write_event(tmp_path, _make_event(author="coderabbitai[bot]"))
        output_path = str(tmp_path / "output")

        skip = filter_event(
            event_path,
            config_path=str(tmp_path / "nonexistent.yaml"),
            github_output=output_path,
        )

        assert skip is False

    def test_output_is_appended_not_overwritten(self, tmp_path):
        event_path = _write_event(tmp_path, _make_event(author="coderabbitai[bot]"))
        output_path = tmp_path / "output"
        output_path.write_text("existing=value\n", encoding="utf-8")

        filter_event(str(event_path), config_path=None, github_output=str(output_path))

        content = output_path.read_text()
        assert content == "existing=value\nskip=false\n"


class TestWriteOutput:
    def test_writes_skip_true(self, tmp_path):
        output_path = str(tmp_path / "out")
        fe._write_output(output_path, skip=True)
        assert (tmp_path / "out").read_text() == "skip=true\n"

    def test_writes_skip_false(self, tmp_path):
        output_path = str(tmp_path / "out")
        fe._write_output(output_path, skip=False)
        assert (tmp_path / "out").read_text() == "skip=false\n"

    def test_none_output_path_is_noop(self):
        fe._write_output(None, skip=True)  # should not raise
