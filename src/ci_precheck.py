#!/usr/bin/env python3
"""Preflight check for auto-review workflow.

This script is designed to run before heavy setup steps in CI.
It checks:
  1) whether there are open PRs in configured repositories
  2) whether at least one PR appears to have CodeRabbit review targets
  3) whether at least one PR is behind its base branch and needs merge
"""

from __future__ import annotations

import fnmatch
import json
import os
import subprocess
import sys
from dataclasses import dataclass
from typing import Any

CODERABBIT_BOT_LOGIN_PREFIX = "coderabbitai"


@dataclass
class PrecheckResult:
    has_open_pr: bool
    has_review_target: bool
    has_behind_pr: bool
    target_prs: list[str]
    pr_statuses: list[tuple[str, str]]  # (repo#pr_number, status)

    @property
    def should_run(self) -> bool:
        return self.has_review_target or self.has_behind_pr


def _run_gh_json(cmd: list[str]) -> Any:
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=False,
            encoding="utf-8",
            timeout=60,
        )
    except subprocess.TimeoutExpired as e:
        raise RuntimeError(f"gh command timed out: {' '.join(cmd)}") from e
    if result.returncode != 0:
        stderr = (result.stderr or "").strip()
        raise RuntimeError(f"gh command failed: {' '.join(cmd)}: {stderr}")
    if not result.stdout:
        return []
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as e:
        raise RuntimeError(f"Failed to parse gh output for command: {' '.join(cmd)}") from e


def _list_repositories_for_owner(owner: str) -> list[str]:
    data = _run_gh_json(
        [
            "gh",
            "repo",
            "list",
            owner,
            "--limit",
            "1000",
            "--json",
            "nameWithOwner",
        ]
    )
    if not isinstance(data, list):
        raise RuntimeError(f"Unexpected gh repo list output for owner '{owner}'")
    if len(data) >= 1000:
        raise RuntimeError(
            f"gh repo list for '{owner}' returned {len(data)} results which may be truncated (limit=1000)"
        )

    repos: list[str] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        name_with_owner = item.get("nameWithOwner")
        if isinstance(name_with_owner, str) and name_with_owner.startswith(f"{owner}/"):
            repos.append(name_with_owner)
    return repos


def _expand_repo_spec(owner: str, name_spec: str) -> list[str]:
    if "*" not in name_spec:
        return [f"{owner}/{name_spec}"]

    repos = _list_repositories_for_owner(owner)
    matched: list[str] = []
    for repo in repos:
        _, repo_name = repo.split("/", 1)
        if fnmatch.fnmatchcase(repo_name, name_spec):
            matched.append(repo)
    return matched


def parse_repos_from_env(repos_env: str) -> list[str]:
    repos: list[str] = []
    for entry in repos_env.split(","):
        entry = entry.strip()
        if not entry:
            continue
        repo_spec = entry.split(":", 2)[0]
        owner_name = repo_spec.split("/", 1)
        if repo_spec.count("/") != 1 or not owner_name[0] or not owner_name[1]:
            print(
                f"Warning: skipping invalid repo entry '{repo_spec}' (expected owner/name)",
                file=sys.stderr,
            )
            continue
        owner, name_spec = owner_name
        if "*" in owner:
            print(
                f"Warning: skipping invalid wildcard repo entry '{repo_spec}' (owner wildcard is not supported)",
                file=sys.stderr,
            )
            continue
        repos.extend(_expand_repo_spec(owner, name_spec))

    # Keep order while deduplicating
    deduped: list[str] = []
    seen: set[str] = set()
    for repo in repos:
        if repo not in seen:
            deduped.append(repo)
            seen.add(repo)
    return deduped


def _list_open_pr_numbers(repo: str, limit: int = 1000) -> list[int]:
    prs = _run_gh_json(
        [
            "gh",
            "pr",
            "list",
            "--repo",
            repo,
            "--state",
            "open",
            "--json",
            "number",
            "--limit",
            str(limit),
        ]
    )
    if not isinstance(prs, list):
        raise RuntimeError(f"Unexpected gh pr list output for repo '{repo}'")

    numbers: list[int] = []
    for pr in prs:
        if isinstance(pr, dict):
            number = pr.get("number")
            if isinstance(number, int):
                numbers.append(number)
    if len(numbers) >= limit:
        raise RuntimeError(
            f"gh pr list for '{repo}' returned {len(numbers)} results which may be truncated (limit={limit})"
        )
    return numbers


