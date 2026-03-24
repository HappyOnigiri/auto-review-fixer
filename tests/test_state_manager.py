"""Unit tests for state_manager."""

import json

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


def test_render_state_comment_uses_updated_review_summary_title():
    body = state_manager.render_state_comment([])

    assert "<summary>Processed Reviews</summary>" in body
    assert "System Use Only" not in body


def test_render_state_comment_uses_ja_review_summary_title_when_language_ja():
    import i18n

    original = i18n.get_language()
    try:
        i18n.set_language("ja")
        body = state_manager.render_state_comment([])
        assert "<summary>対応済みレビュー一覧</summary>" in body
    finally:
        i18n.set_language(original)


def test_render_state_comment_includes_result_log_section():
    body = state_manager.render_state_comment(
        [],
        result_log_body="#### Review Fix\n\n**Executed at:** 2026-03-12 10:00:00 JST",
    )

    assert state_manager.RESULT_LOG_SECTION_START_MARKER in body
    assert "<summary>Execution Log</summary>" in body
    assert "#### Review Fix" in body


def test_render_state_comment_includes_result_log_section_ja():
    import i18n

    i18n.set_language("ja")
    body = state_manager.render_state_comment(
        [],
        result_log_body="#### レビュー修正\n\n**実行日時:** 2026-03-12 10:00:00 JST",
    )

    assert state_manager.RESULT_LOG_SECTION_START_MARKER in body
    assert "<summary>実行ログ</summary>" in body


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
        "<!-- This comment is used by Refix to record processing state. "
        "Do not manually edit or delete it. -->"
    ) in body


def test_render_state_comment_hides_description_in_html_comment_ja():
    import i18n

    i18n.set_language("ja")
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


def test_load_state_comment_extracts_latest_marker_comment_and_ids(
    mocker, make_cmd_result
):
    state_body = state_manager.render_state_comment(
        [
            state_manager.StateEntry(
                comment_id="r123",
                url="https://github.com/owner/repo/pull/1#discussion_r123",
                processed_at="2026-03-11 12:00:00",
            )
        ],
        result_log_body="#### Review Fix\n\n**Executed at:** 2026-03-12 10:00:00 JST",
    )
    stdout = json.dumps(
        [
            [
                {"id": 1, "body": "hello"},
                {"id": 2, "body": state_body, "user": {"login": "test-bot"}},
            ]
        ]
    )
    mocker.patch("state_manager.run_command", return_value=make_cmd_result(stdout))
    mocker.patch(
        "state_manager._get_authenticated_github_user", return_value="test-bot"
    )
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
    assert "#### Review Fix" in comment.result_log_body


def test_parse_processed_ids_ignores_report_section_content():
    text = state_manager.render_state_comment(
        [],
        result_log_body="#### Review Fix\n\n- related id: discussion_r999",
    )

    assert state_manager.parse_processed_ids(text) == []


def test_upsert_state_comment_creates_when_missing(mocker, make_cmd_result):
    mocker.patch(
        "state_manager.load_state_comment",
        return_value=state_manager.StateComment(
            github_comment_id=None,
            body="",
            entries=[],
            processed_ids=set(),
            archived_ids=set(),
        ),
    )
    mock_run = mocker.patch(
        "state_manager.run_command",
        return_value=make_cmd_result(""),
    )
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


def test_upsert_state_comment_deduplicates_when_stale_state(mocker, make_cmd_result):
    """stale な state (github_comment_id=None) でも fresh load で既存コメントを検出して PATCH する。"""
    stale_state = state_manager.StateComment(
        github_comment_id=None,
        body="",
        entries=[],
        processed_ids=set(),
        archived_ids=set(),
    )
    fresh_state = state_manager.StateComment(
        github_comment_id=4121167344,
        body="existing body",
        entries=[
            state_manager.StateEntry(
                comment_id="r100",
                url="https://github.com/owner/repo/pull/7#discussion_r100",
                processed_at="2026-03-11 12:00:00",
            )
        ],
        processed_ids={"r100"},
        archived_ids=set(),
    )
    # 1回目は stale、2回目 (fresh check) は既存コメントあり
    mocker.patch(
        "state_manager.load_state_comment",
        side_effect=[stale_state, fresh_state],
    )
    mock_run = mocker.patch(
        "state_manager.run_command",
        return_value=make_cmd_result(""),
    )
    state_manager.upsert_state_comment(
        "owner/repo",
        7,
        [
            state_manager.StateEntry(
                comment_id="r200",
                url="https://github.com/owner/repo/pull/7#discussion_r200",
                processed_at="2026-03-11 12:05:00",
            )
        ],
    )

    cmd = mock_run.call_args.args[0]
    # 新規作成ではなく PATCH が使われること
    assert cmd[:4] == ["gh", "api", "repos/owner/repo/issues/comments/4121167344", "-X"]
    assert "PATCH" in cmd


