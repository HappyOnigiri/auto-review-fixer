#!/usr/bin/env python3
"""
GitHub PR reviewer - fetches PR content and CodeRabbit reviews.
Displays review comments that are newer than the latest commit.
"""

import json
import re
import sys
from datetime import datetime
from typing import Any

from subprocess_helpers import SubprocessError, run_command

_GITHUB_ACTIONS_RUN_URL_RE = re.compile(r"/actions/runs/(\d+)")

# Set UTF-8 encoding for output
if sys.stdout.encoding != "utf-8" and hasattr(sys.stdout, "buffer"):
    import io

    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")


def _filter_check_runs(runs: list[dict[str, Any]], repo: str) -> list[dict[str, Any]]:
    """check run をフィルタリングする。
    - workflow_dispatch トリガーの run を除外
    - 同名 check run は最新（id が最大）のみ保持
    """
    # run ID ごとにグループ化
    run_id_to_runs: dict[str, list[dict[str, Any]]] = {}
    no_run_id: list[dict[str, Any]] = []
    for r in runs:
        url = r.get("details_url") or r.get("html_url") or ""
        m = _GITHUB_ACTIONS_RUN_URL_RE.search(url)
        if m:
            run_id_to_runs.setdefault(m.group(1), []).append(r)
        else:
            no_run_id.append(r)

    # workflow_dispatch の run ID を特定
    dispatch_run_ids: set[str] = set()
    for run_id in run_id_to_runs:
        try:
            result = run_command(
                ["gh", "api", f"repos/{repo}/actions/runs/{run_id}", "--jq", ".event"],
                check=False,
                timeout=10,
            )
            if result.returncode == 0:
                event = (result.stdout or "").strip().strip('"')
                if event == "workflow_dispatch":
                    dispatch_run_ids.add(run_id)
        except SubprocessError:
            pass  # API 失敗時はフィルタせずそのまま残す

    # dispatch を除外
    filtered: list[dict[str, Any]] = list(no_run_id)
    for run_id, run_list in run_id_to_runs.items():
        if run_id not in dispatch_run_ids:
            filtered.extend(run_list)

    # 同名 check run は id が最大のものだけ保持
    by_name: dict[str, dict[str, Any]] = {}
    for r in filtered:
        name = r.get("name") or ""
        existing = by_name.get(name)
        if existing is None or (r.get("id") or 0) > (existing.get("id") or 0):
            by_name[name] = r

    return list(by_name.values())


def _flatten_paginated_response(data: Any) -> list[dict[str, Any]]:
    """Flatten gh api --paginate/--slurp responses into a list of objects."""
    if not isinstance(data, list):
        return []

    items: list[dict[str, Any]] = []
    for item in data:
        if isinstance(item, list):
            items.extend(entry for entry in item if isinstance(entry, dict))
        elif isinstance(item, dict):
            items.append(item)
    return items


def _fetch_check_runs_via_rest(repo: str, ref: str) -> list[dict[str, Any]]:
    """Fetch check runs for a commit via REST API.
    NOTE: statusCheckRollup (GraphQL) must NOT be used - Fine-grained PAT cannot access it.
    Use REST check-runs API only. On 403 or error, returns []."""
    cmd = [
        "gh",
        "api",
        f"repos/{repo}/commits/{ref}/check-runs",
        "--paginate",
        "--slurp",
    ]
    try:
        result = run_command(cmd, check=False)
    except SubprocessError:
        return []
    if result.returncode != 0:
        return []
    try:
        pages = json.loads(result.stdout) if result.stdout else []
    except json.JSONDecodeError:
        return []
    runs: list[dict[str, Any]] = []
    for page in pages if isinstance(pages, list) else []:
        if isinstance(page, dict):
            runs.extend(
                r for r in (page.get("check_runs") or []) if isinstance(r, dict)
            )
    runs = _filter_check_runs(runs, repo)
    # Convert to format expected by _extract_failing_ci_contexts (name, conclusion, state, detailsUrl, targetUrl)
    rollup: list[dict[str, Any]] = []
    for r in runs:
        rollup.append(
            {
                "name": r.get("name") or "",
                "conclusion": (r.get("conclusion") or "").upper(),
                "state": (r.get("status") or "").upper(),
                "detailsUrl": r.get("details_url") or r.get("html_url") or "",
                "targetUrl": r.get("details_url") or r.get("html_url") or "",
            }
        )
    return rollup


