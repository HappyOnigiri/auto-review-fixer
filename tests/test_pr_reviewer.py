"""Unit tests for pr_reviewer helpers."""

from unittest.mock import Mock, patch

import pr_reviewer


def test_fetch_pr_reviews_normalizes_ids_and_urls():
    result = Mock(
        returncode=0,
        stdout='[[{"id": 123, "user": {"login": "coderabbitai[bot]"}, "body": "fix", "state": "COMMENTED", "submitted_at": "2026-03-11T12:00:00Z", "html_url": "https://github.com/owner/repo/pull/1#pullrequestreview-123"}]]',
        stderr="",
    )

    with patch("pr_reviewer.subprocess.run", return_value=result):
        reviews = pr_reviewer.fetch_pr_reviews("owner/repo", 1)

    assert reviews == [
        {
            "id": "r123",
            "databaseId": 123,
            "author": {"login": "coderabbitai[bot]"},
            "body": "fix",
            "state": "COMMENTED",
            "submittedAt": "2026-03-11T12:00:00Z",
            "url": "https://github.com/owner/repo/pull/1#pullrequestreview-123",
        }
    ]


def test_fetch_pr_review_comments_flattens_paginated_response():
    result = Mock(
        returncode=0,
        stdout='[[{"id": 10, "body": "a"}], [{"id": 11, "body": "b"}]]',
        stderr="",
    )

    with patch("pr_reviewer.subprocess.run", return_value=result):
        comments = pr_reviewer.fetch_pr_review_comments("owner/repo", 1)

    assert comments == [{"id": 10, "body": "a"}, {"id": 11, "body": "b"}]


def test_fetch_issue_comments_flattens_paginated_response():
    result = Mock(
        returncode=0,
        stdout='[[{"id": 21, "body": "a"}], [{"id": 22, "body": "b"}]]',
        stderr="",
    )

    with patch("pr_reviewer.subprocess.run", return_value=result):
        comments = pr_reviewer.fetch_issue_comments("owner/repo", 1)

    assert comments == [{"id": 21, "body": "a"}, {"id": 22, "body": "b"}]