def _get_pr_branches(repo: str, pr_number: int) -> tuple[str, str]:
    """Return (base_branch, head_branch) for a PR."""
    pr_data = _run_gh_json(
        [
            "gh",
            "pr",
            "view",
            str(pr_number),
            "--repo",
            repo,
            "--json",
            "baseRefName,headRefName",
        ]
    )
    if not isinstance(pr_data, dict):
        raise RuntimeError(f"Unexpected gh pr view output for {repo}#{pr_number}")
    base_branch = pr_data.get("baseRefName")
    head_branch = pr_data.get("headRefName")
    if not isinstance(base_branch, str) or not base_branch:
        raise RuntimeError(f"Missing baseRefName for {repo}#{pr_number}")
    if not isinstance(head_branch, str) or not head_branch:
        raise RuntimeError(f"Missing headRefName for {repo}#{pr_number}")
    return base_branch, head_branch


def get_branch_compare_status(repo: str, base_branch: str, current_branch: str) -> tuple[str, int]:
    """Return compare API (status, behind_by) for base...current."""
    compare_data = _run_gh_json(
        [
            "gh",
            "api",
            f"repos/{repo}/compare/{base_branch}...{current_branch}",
        ]
    )
    if not isinstance(compare_data, dict):
        raise RuntimeError(
            f"Unexpected compare output for {repo} {base_branch}...{current_branch}"
        )
    status = compare_data.get("status")
    behind_by_raw = compare_data.get("behind_by")
    if not isinstance(status, str):
        raise RuntimeError(
            f"Missing compare status for {repo} {base_branch}...{current_branch}"
        )
    if not isinstance(behind_by_raw, int):
        raise RuntimeError(
            f"Missing compare behind_by for {repo} {base_branch}...{current_branch}"
        )
    return status, behind_by_raw


def needs_base_merge(compare_status: str, behind_by: int) -> bool:
    """Determine whether base branch merge is needed."""
    return behind_by >= 1 or compare_status in {"behind", "diverged"}


