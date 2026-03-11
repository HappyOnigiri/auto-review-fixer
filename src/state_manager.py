#!/usr/bin/env python3
"""State comment management for processed PR reviews."""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from datetime import datetime
import re
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

STATE_COMMENT_MARKER = "<!-- auto-review-fixer-state-comment -->"
STATE_COMMENT_TITLE = "### 🤖 Auto Review Fixer Status"
STATE_COMMENT_DESCRIPTION = (
    "<!-- このコメントは Auto Review Fixer が処理状態を記録するためのものです。"
    "手動で編集・削除しないでください。 -->"
)
STATE_COMMENT_MAX_LENGTH = 60000
STATE_ID_PATTERN = re.compile(r"\[(r\d+|discussion_r\d+)\](?:\([^)]+\))?")
STATE_ID_FALLBACK_PATTERN = re.compile(r"\b(r\d+|discussion_r\d+)\b")
LEGACY_TIMESTAMP_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$")
STATE_TABLE_ROW_PATTERN = re.compile(
    r"^\|\s*(?:\[(?P<link_id>r\d+|discussion_r\d+)\]\((?P<url>[^)]+)\)|(?P<plain_id>r\d+|discussion_r\d+))\s*\|\s*(?P<timestamp>[^|]+?)\s*\|$",
    re.MULTILINE,
)
ARCHIVED_IDS_PATTERN = re.compile(r"<!-- archived-ids: ([^>]+) -->")
DEFAULT_STATE_COMMENT_TIMEZONE = "JST"
STATE_TIMEZONE_ALIASES = {
    "JST": "Asia/Tokyo",
}


@dataclass(frozen=True)
class StateEntry:
    comment_id: str
    url: str
    processed_at: str


@dataclass(frozen=True)
class StateComment:
    github_comment_id: int | None
    body: str
    entries: list[StateEntry]
    processed_ids: set[str]
    archived_ids: set[str]


def normalize_state_timezone_name(timezone_name: str) -> str:
    """Normalize a configured timezone name for state comment timestamps."""
    normalized = (timezone_name or DEFAULT_STATE_COMMENT_TIMEZONE).strip()
    if not normalized:
        normalized = DEFAULT_STATE_COMMENT_TIMEZONE
    return STATE_TIMEZONE_ALIASES.get(normalized.upper(), normalized)


def ensure_valid_state_timezone(timezone_name: str) -> str:
    """Validate and return a timezone name accepted by zoneinfo."""
    normalized = normalize_state_timezone_name(timezone_name)
    try:
        ZoneInfo(normalized)
    except ZoneInfoNotFoundError as exc:
        raise ValueError(
            f"Invalid state comment timezone: {timezone_name}. "
            "Use a valid IANA timezone (e.g. Asia/Tokyo) or JST."
        ) from exc
    return normalized


def current_timestamp(timezone_name: str = DEFAULT_STATE_COMMENT_TIMEZONE) -> str:
    """Return the current timestamp in the state-comment format."""
    normalized = ensure_valid_state_timezone(timezone_name)
    return datetime.now(ZoneInfo(normalized)).strftime("%Y-%m-%d %H:%M:%S %Z")


def parse_processed_ids(text: str) -> list[str]:
    """Extract processed IDs from a state comment body without crashing."""
    if not text:
        return []

    matches = STATE_ID_PATTERN.findall(text)
    if not matches:
        matches = STATE_ID_FALLBACK_PATTERN.findall(text)

    seen: set[str] = set()
    processed_ids: list[str] = []
    for comment_id in matches:
        if comment_id in seen:
            continue
        seen.add(comment_id)
        processed_ids.append(comment_id)
    return processed_ids


def _normalize_legacy_processed_at(processed_at: str) -> str:
    """Append ' UTC' to legacy timestamps that lack timezone info."""
    if processed_at and LEGACY_TIMESTAMP_PATTERN.match(processed_at):
        return processed_at + " UTC"
    return processed_at


def parse_state_entries(text: str) -> list[StateEntry]:
    """Parse structured entries from a state comment body."""
    entries: list[StateEntry] = []
    seen: set[str] = set()

    for match in STATE_TABLE_ROW_PATTERN.finditer(text or ""):
        comment_id = (match.group("link_id") or match.group("plain_id") or "").strip()
        if not comment_id or comment_id in seen:
            continue
        seen.add(comment_id)
        entries.append(
            StateEntry(
                comment_id=comment_id,
                url=(match.group("url") or "").strip(),
                processed_at=_normalize_legacy_processed_at(match.group("timestamp").strip()),
            )
        )

    text_without_footer = ARCHIVED_IDS_PATTERN.sub("", text or "")
    fallback_ids = parse_processed_ids(text_without_footer)
    for comment_id in fallback_ids:
        if comment_id in seen:
            continue
        seen.add(comment_id)
        entries.append(StateEntry(comment_id=comment_id, url="", processed_at=""))

    return entries


def format_state_row(comment_id: str, url: str, processed_at: str) -> str:
    """Format a single markdown table row for the state comment."""
    id_cell = f"[{comment_id}]({url})" if url else comment_id
    return f"| {id_cell} | {processed_at} |"


