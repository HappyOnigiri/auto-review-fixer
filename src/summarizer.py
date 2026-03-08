#!/usr/bin/env python3
"""Summarize PR review comments using Claude Haiku via CLI (single call)."""

import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any


def summarize_reviews(
    reviews: list[dict[str, Any]],
    comments: list[dict[str, Any]],
) -> dict[str, str]:
    """Return {id: summary} for all reviews and inline comments.

    Uses a single claude CLI call with Haiku model.
    Falls back to empty dict on failure (caller uses original body).
    """
    items = []
    for r in reviews:
        if r.get("id") and r.get("body"):
            items.append({"id": r["id"], "body": r["body"]})
    for c in comments:
        if c.get("id") and c.get("body"):
            items.append({"id": f"discussion_r{c['id']}", "body": c["body"]})

    if not items:
        return {}

    items_text = "\n\n".join(
        f"=== ID: {it['id']} ===\n{it['body']}" for it in items
    )
    prompt = f"""以下のコードレビューコメントを、AIエージェントがコードを改修するために必要な情報を保ちながら日本語で要約してください。

要約のルール:
- 日本語で記述する
- 文字数制限なし
- ファイル名・行番号は必ず維持する
- 何が問題か・何を修正すべきかが明確にわかるようにする
- 改修に必要な情報はすべて残す
- 重複する説明や改修に不要な情報（挨拶、定型文など）は省く

各コメントのIDごとにJSON配列で返してください。JSON配列のみ返してください。形式:
[{{"id": "...", "summary": "..."}}]

コメント一覧:
{items_text}"""

    # Write prompt to a temp file to avoid Windows command-line length limits
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".md", encoding="utf-8", delete=False
    ) as f:
        f.write(prompt)
        prompt_path = f.name

    try:
        print("Summarizing reviews with Haiku...")
        result = subprocess.run(
            [
                "claude",
                "--model", "haiku",
                "--dangerously-skip-permissions",
                "-p", f"Read the file {prompt_path} and follow the instructions in it.",
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    finally:
        Path(prompt_path).unlink(missing_ok=True)

    if result.returncode != 0:
        print(f"Warning: summarization failed (exit {result.returncode})", file=sys.stderr)
        if result.stderr:
            print(f"  stderr: {result.stderr.strip()}", file=sys.stderr)
        if result.stdout:
            print(f"  stdout: {result.stdout.strip()}", file=sys.stderr)
        return {}

    try:
        text = result.stdout
        parsed = None
        for match in re.finditer(r"\[.*?\]", text, re.DOTALL):
            try:
                parsed = json.loads(match.group())
                break
            except json.JSONDecodeError:
                continue
        if parsed is None:
            raise ValueError("No JSON array found in response")
        summaries = {
            item["id"]: item["summary"]
            for item in parsed
            if "id" in item and "summary" in item
        }
        print(f"Summarized {len(summaries)} review(s)/comment(s)")
        return summaries
    except Exception as e:
        print(f"Warning: failed to parse summarization response ({e})", file=sys.stderr)
        print(f"  Raw response:\n{result.stdout.strip()}", file=sys.stderr)
        return {}