def _fetch_classic_statuses_via_rest(repo: str, sha: str) -> list[dict[str, Any]]:
    """Fetch classic commit statuses (Jenkins, Travis, etc.) via REST API.
    Returns normalized entries in the same format as _fetch_check_runs_via_rest."""
    cmd = ["gh", "api", f"repos/{repo}/commits/{sha}/status"]
    try:
        result = run_command(cmd, check=False)
    except SubprocessError:
        return []
    if result.returncode != 0:
        return []
    try:
        data = json.loads(result.stdout) if result.stdout else {}
    except json.JSONDecodeError:
        return []
    if not isinstance(data, dict):
        return []
    statuses = data.get("statuses") or []
    normalized: list[dict[str, Any]] = []
    for s in statuses:
        if not isinstance(s, dict):
            continue
        state = (s.get("state") or "").upper()
        normalized.append(
            {
                "name": str(
                    s.get("context") or s.get("description") or "unknown-status"
                ),
                "conclusion": state,
                "state": state,
                "detailsUrl": str(s.get("target_url") or ""),
                "targetUrl": str(s.get("target_url") or ""),
            }
        )
    return normalized


def fetch_pr_details(repo: str, pr_number: int) -> dict[str, Any]:
    """Fetch PR details including commits, reviews, comments, and branch name.
    NOTE: statusCheckRollup (GraphQL) must NOT be used - Fine-grained PAT cannot access it.
    Uses REST check-runs API for CI status only."""
    base_json = "number,title,body,commits,reviews,comments,createdAt,updatedAt,labels,headRefName,baseRefName,headRefOid"
    cmd = [
        "gh",
        "pr",
        "view",
        str(pr_number),
        "--repo",
        repo,
        "--json",
        base_json,
    ]
    result = run_command(cmd, check=False)
    if result.returncode != 0:
        raise SubprocessError(
            f"Failed to fetch PR details for {repo}#{pr_number}: {result.stderr.strip()}",
            returncode=result.returncode,
            stderr=result.stderr or "",
        )
    try:
        pr_data = json.loads(result.stdout) if result.stdout else {}
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            f"Failed to parse gh pr view output for {repo}#{pr_number}"
        ) from exc

    # Fetch check runs via REST (works with Fine-grained PAT for repos with access)
    # Use headRefOid as primary source to avoid the 100-commit limit of gh pr view --json commits
    head_oid = str(pr_data.get("headRefOid") or "").strip()
    if not head_oid:
        commits = pr_data.get("commits") or []
        if commits:
            head_oid = (
                str(commits[-1].get("oid") or "")
                if isinstance(commits[-1], dict)
                else ""
            )
    if head_oid:
        check_runs = _fetch_check_runs_via_rest(repo, head_oid)
        classic_statuses = _fetch_classic_statuses_via_rest(repo, head_oid)
        all_checks = check_runs + classic_statuses
        if all_checks:
            pr_data["check_runs"] = all_checks

    normalized_reviews = fetch_pr_reviews(repo, pr_number)
    if normalized_reviews:
        pr_data["reviews"] = normalized_reviews
    return pr_data


def fetch_pr_reviews(repo: str, pr_number: int) -> list[dict[str, Any]]:
    """Fetch top-level PR reviews via REST and normalize them for the fixer."""
    cmd = [
        "gh",
        "api",
        f"repos/{repo}/pulls/{pr_number}/reviews",
        "--paginate",
        "--slurp",
    ]
    try:
        result = run_command(cmd, check=False)
    except SubprocessError as exc:
        raise RuntimeError(
            f"Failed to fetch PR reviews for {repo}#{pr_number}: {exc}"
        ) from exc
    if result.returncode != 0:
        raise RuntimeError(
            f"Failed to fetch PR reviews for {repo}#{pr_number}: {result.stderr}"
        )

    try:
        reviews = json.loads(result.stdout) if result.stdout else []
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            f"Failed to parse PR reviews response for {repo}#{pr_number}"
        ) from exc

    normalized_reviews: list[dict[str, Any]] = []
    for review in _flatten_paginated_response(reviews):
        database_id = review.get("id")
        if not database_id:
            continue
        normalized_reviews.append(
            {
                "id": f"r{database_id}",
                "databaseId": database_id,
                "author": {"login": review.get("user", {}).get("login", "Unknown")},
                "body": review.get("body", ""),
                "state": review.get("state", ""),
                "submittedAt": review.get("submitted_at", ""),
                "url": review.get("html_url", ""),
            }
        )
    return normalized_reviews