def render_state_comment(entries: list[StateEntry], archived_ids: set[str] | None = None) -> str:
    """Render the full state comment, trimming oldest rows if necessary."""
    # accumulated_archived starts with any IDs previously archived (trimmed in prior renders).
    # As visible entries are removed to stay within STATE_COMMENT_MAX_LENGTH, their IDs
    # are added here so they remain queryable even after disappearing from the table.
    accumulated_archived: set[str] = set(archived_ids or set())
    trimmed_entries = list(entries)
    # Iteratively remove the oldest entry from trimmed_entries until the visible portion
    # of the comment body fits within STATE_COMMENT_MAX_LENGTH.
    # format_state_row renders each entry as a markdown table row; rows are joined and
    # embedded in a <details> block. The oldest rows are dropped first to keep the most
    # recent processing history visible within GitHub's comment size limit.
    while True:
        rows = "\n".join(
            format_state_row(
                entry.comment_id,
                entry.url,
                entry.processed_at,
            )
            for entry in trimmed_entries
        )
        body = "\n".join(
            [
                STATE_COMMENT_MARKER,
                STATE_COMMENT_TITLE,
                STATE_COMMENT_DESCRIPTION,
                "",
                "<details>",
                "<summary>処理済みレビュー一覧 (System Use Only)</summary>",
                "",
                "| Comment ID | 処理日時 |",
                "|---|---|",
                rows,
                "",
                "</details>",
            ]
        )
        footer = f"\n<!-- archived-ids: {','.join(sorted(accumulated_archived))} -->" if accumulated_archived else ""
        if len(body) + len(footer) <= STATE_COMMENT_MAX_LENGTH or not trimmed_entries:
            return body + footer
        removed = trimmed_entries.pop(0)
        accumulated_archived.add(removed.comment_id)


def create_state_entry(
    comment_id: str,
    url: str,
    processed_at: str | None = None,
    timezone_name: str = DEFAULT_STATE_COMMENT_TIMEZONE,
) -> StateEntry:
    """Create a state entry with the default current timestamp."""
    return StateEntry(
        comment_id=comment_id,
        url=url,
        processed_at=processed_at or current_timestamp(timezone_name),
    )


def _get_authenticated_github_user() -> str | None:
    """Return the login of the currently authenticated GitHub user, or None on failure."""
    result = subprocess.run(
        ["gh", "api", "user", "--jq", ".login"],
        capture_output=True,
        text=True,
        check=False,
        encoding="utf-8",
    )
    if result.returncode == 0 and result.stdout.strip():
        username = result.stdout.strip()
        if re.match(r'^[a-zA-Z0-9][a-zA-Z0-9-]{0,38}$', username):
            return username
    return None


def load_state_comment(repo: str, pr_number: int) -> StateComment:
    """Load the current state comment for a PR."""
    cmd = [
        "gh",
        "api",
        f"repos/{repo}/issues/{pr_number}/comments",
        "--paginate",
        "--slurp",
    ]
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        check=False,
        encoding="utf-8",
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"Failed to fetch PR comments for {repo}#{pr_number}: {(result.stderr or '').strip()}"
        )

    try:
        raw_data = json.loads(result.stdout) if result.stdout else []
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Failed to parse PR comments for {repo}#{pr_number}") from exc

    pages = raw_data if isinstance(raw_data, list) else []
    comments: list[dict] = []
    for item in pages:
        if isinstance(item, list):
            comments.extend(comment for comment in item if isinstance(comment, dict))
        elif isinstance(item, dict):
            comments.append(item)

    github_username = _get_authenticated_github_user()
    if github_username is None:
        raise RuntimeError(
            f"Failed to determine authenticated GitHub user; cannot safely load state comment for {repo}#{pr_number}"
        )
    matching_comments = [
        comment
        for comment in comments
        if STATE_COMMENT_MARKER in str(comment.get("body") or "")
        and comment.get("user", {}).get("login") == github_username
    ]
    if not matching_comments:
        return StateComment(github_comment_id=None, body="", entries=[], processed_ids=set(), archived_ids=set())

    merged_entries: list[StateEntry] = []
    seen_ids: set[str] = set()
    archived_ids: set[str] = set()
    for comment in matching_comments:
        body_text = str(comment.get("body") or "")
        for entry in parse_state_entries(body_text):
            if entry.comment_id in seen_ids:
                continue
            seen_ids.add(entry.comment_id)
            merged_entries.append(entry)
        m = ARCHIVED_IDS_PATTERN.search(body_text)
        if m:
            archived_ids.update(aid.strip() for aid in m.group(1).split(",") if aid.strip())

    latest_comment = matching_comments[-1]
    return StateComment(
        github_comment_id=latest_comment.get("id"),
        body=str(latest_comment.get("body") or ""),
        entries=merged_entries,
        processed_ids={entry.comment_id for entry in merged_entries} | archived_ids,
        archived_ids=archived_ids,
    )


def upsert_state_comment(repo: str, pr_number: int, new_entries: list[StateEntry]) -> None:
    """Create or update the state comment for a PR."""
    if not new_entries:
        return

    state = load_state_comment(repo, pr_number)
    merged_entries = list(state.entries)
    seen_ids = {entry.comment_id for entry in merged_entries}
    for entry in new_entries:
        if entry.comment_id in seen_ids:
            continue
        seen_ids.add(entry.comment_id)
        merged_entries.append(entry)

    body = render_state_comment(merged_entries, archived_ids=state.archived_ids)
    if state.github_comment_id is None:
        cmd = [
            "gh",
            "pr",
            "comment",
            str(pr_number),
            "--repo",
            repo,
            "--body",
            body,
        ]
    else:
        cmd = [
            "gh",
            "api",
            f"repos/{repo}/issues/comments/{state.github_comment_id}",
            "-X",
            "PATCH",
            "-f",
            f"body={body}",
        ]

    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        check=False,
        encoding="utf-8",
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"Failed to upsert state comment for {repo}#{pr_number}: {(result.stderr or '').strip()}"
        )