def _get_pr_status_and_ids(repo: str, pr_number: int) -> tuple[str, list[str]]:
    """Returns (status, list of review/comment IDs for DB check)."""
    owner, name = repo.split("/", 1)
    reviews_query = """
query($owner: String!, $name: String!, $number: Int!, $after: String) {
  repository(owner: $owner, name: $name) {
    pullRequest(number: $number) {
      reviews(first: 50, after: $after) {
        pageInfo { hasNextPage endCursor }
        nodes {
          id
          author { login }
        }
      }
    }
  }
}
"""
    threads_query = """
query($owner: String!, $name: String!, $number: Int!, $after: String) {
  repository(owner: $owner, name: $name) {
    pullRequest(number: $number) {
      reviewThreads(first: 100, after: $after) {
        pageInfo { hasNextPage endCursor }
        nodes {
          id
          isResolved
          comments(first: 100) {
            pageInfo { hasNextPage endCursor }
            nodes {
              databaseId
              author { login }
            }
          }
        }
      }
    }
  }
}
"""
    thread_comments_query = """
query($threadId: ID!, $commentAfter: String) {
  node(id: $threadId) {
    ... on PullRequestReviewThread {
      isResolved
      comments(first: 100, after: $commentAfter) {
        pageInfo { hasNextPage endCursor }
        nodes {
          databaseId
          author { login }
        }
      }
    }
  }
}
"""

    def _build_cmd(query: str, after_cursor: str | None) -> list[str]:
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
        if after_cursor is not None:
            cmd += ["-f", f"after={after_cursor}"]
        return cmd

    def _build_thread_comments_cmd(thread_id: str, comment_after: str | None) -> list[str]:
        cmd = [
            "gh",
            "api",
            "graphql",
            "-f",
            f"query={thread_comments_query}",
            "-F",
            f"threadId={thread_id}",
        ]
        if comment_after is not None:
            cmd += ["-f", f"commentAfter={comment_after}"]
        return cmd

    def _fetch_all_thread_comments(thread_id: str) -> list[dict[str, Any]]:
        """Fetch all comments for a thread (handles pagination when >100 comments).

        Raises:
            RuntimeError: If the GraphQL response payload is malformed or missing expected fields.
        """
        all_comments: list[dict[str, Any]] = []
        comment_after: str | None = None
        while True:
            cmd = _build_thread_comments_cmd(thread_id, comment_after)
            data = _run_gh_json(cmd)
            node_data = data.get("data", {}).get("node", {})
            if not isinstance(node_data, dict):
                raise RuntimeError(
                    f"Unexpected thread_comments payload for thread {thread_id}: "
                    f"node is not a dict (type={type(node_data).__name__}), cmd={cmd!r}"
                )
            comments_data = node_data.get("comments", {})
            if not isinstance(comments_data, dict):
                raise RuntimeError(
                    f"Unexpected thread_comments payload for thread {thread_id}: "
                    f"comments is not a dict (type={type(comments_data).__name__}), cmd={cmd!r}"
                )
            nodes = comments_data.get("nodes", [])
            if isinstance(nodes, list):
                all_comments.extend(nodes)
            page_info = comments_data.get("pageInfo", {})
            if not page_info.get("hasNextPage"):
                break
            comment_after = page_info.get("endCursor")
            if not comment_after:
                raise RuntimeError(
                    f"Unexpected thread_comments payload for thread {thread_id}: "
                    f"hasNextPage=true but endCursor is missing, cmd={cmd!r}"
                )
        return all_comments

    # Phase 1: Check review-level CodeRabbit reviews (paginated)
    ids: list[str] = []
    after_cursor: str | None = None
    while True:
        data = _run_gh_json(_build_cmd(reviews_query, after_cursor))
        pr_data = data.get("data", {}).get("repository", {}).get("pullRequest", {})
        if not isinstance(pr_data, dict):
            raise RuntimeError(
                f"Unexpected Phase 1 payload for {repo}#{pr_number}: "
                f"pullRequest is not a dict (type={type(pr_data).__name__})"
            )
        reviews_data = pr_data.get("reviews", {})
        if isinstance(reviews_data, dict):
            review_nodes = reviews_data.get("nodes", [])
            if isinstance(review_nodes, list):
                for r in review_nodes:
                    if isinstance(r, dict) and r.get("id"):
                        login = (r.get("author") or {}).get("login") if isinstance(r.get("author"), dict) else None
                        if isinstance(login, str) and login.startswith(CODERABBIT_BOT_LOGIN_PREFIX):
                            ids.append(r["id"])
            page_info = reviews_data.get("pageInfo", {})
            if not page_info.get("hasNextPage"):
                break
            after_cursor = page_info.get("endCursor")
            if not after_cursor:
                break
        else:
            raise RuntimeError(
                f"Unexpected Phase 1 payload for {repo}#{pr_number}: "
                f"reviews is not a dict (type={type(reviews_data).__name__})"
            )

    # Phase 2: Check review threads (paginated)
    has_any_coderabbit = bool(ids)
    after_cursor = None
    while True:
        data = _run_gh_json(_build_cmd(threads_query, after_cursor))
        pr_data = data.get("data", {}).get("repository", {}).get("pullRequest", {})
        if not isinstance(pr_data, dict):
            if ids or has_any_coderabbit:
                raise RuntimeError(f"Unexpected Phase 2 payload for {repo}#{pr_number}: pr_data is not a dict")
            return ("skip:no_coderabbit", [])

        review_threads_data = pr_data.get("reviewThreads", {})
        if not isinstance(review_threads_data, dict):
            if ids or has_any_coderabbit:
                raise RuntimeError(f"Unexpected Phase 2 payload for {repo}#{pr_number}: reviewThreads is not a dict")
            return ("skip:no_coderabbit", [])
        threads = review_threads_data.get("nodes", [])
        if not isinstance(threads, list):
            if ids or has_any_coderabbit:
                raise RuntimeError(f"Unexpected Phase 2 payload for {repo}#{pr_number}: threads is not a list")
            return ("skip:no_coderabbit", [])

        for thread in threads:
            if not isinstance(thread, dict):
                continue
            is_resolved = thread.get("isResolved")
            comments_data = thread.get("comments", {})
            comments = comments_data.get("nodes", []) if isinstance(comments_data, dict) else []
            if not isinstance(comments, list):
                continue
            # Paginate comments when a thread has more than first page (100)
            if isinstance(comments_data, dict) and comments_data.get("pageInfo", {}).get("hasNextPage"):
                thread_id = thread.get("id")
                if isinstance(thread_id, str):
                    comments = _fetch_all_thread_comments(thread_id)
            for comment in comments:
                if not isinstance(comment, dict):
                    continue
                author = comment.get("author", {})
                login = author.get("login") if isinstance(author, dict) else None
                if isinstance(login, str) and login.startswith(CODERABBIT_BOT_LOGIN_PREFIX):
                    has_any_coderabbit = True
                    if not is_resolved and comment.get("databaseId") is not None:
                        ids.append(f"discussion_r{comment['databaseId']}")

        page_info = review_threads_data.get("pageInfo", {})
        if not page_info.get("hasNextPage"):
            break
        after_cursor = page_info.get("endCursor")
        if not after_cursor:
            break

    if ids:
        return ("target", ids)
    status = "skip:all_resolved" if has_any_coderabbit else "skip:no_coderabbit"
    return (status, ids)


