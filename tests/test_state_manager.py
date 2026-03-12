"""Unit tests for state_manager."""

import json
from unittest.mock import Mock, patch

import pytest

import state_manager


def test_parse_processed_ids_from_markdown_table():
    text = """<!-- auto-review-fixer-state-comment -->
### 🤖 Refix Status

| Comment ID | 処理日時 |
|---|---|
| [r123](https://github.com/owner/repo/pull/1#discussion_r123) | 2026-03-11 12:00:00 |
| [discussion_r456](https://github.com/owner/repo/pull/1#discussion_r456) | 2026-03-11 12:05:00 |
"""

    assert state_manager.parse_processed_ids(text) == ["r123", "discussion_r456"]


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("", []),
        ("no table here", []),
        ("broken [discussion_r456](https://example.com", ["discussion_r456"]),
    ],
)
def test_parse_processed_ids_handles_missing_or_broken_text(text, expected):
    assert state_manager.parse_processed_ids(text) == expected


def test_parse_state_entries_falls_back_for_broken_rows():
    text = """<!-- auto-review-fixer-state-comment -->
| Comment ID | 処理日時 |
|---|---|
| [r123](https://github.com/owner/repo/pull/1#discussion_r123) | 2026-03-11 12:00:00 |
| [discussion_r456](https://github.com/owner/repo/pull/1#discussion_r456
"""

    entries = state_manager.parse_state_entries(text)

    assert entries == [
        state_manager.StateEntry(
            comment_id="r123",
            url="https://github.com/owner/repo/pull/1#discussion_r123",
            processed_at="2026-03-11 12:00:00 UTC",
        ),
        state_manager.StateEntry(
            comment_id="discussion_r456",
            url="",
            processed_at="",
        ),
    ]


def test_parse_state_entries_does_not_readd_archived_ids_as_entries():
    text = (
        "<!-- auto-review-fixer-state-comment -->\n"
        "\n<!-- archived-ids: r123,discussion_r456 -->"
    )
    entries = state_manager.parse_state_entries(text)
    assert entries == []


def test_format_state_row_generates_markdown_row():
    row = state_manager.format_state_row(
        "discussion_r456",
        "https://github.com/owner/repo/pull/1#discussion_r456",
        "2026-03-11 12:05:00",
    )

    assert row == (
        "| [discussion_r456](https://github.com/owner/repo/pull/1#discussion_r456) "
        "| 2026-03-11 12:05:00 |"
    )


def test_render_state_comment_trims_oldest_rows_to_fit_limit(monkeypatch):
    monkeypatch.setattr(state_manager, "STATE_COMMENT_MAX_LENGTH", 1000)
    entries = [
        state_manager.StateEntry(
            comment_id=f"discussion_r{i}",
            url=f"https://github.com/owner/repo/pull/1#discussion_r{i}",
            processed_at="2026-03-11 12:00:00",
        )
        for i in range(20)
    ]

    body = state_manager.render_state_comment(entries)

    # Total body (including archived-ids footer) must fit within the limit
    assert len(body) <= 1000
    visible_part = (
        body.split("<!-- archived-ids:")[0] if "<!-- archived-ids:" in body else body
    )
    assert "discussion_r19" in visible_part
    assert "discussion_r0" not in visible_part
    # Trimmed IDs are preserved in the hidden archived-ids section
    assert "<!-- archived-ids:" in body
    assert "discussion_r0" in body


def test_render_state_comment_raises_on_archived_id_overflow(monkeypatch):
    monkeypatch.setattr(state_manager, "STATE_COMMENT_MAX_LENGTH", 500)
    # Fill the comment body to near the limit using many archived IDs
    # so that not all of them can fit in the footer.
    archived = {f"discussion_r{i}" for i in range(50)}
    with pytest.raises(RuntimeError, match="archived IDs"):
        state_manager.render_state_comment([], archived_ids=archived)