def test_upsert_state_comment_deduplicates_merges_workflow_status(
    mocker, make_cmd_result
):
    """stale state の workflow_status が空のとき、fresh state の workflow_status をマージする。"""
    stale_state = state_manager.StateComment(
        github_comment_id=None,
        body="",
        entries=[],
        processed_ids=set(),
        archived_ids=set(),
        workflow_status="",
    )
    fresh_state = state_manager.StateComment(
        github_comment_id=4121167344,
        body="existing body",
        entries=[],
        processed_ids=set(),
        archived_ids=set(),
        workflow_status="running",
    )
    mocker.patch(
        "state_manager.load_state_comment",
        side_effect=[stale_state, fresh_state],
    )
    mock_run = mocker.patch(
        "state_manager.run_command",
        return_value=make_cmd_result(""),
    )
    state_manager.upsert_state_comment(
        "owner/repo",
        7,
        [
            state_manager.StateEntry(
                comment_id="r200",
                url="https://github.com/owner/repo/pull/7#discussion_r200",
                processed_at="2026-03-11 12:05:00",
            )
        ],
        workflow_status=None,
    )

    cmd = mock_run.call_args.args[0]
    assert "PATCH" in cmd
    body_arg = next(a for a in cmd if "refix-status" in a)
    assert "running" in body_arg


def test_upsert_state_comment_updates_when_existing(mocker, make_cmd_result):
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

    mocker.patch("state_manager.load_state_comment", return_value=existing)
    mock_run = mocker.patch(
        "state_manager.run_command",
        return_value=make_cmd_result(""),
    )
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


def test_upsert_state_comment_writes_result_log_body_without_new_entries(
    mocker, make_cmd_result
):
    mocker.patch(
        "state_manager.load_state_comment",
        return_value=state_manager.StateComment(
            github_comment_id=None,
            body="",
            entries=[],
            processed_ids=set(),
            archived_ids=set(),
            result_log_body="",
        ),
    )
    mock_run = mocker.patch(
        "state_manager.run_command",
        return_value=make_cmd_result(""),
    )
    state_manager.upsert_state_comment(
        "owner/repo",
        7,
        [],
        result_log_body="#### CI 修正\n\n**実行日時:** 2026-03-12 10:00:00 JST",
    )

    cmd = mock_run.call_args.args[0]
    assert cmd[:5] == ["gh", "pr", "comment", "7", "--repo"]
    assert "#### CI 修正" in cmd[-1]


def test_upsert_state_comment_skips_load_with_preloaded_state(mocker, make_cmd_result):
    """_preloaded_state が渡された場合、初回ロードはスキップされる。
    ただし github_comment_id=None の場合は重複防止のため fresh check が1回走る。"""
    preloaded = state_manager.StateComment(
        github_comment_id=None,
        body="",
        entries=[],
        processed_ids=set(),
        archived_ids=set(),
        result_log_body="",
    )
    # fresh check 用の戻り値 (コメントなし → 新規作成パス)
    fresh_empty = state_manager.StateComment(
        github_comment_id=None,
        body="",
        entries=[],
        processed_ids=set(),
        archived_ids=set(),
        result_log_body="",
    )
    mock_load = mocker.patch(
        "state_manager.load_state_comment",
        return_value=fresh_empty,
    )
    mock_run = mocker.patch(
        "state_manager.run_command",
        return_value=make_cmd_result(""),
    )
    state_manager.upsert_state_comment(
        "owner/repo",
        7,
        [],
        result_log_body="#### CI 修正\n\n**実行日時:** 2026-03-12 10:00:00 JST",
        _preloaded_state=preloaded,
    )

    # 初回ロードはスキップ、fresh check の1回のみ
    mock_load.assert_called_once_with("owner/repo", 7)
    cmd = mock_run.call_args.args[0]
    assert cmd[:5] == ["gh", "pr", "comment", "7", "--repo"]