def _db_available() -> bool:
    """Return True if Turso/DB credentials are set for formal verification."""
    url = os.environ.get("REFIX_TURSO_DATABASE_URL", "").strip()
    token = os.environ.get("REFIX_TURSO_AUTH_TOKEN", "").strip()
    return bool(url and token)


def _filter_targets_by_db(
    candidate_targets: list[tuple[str, list[str]]],
) -> tuple[list[str], list[tuple[str, str]]]:
    """Filter targets by DB: remove PRs where all review/comment IDs are processed."""
    try:
        from review_db import init_db, is_processed
    except ImportError as e:
        print(f"Warning: DB verification failed, skipping formal check: {e}", file=sys.stderr)
        return [pr_key for pr_key, _ in candidate_targets], []

    import libsql  # safe: review_db already imported libsql successfully

    try:
        init_db()

        confirmed_targets: list[str] = []
        status_updates: list[tuple[str, str]] = []  # (pr_key, new_status)

        for pr_key, ids in candidate_targets:
            if not ids:
                continue
            all_processed = all(is_processed(rid) for rid in ids)
            if all_processed:
                status_updates.append((pr_key, "skip:all_processed"))
            else:
                confirmed_targets.append(pr_key)

        return confirmed_targets, status_updates
    except libsql.Error as e:
        print(f"Warning: DB verification failed, skipping formal check: {e}", file=sys.stderr)
        return [pr_key for pr_key, _ in candidate_targets], []


def check_review_targets(repos: list[str]) -> PrecheckResult:
    has_open_pr = False
    has_behind_pr = False
    target_prs: list[str] = []
    pr_statuses: list[tuple[str, str]] = []
    review_candidate_targets: list[tuple[str, list[str]]] = []  # (pr_key, ids)
    pr_keys_in_order: list[str] = []
    review_base_status: dict[str, str] = {}
    review_target_status: dict[str, bool] = {}
    behind_status: dict[str, bool] = {}

    for repo in repos:
        pr_numbers = _list_open_pr_numbers(repo)
        if not pr_numbers:
            continue
        has_open_pr = True
        for pr_number in pr_numbers:
            pr_key = f"{repo}#{pr_number}"
            pr_keys_in_order.append(pr_key)
            review_status, ids = _get_pr_status_and_ids(repo, pr_number)
            review_base_status[pr_key] = review_status
            is_review_target = review_status == "target"
            review_target_status[pr_key] = is_review_target
            if is_review_target:
                review_candidate_targets.append((pr_key, ids))

            base_branch, head_branch = _get_pr_branches(repo, pr_number)
            compare_status, behind_by = get_branch_compare_status(repo, base_branch, head_branch)
            is_behind = needs_base_merge(compare_status, behind_by)
            behind_status[pr_key] = is_behind
            if is_behind:
                has_behind_pr = True

    # DB verification: if we have candidates and DB is available, filter confirmed targets
    review_status_updates: dict[str, str] = {}
    if review_candidate_targets and _db_available():
        confirmed_targets, status_updates = _filter_targets_by_db(review_candidate_targets)
        confirmed_set = set(confirmed_targets)
        for pr_key, _ in review_candidate_targets:
            review_target_status[pr_key] = pr_key in confirmed_set
        review_status_updates = dict(status_updates)

    # Build final statuses/targets after review + behind evaluation
    for pr_key in pr_keys_in_order:
        is_review_target = review_target_status.get(pr_key, False)
        is_behind = behind_status.get(pr_key, False)
        if is_review_target and is_behind:
            final_status = "target:review+behind"
        elif is_review_target:
            final_status = "target"
        elif is_behind:
            final_status = "target:behind"
        else:
            final_status = review_status_updates.get(pr_key, review_base_status.get(pr_key, "skip:no_coderabbit"))
        pr_statuses.append((pr_key, final_status))
        if is_review_target or is_behind:
            target_prs.append(pr_key)

    return PrecheckResult(
        has_open_pr=has_open_pr,
        has_review_target=any(review_target_status.values()),
        has_behind_pr=has_behind_pr,
        target_prs=target_prs,
        pr_statuses=pr_statuses,
    )