def test_render_state_comment_hides_description_in_html_comment():
    body = state_manager.render_state_comment([])

    assert (
        "<!-- このコメントは Refix が処理状態を記録するためのものです。"
        "手動で編集・削除しないでください。 -->"
    ) in body


def test_current_timestamp_defaults_to_jst():
    timestamp = state_manager.current_timestamp()
    assert timestamp.endswith("JST")


def test_create_state_entry_uses_requested_timezone():
    entry = state_manager.create_state_entry(
        comment_id="r123",
        url="https://github.com/owner/repo/pull/1#discussion_r123",
        timezone_name="UTC",
    )
    assert entry.processed_at.endswith("UTC")


def test_load_state_comment_extracts_latest_marker_comment_and_ids():
    state_body = state_manager.render_state_comment(
        [
            state_manager.StateEntry(
                comment_id="r123",
                url="https://github.com/owner/repo/pull/1#discussion_r123",
                processed_at="2026-03-11 12:00:00",
            )
        ]
    )
    result = Mock(
        returncode=0,
        stdout=json.dumps(
            [
                [
                    {"id": 1, "body": "hello"},
                    {"id": 2, "body": state_body, "user": {"login": "test-bot"}},
                ]
            ]
        ),
        stderr="",
    )

    with (
        patch("state_manager.subprocess.run", return_value=result),
        patch("state_manager._get_authenticated_github_user", return_value="test-bot"),
    ):
        comment = state_manager.load_state_comment("owner/repo", 1)

    assert comment.github_comment_id == 2
    assert comment.processed_ids == {"r123"}
    assert comment.entries == [
        state_manager.StateEntry(
            comment_id="r123",
            url="https://github.com/owner/repo/pull/1#discussion_r123",
            processed_at="2026-03-11 12:00:00 UTC",
        )
    ]


def test_upsert_state_comment_creates_when_missing():
    with (
        patch(
            "state_manager.load_state_comment",
            return_value=state_manager.StateComment(
                github_comment_id=None,
                body="",
                entries=[],
                processed_ids=set(),
                archived_ids=set(),
            ),
        ),
        patch(
            "state_manager.subprocess.run",
            return_value=Mock(returncode=0, stdout="", stderr=""),
        ) as mock_run,
    ):
        state_manager.upsert_state_comment(
            "owner/repo",
            7,
            [
                state_manager.StateEntry(
                    comment_id="r123",
                    url="https://github.com/owner/repo/pull/7#discussion_r123",
                    processed_at="2026-03-11 12:00:00",
                )
            ],
        )

    cmd = mock_run.call_args.args[0]
    assert cmd[:5] == ["gh", "pr", "comment", "7", "--repo"]
    assert "owner/repo" in cmd


def test_upsert_state_comment_updates_when_existing():
    existing = state_manager.StateComment(
        github_comment_id=99,
        body="old",
        entries=[
            state_manager.StateEntry(
                comment_id="r123",
                url="https://github.com/owner/repo/pull/7#discussion_r123",
                processed_at="2026-03-11 12:00:00",
            )
        ],
        processed_ids={"r123"},
        archived_ids=set(),
    )

    with (
        patch("state_manager.load_state_comment", return_value=existing),
        patch(
            "state_manager.subprocess.run",
            return_value=Mock(returncode=0, stdout="", stderr=""),
        ) as mock_run,
    ):
        state_manager.upsert_state_comment(
            "owner/repo",
            7,
            [
                state_manager.StateEntry(
                    comment_id="discussion_r456",
                    url="https://github.com/owner/repo/pull/7#discussion_r456",
                    processed_at="2026-03-11 12:05:00",
                )
            ],
        )

    cmd = mock_run.call_args.args[0]
    assert cmd[:4] == ["gh", "api", "repos/owner/repo/issues/comments/99", "-X"]
    assert "PATCH" in cmd
    assert any(arg.startswith("body=") for arg in cmd)