def fetch_pr_review_comments(repo: str, pr_number: int) -> list[dict[str, Any]]:
    """Fetch inline review comments (discussion_r<id> format) via REST API."""
    cmd = [
        "gh",
        "api",
        f"repos/{repo}/pulls/{pr_number}/comments",
        "--paginate",
        "--slurp",
    ]
    try:
        result = run_command(cmd, check=False)
    except SubprocessError as exc:
        raise RuntimeError(
            f"Failed to fetch review comments for {repo}#{pr_number}: {exc}"
        ) from exc
    if result.returncode != 0:
        raise RuntimeError(
            f"Failed to fetch review comments for {repo}#{pr_number}: {result.stderr}"
        )
    try:
        data = json.loads(result.stdout) if result.stdout else []
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            f"Failed to parse review comments response for {repo}#{pr_number}"
        ) from exc
    return _flatten_paginated_response(data)


def fetch_issue_comments(repo: str, pr_number: int) -> list[dict[str, Any]]:
    """Fetch issue comments for a pull request via REST API."""
    cmd = [
        "gh",
        "api",
        f"repos/{repo}/issues/{pr_number}/comments",
        "--paginate",
        "--slurp",
    ]
    result = run_command(cmd, check=False)
    if result.returncode != 0:
        raise RuntimeError(f"failed to fetch issue comments: {result.stderr}")
    try:
        data = json.loads(result.stdout) if result.stdout else []
    except json.JSONDecodeError as e:
        raise RuntimeError(f"failed to parse issue comments response: {e}") from e
    return _flatten_paginated_response(data)


def fetch_review_threads(repo: str, pr_number: int) -> dict[int, str]:
    """Fetch unresolved review threads and return {comment_db_id: thread_node_id}."""
    owner, name = repo.split("/")
    query = """
query($owner: String!, $name: String!, $number: Int!) {
  repository(owner: $owner, name: $name) {
    pullRequest(number: $number) {
      reviewThreads(first: 100) {
        nodes {
          id
          isResolved
          comments(first: 1) {
            nodes {
              databaseId
            }
          }
        }
      }
    }
  }
}
"""
    cmd = [
        "gh",
        "api",
        "graphql",
        "-f",
        f"query={query}",
        "-F",
        f"owner={owner}",
        "-F",
        f"name={name}",
        "-F",
        f"number={pr_number}",
    ]
    try:
        result = run_command(cmd, check=False)
    except SubprocessError as exc:
        raise RuntimeError(
            f"Failed to fetch review threads for {repo}#{pr_number}: {exc}"
        ) from exc
    if result.returncode != 0:
        raise RuntimeError(
            f"Failed to fetch review threads for {repo}#{pr_number}: {result.stderr}"
        )
    try:
        data = json.loads(result.stdout) if result.stdout else {}
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            f"Failed to parse review threads response for {repo}#{pr_number}"
        ) from exc
    threads = (
        data.get("data", {})
        .get("repository", {})
        .get("pullRequest", {})
        .get("reviewThreads", {})
        .get("nodes", [])
    )
    mapping: dict[int, str] = {}
    for thread in threads:
        if thread.get("isResolved"):
            continue
        comments = thread.get("comments", {}).get("nodes", [])
        if comments and comments[0].get("databaseId"):
            mapping[comments[0]["databaseId"]] = thread["id"]
    return mapping


def resolve_review_thread(thread_node_id: str) -> bool:
    """Resolve a review thread by its GraphQL node ID. Returns True on success."""
    mutation = """
mutation($threadId: ID!) {
  resolveReviewThread(input: {threadId: $threadId}) {
    thread { id isResolved }
  }
}
"""
    cmd = [
        "gh",
        "api",
        "graphql",
        "-f",
        f"query={mutation}",
        "-F",
        f"threadId={thread_node_id}",
    ]
    try:
        result = run_command(cmd, check=False)
    except SubprocessError as exc:
        raise RuntimeError(f"Failed to resolve thread {thread_node_id}: {exc}") from exc
    if result.returncode != 0:
        raise RuntimeError(
            f"Failed to resolve thread {thread_node_id}: {result.stderr}"
        )
    return True


def get_latest_commit_time(commits: list[dict[str, Any]]) -> datetime:
    """Get the timestamp of the latest commit."""
    if not commits:
        return datetime.min
    latest = commits[-1]
    return datetime.fromisoformat(latest["committedDate"].replace("Z", "+00:00"))