def _write_github_output(key: str, value: str) -> None:
    output_file = os.environ.get("GITHUB_OUTPUT", "").strip()
    if not output_file:
        return
    with open(output_file, "a", encoding="utf-8") as f:
        f.write(f"{key}={value}\n")


def _write_github_output_target_prs(target_prs: list[str]) -> None:
    """Write target_prs to GITHUB_OUTPUT (multiline format)."""
    output_file = os.environ.get("GITHUB_OUTPUT", "").strip()
    if not output_file:
        return
    with open(output_file, "a", encoding="utf-8") as f:
        f.write("target_prs<<EOF\n")
        f.write("\n".join(target_prs))
        f.write("\nEOF\n")


def _write_github_output_pr_statuses(pr_statuses: list[tuple[str, str]]) -> None:
    """Write pr_statuses to GITHUB_OUTPUT (multiline: repo#pr: status)."""
    output_file = os.environ.get("GITHUB_OUTPUT", "").strip()
    if not output_file:
        return
    lines = [f"{pr_key}: {status}" for pr_key, status in pr_statuses]
    with open(output_file, "a", encoding="utf-8") as f:
        f.write("pr_statuses<<EOF\n")
        f.write("\n".join(lines))
        f.write("\nEOF\n")


def main() -> int:
    repos_env = os.environ.get("REPOS")
    if repos_env is None:
        print("Error: REPOS is not set.", file=sys.stderr)
        return 1
    if not repos_env.strip():
        print("Error: REPOS is set but empty.", file=sys.stderr)
        return 1

    try:
        repos = parse_repos_from_env(repos_env)
    except Exception as e:
        print(f"Warning: failed to parse repos: {e}", file=sys.stderr)
        _write_github_output("has_open_pr", "false")
        _write_github_output("has_review_target", "false")
        _write_github_output("has_behind_pr", "false")
        _write_github_output("should_run", "true")
        _write_github_output_target_prs([])
        _write_github_output_pr_statuses([])
        return 0

    if not repos:
        print("No repositories to check after parsing REPOS.")
        _write_github_output("has_open_pr", "false")
        _write_github_output("has_review_target", "false")
        _write_github_output("has_behind_pr", "false")
        _write_github_output("should_run", "false")
        _write_github_output_target_prs([])
        _write_github_output_pr_statuses([])
        return 0

    print(f"Precheck repositories: {len(repos)}")
    for repo in repos:
        print(f"  - {repo}")

    try:
        result = check_review_targets(repos)
    except Exception as e:
        print(f"Warning: failed to check review targets: {e}", file=sys.stderr)
        _write_github_output("has_open_pr", "false")
        _write_github_output("has_review_target", "false")
        _write_github_output("has_behind_pr", "false")
        _write_github_output("should_run", "true")
        _write_github_output_target_prs([])
        _write_github_output_pr_statuses([])
        return 0

    print(f"has_open_pr={str(result.has_open_pr).lower()}")
    print(f"has_review_target={str(result.has_review_target).lower()}")
    print(f"has_behind_pr={str(result.has_behind_pr).lower()}")
    if result.pr_statuses:
        print("PRs:")
        for pr_key, status in result.pr_statuses:
            print(f"  - {pr_key}: {status}")

    _write_github_output("has_open_pr", str(result.has_open_pr).lower())
    _write_github_output("has_review_target", str(result.has_review_target).lower())
    _write_github_output("has_behind_pr", str(result.has_behind_pr).lower())
    _write_github_output("should_run", str(result.should_run).lower())
    _write_github_output_target_prs(result.target_prs)
    _write_github_output_pr_statuses(result.pr_statuses)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