# --- workflow_status 新機能テスト ---


def test_render_state_comment_includes_workflow_status_marker():
    body = state_manager.render_state_comment([], workflow_status="running")
    assert "<!-- refix-status: running -->" in body


def test_render_state_comment_omits_status_marker_when_empty():
    body = state_manager.render_state_comment([], workflow_status="")
    assert "<!-- refix-status:" not in body


def test_load_state_comment_parses_workflow_status(mocker, make_cmd_result):
    entries = [
        state_manager.StateEntry(
            comment_id="r123",
            url="https://github.com/owner/repo/pull/1#discussion_r123",
            processed_at="2026-03-11 12:00:00",
        )
    ]
    body = state_manager.render_state_comment(entries, workflow_status="done")
    stdout = json.dumps([[{"id": 5, "body": body, "user": {"login": "bot"}}]])
    mocker.patch("state_manager.run_command", return_value=make_cmd_result(stdout))
    mocker.patch("state_manager._get_authenticated_github_user", return_value="bot")

    comment = state_manager.load_state_comment("owner/repo", 1)

    assert comment.workflow_status == "done"


def test_load_state_comment_returns_empty_status_when_no_marker(
    mocker, make_cmd_result
):
    body = state_manager.render_state_comment([], workflow_status="")
    stdout = json.dumps([[{"id": 5, "body": body, "user": {"login": "bot"}}]])
    mocker.patch("state_manager.run_command", return_value=make_cmd_result(stdout))
    mocker.patch("state_manager._get_authenticated_github_user", return_value="bot")

    comment = state_manager.load_state_comment("owner/repo", 1)

    assert comment.workflow_status == ""


def test_upsert_state_comment_passes_workflow_status(mocker, make_cmd_result):
    preloaded = state_manager.StateComment(
        github_comment_id=None,
        body="",
        entries=[],
        processed_ids=set(),
        archived_ids=set(),
        workflow_status="",
    )
    mocker.patch(
        "state_manager.load_state_comment",
        return_value=state_manager.StateComment(
            github_comment_id=None,
            body="",
            entries=[],
            processed_ids=set(),
            archived_ids=set(),
        ),
    )
    mock_run = mocker.patch(
        "state_manager.run_command",
        return_value=make_cmd_result(""),
    )
    state_manager.upsert_state_comment(
        "owner/repo",
        7,
        [],
        workflow_status="running",
        _preloaded_state=preloaded,
    )

    cmd = mock_run.call_args.args[0]
    # gh pr comment の場合は --body の次の引数がボディ
    body = (
        cmd[cmd.index("--body") + 1]
        if "--body" in cmd
        else next(arg[len("body=") :] for arg in cmd if arg.startswith("body="))
    )
    assert "<!-- refix-status: running -->" in body


def test_upsert_state_comment_preserves_existing_status_when_none(
    mocker, make_cmd_result
):
    preloaded = state_manager.StateComment(
        github_comment_id=None,
        body="",
        entries=[],
        processed_ids=set(),
        archived_ids=set(),
        workflow_status="done",
    )
    mocker.patch(
        "state_manager.load_state_comment",
        return_value=state_manager.StateComment(
            github_comment_id=None,
            body="",
            entries=[],
            processed_ids=set(),
            archived_ids=set(),
        ),
    )
    mock_run = mocker.patch(
        "state_manager.run_command",
        return_value=make_cmd_result(""),
    )
    state_manager.upsert_state_comment(
        "owner/repo",
        7,
        [],
        workflow_status=None,
        _preloaded_state=preloaded,
    )

    cmd = mock_run.call_args.args[0]
    body = (
        cmd[cmd.index("--body") + 1]
        if "--body" in cmd
        else next(arg[len("body=") :] for arg in cmd if arg.startswith("body="))
    )
    assert "<!-- refix-status: done -->" in body