def get_review_comments(reviews: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Extract review comments from reviews."""
    all_comments = []
    for review in reviews:
        all_comments.append(
            {
                "author": review.get("author", {}).get("login", "Unknown"),
                "createdAt": review.get("submittedAt", ""),
                "body": review.get("body", ""),
                "state": review.get("state", ""),
            }
        )
    return all_comments


def get_pr_comments(comments: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Extract PR comments."""
    return [
        {
            "author": c.get("author", {}).get("login", "Unknown"),
            "createdAt": c.get("createdAt", ""),
            "body": c.get("body", ""),
            "type": "comment",
        }
        for c in comments
    ]


def filter_reviews_after_commit(
    reviews: list[dict[str, Any]], latest_commit_time: datetime
) -> list[dict[str, Any]]:
    """Filter reviews that are newer than the latest commit."""
    filtered = []
    for review in reviews:
        try:
            review_time = datetime.fromisoformat(
                review["createdAt"].replace("Z", "+00:00")
            )
        except (ValueError, TypeError, KeyError):
            continue
        if review_time > latest_commit_time:
            filtered.append(review)
    return filtered


def format_review_output(pr_data: dict[str, Any]) -> str:
    """Format PR data and reviews for display."""
    output = f"PR #{pr_data.get('number', 'N/A')}: {pr_data.get('title', 'N/A')}\n"
    output += f"Created: {pr_data.get('createdAt', 'N/A')}\n"
    output += f"Updated: {pr_data.get('updatedAt', 'N/A')}\n"
    output += "\n" + "=" * 80 + "\n"

    # Get commit info
    commits = pr_data.get("commits", [])
    output += f"\n[Commits ({len(commits)})]\n"
    if commits:
        latest_commit = commits[-1]
        output += f"  Latest: {latest_commit.get('messageHeadline', 'N/A')}\n"
        output += f"  Committed: {latest_commit.get('committedDate', 'N/A')}\n"
        latest_commit_time = get_latest_commit_time(commits)
    else:
        latest_commit_time = datetime.min

    output += "\n" + "=" * 80 + "\n"

    # Get reviews
    all_reviews = get_review_comments(pr_data.get("reviews", []))
    all_comments = get_pr_comments(pr_data.get("comments", []))

    new_reviews = filter_reviews_after_commit(all_reviews, latest_commit_time)
    new_comments = filter_reviews_after_commit(all_comments, latest_commit_time)

    # Display summary
    output += "\n[Review Summary]\n"
    output += f"  Total Reviews: {len(all_reviews)}\n"
    output += f"  New Reviews (after latest commit): {len(new_reviews)}\n"
    output += f"  Total Comments: {len(all_comments)}\n"
    output += f"  New Comments (after latest commit): {len(new_comments)}\n"

    # Display new reviews
    if new_reviews:
        output += "\n" + "=" * 80 + "\n"
        output += "\n[NEW REVIEWS (after latest commit)]\n"
        for i, review in enumerate(new_reviews, 1):
            output += f"\n--- Review {i} ---\n"
            output += f"Author: {review['author']}\n"
            output += f"Submitted: {review['createdAt']}\n"
            output += f"State: {review.get('state', 'N/A')}\n"
            body_preview = review["body"][:1000]
            output += (
                f"Body:\n{body_preview}...\n"
                if len(review["body"]) > 1000
                else f"Body:\n{body_preview}\n"
            )

    # Display all reviews if needed
    if all_reviews:
        output += "\n" + "=" * 80 + "\n"
        output += "\n[ALL REVIEWS]\n"
        new_review_ids = {r.get("id") for r in new_reviews}
        for i, review in enumerate(all_reviews, 1):
            is_new = review.get("id") in new_review_ids
            marker = "[NEW] " if is_new else "      "
            output += (
                f"\n{marker}Review {i} - {review['author']} ({review['createdAt']})\n"
            )
            output += f"State: {review.get('state', 'N/A')}\n"

    return output


def main():
    if len(sys.argv) < 3:
        print("Usage: python pr_reviewer.py <repo> <pr_number>")
        print("Example: python pr_reviewer.py HappyOnigiri/ComfyUI-Meld 94")
        sys.exit(1)

    repo = sys.argv[1]
    pr_number = int(sys.argv[2])

    print(f"Fetching PR #{pr_number} from {repo}...")
    pr_data = fetch_pr_details(repo, pr_number)

    output = format_review_output(pr_data)
    print(output)

    # Save detailed JSON
    print("\n" + "=" * 80)
    print("📄 Full JSON data (review section):")
    review_section = {
        "commits": pr_data.get("commits", []),
        "reviews_count": len(pr_data.get("reviews", [])),
        "comments_count": len(pr_data.get("comments", [])),
    }
    print(json.dumps(review_section, indent=2, default=str))


if __name__ == "__main__":
    main()