def test_upsert_state_comment_creates_comment_with_workflow_status_only(
    mocker, make_cmd_result
):
    preloaded = state_manager.StateComment(
        github_comment_id=None,
        body="",
        entries=[],
        processed_ids=set(),
        archived_ids=set(),
        workflow_status="",
    )
    mocker.patch(
        "state_manager.load_state_comment",
        return_value=state_manager.StateComment(
            github_comment_id=None,
            body="",
            entries=[],
            processed_ids=set(),
            archived_ids=set(),
        ),
    )
    mock_run = mocker.patch(
        "state_manager.run_command",
        return_value=make_cmd_result(""),
    )
    state_manager.upsert_state_comment(
        "owner/repo",
        7,
        [],
        workflow_status="running",
        _preloaded_state=preloaded,
    )

    assert mock_run.called


def test_update_workflow_status_calls_upsert(mocker, make_cmd_result):
    preloaded = state_manager.StateComment(
        github_comment_id=None,
        body="",
        entries=[],
        processed_ids=set(),
        archived_ids=set(),
        workflow_status="",
    )
    mock_upsert = mocker.patch("state_manager.upsert_state_comment")

    state_manager.update_workflow_status(
        "owner/repo", 7, "running", _preloaded_state=preloaded
    )

    mock_upsert.assert_called_once()
    call_kwargs = mock_upsert.call_args
    assert call_kwargs.args[0] == "owner/repo"
    assert call_kwargs.args[1] == 7


def test_update_workflow_status_skips_when_same_status(mocker, make_cmd_result):
    preloaded = state_manager.StateComment(
        github_comment_id=None,
        body="",
        entries=[],
        processed_ids=set(),
        archived_ids=set(),
        workflow_status="running",
    )
    mock_upsert = mocker.patch("state_manager.upsert_state_comment")

    state_manager.update_workflow_status(
        "owner/repo", 7, "running", _preloaded_state=preloaded
    )

    mock_upsert.assert_not_called()


def test_load_state_comment_deletes_duplicate_comments(mocker, make_cmd_result):
    body1 = state_manager.render_state_comment(
        [
            state_manager.StateEntry(
                comment_id="r100",
                url="https://github.com/owner/repo/pull/1#discussion_r100",
                processed_at="2026-03-11 10:00:00",
            )
        ]
    )
    body2 = state_manager.render_state_comment(
        [
            state_manager.StateEntry(
                comment_id="r200",
                url="https://github.com/owner/repo/pull/1#discussion_r200",
                processed_at="2026-03-11 11:00:00",
            )
        ]
    )
    stdout = json.dumps(
        [
            [
                {"id": 10, "body": body1, "user": {"login": "bot"}},
                {"id": 20, "body": body2, "user": {"login": "bot"}},
            ]
        ]
    )
    mock_run = mocker.patch(
        "state_manager.run_command", return_value=make_cmd_result(stdout)
    )
    mocker.patch("state_manager._get_authenticated_github_user", return_value="bot")

    comment = state_manager.load_state_comment("owner/repo", 1)

    # 最新コメント（id=20）が返される
    assert comment.github_comment_id == 20
    # 両コメントのエントリがマージされる
    assert {e.comment_id for e in comment.entries} == {"r100", "r200"}

    # 古いコメント（id=10）の DELETE が呼ばれる
    delete_calls = [
        call for call in mock_run.call_args_list if "DELETE" in call.args[0]
    ]
    assert len(delete_calls) == 1
    assert "repos/owner/repo/issues/comments/10" in delete_calls[0].args[0]


# --- ローカルファイルモードのテスト ---


def test_configure_local_state(monkeypatch):
    """configure_local_state がモジュール変数を更新することを確認。"""
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    original_use = state_manager._use_local_state
    original_dir = state_manager._local_state_dir
    try:
        state_manager.configure_local_state(
            use_local_state=True, local_state_dir="/tmp/mystate"
        )
        assert state_manager._use_local_state is True
        assert state_manager._local_state_dir == "/tmp/mystate"
    finally:
        state_manager.configure_local_state(
            use_local_state=original_use, local_state_dir=original_dir
        )


def test_load_state_from_file_missing(tmp_path, monkeypatch):
    """ファイルが存在しない場合に空の StateComment を返す。"""
    monkeypatch.setattr(state_manager, "_use_local_state", True)
    monkeypatch.setattr(state_manager, "_local_state_dir", str(tmp_path))

    result = state_manager.load_state_comment("owner/repo", 42)

    assert result.github_comment_id is None
    assert result.body == ""
    assert result.entries == []
    assert result.processed_ids == set()
    assert result.archived_ids == set()


def test_load_state_from_file_with_content(tmp_path, monkeypatch):
    """ファイル内容からエントリ・ステータス・archived_ids を正しくパースできる。"""
    monkeypatch.setattr(state_manager, "_use_local_state", True)
    monkeypatch.setattr(state_manager, "_local_state_dir", str(tmp_path))

    state_dir = tmp_path / "owner" / "repo"
    state_dir.mkdir(parents=True)
    body = (
        "<!-- refix-state-comment -->\n"
        "### 🤖 Refix Status\n\n"
        "| Comment ID | 処理日時 |\n"
        "|---|---|\n"
        "| [r123](https://github.com/owner/repo/pull/5#discussion_r123) | 2026-01-01 00:00:00 JST |\n"
        "<!-- archived-ids: r999 -->\n"
        "<!-- refix-status: done -->\n"
    )
    (state_dir / "5.md").write_text(body, encoding="utf-8")

    result = state_manager.load_state_comment("owner/repo", 5)

    assert result.github_comment_id is None
    assert "r123" in result.processed_ids
    assert "r999" in result.archived_ids
    assert result.workflow_status == "done"
    assert len(result.entries) == 1
    assert result.entries[0].comment_id == "r123"


def test_save_state_to_file_creates_dirs(tmp_path, monkeypatch):
    """ディレクトリが自動作成される。"""
    monkeypatch.setattr(state_manager, "_use_local_state", True)
    monkeypatch.setattr(state_manager, "_local_state_dir", str(tmp_path))

    state_manager._save_state_to_file("owner/repo", 7, "hello")

    path = tmp_path / "owner" / "repo" / "7.md"
    assert path.exists()
    assert path.read_text(encoding="utf-8") == "hello"


def test_upsert_local_mode(tmp_path, monkeypatch):
    """_use_local_state=True 時に upsert_state_comment がファイルに書き込む（gh コマンド未呼出し）。"""
    monkeypatch.setattr(state_manager, "_use_local_state", True)
    monkeypatch.setattr(state_manager, "_local_state_dir", str(tmp_path))

    mock_calls = []

    def fake_run(cmd, **kwargs):
        mock_calls.append(cmd)

        class R:
            returncode = 0
            stdout = "[]"
            stderr = ""

        return R()

    monkeypatch.setattr(state_manager, "run_command", fake_run)

    entry = state_manager.StateEntry(
        comment_id="r1",
        url="https://example.com",
        processed_at="2026-01-01 00:00:00 JST",
    )
    state_manager.upsert_state_comment("owner/repo", 10, [entry])

    path = tmp_path / "owner" / "repo" / "10.md"
    assert path.exists()
    content = path.read_text(encoding="utf-8")
    assert "r1" in content
    # gh コマンドは呼ばれていない
    assert all("gh" not in str(c) for c in mock_calls)


def test_load_local_mode_routing(tmp_path, monkeypatch):
    """_use_local_state=True 時に load_state_comment がファイルから読む（gh コマンド未呼出し）。"""
    monkeypatch.setattr(state_manager, "_use_local_state", True)
    monkeypatch.setattr(state_manager, "_local_state_dir", str(tmp_path))

    mock_calls = []

    def fake_run(cmd, **kwargs):
        mock_calls.append(cmd)

        class R:
            returncode = 0
            stdout = "[]"
            stderr = ""

        return R()

    monkeypatch.setattr(state_manager, "run_command", fake_run)

    result = state_manager.load_state_comment("owner/repo", 99)

    assert result.entries == []
    # gh コマンドは呼ばれていない
    assert mock_calls == []


def test_configure_local_state_rejects_ci(monkeypatch):
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    import errors

    with pytest.raises(errors.ConfigError):
        state_manager.configure_local_state(use_local_state=True)


def test_configure_local_state_allows_ci_when_disabled(monkeypatch):
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    # use_local_state=False の場合はエラーにならない
    state_manager.configure_local_state(use_local_state=False)
