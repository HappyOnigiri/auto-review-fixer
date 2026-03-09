#!/usr/bin/env python3
"""Preflight check for auto-review workflow.

This script is designed to run before heavy setup steps in CI.
It checks:
  1) whether there are open PRs in configured repositories
  2) whether at least one PR appears to have CodeRabbit review targets
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
    target_prs: list[str]

    @property
    def should_run(self) -> bool:
        return self.has_review_target


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


def _pr_has_coderabbit_review(repo: str, pr_number: int) -> bool:
    data = _run_gh_json(
        [
            "gh",
            "pr",
            "view",
            str(pr_number),
            "--repo",
            repo,
            "--json",
            "reviews",
        ]
    )
    if not isinstance(data, dict):
        return False
    reviews = data.get("reviews", [])
    if not isinstance(reviews, list):
        return False

    for review in reviews:
        if not isinstance(review, dict):
            continue
        if not review.get("id"):
            continue
        author = review.get("author", {})
        login = author.get("login") if isinstance(author, dict) else None
        if isinstance(login, str) and login.startswith(CODERABBIT_BOT_LOGIN_PREFIX):
            return True
    return False


def _pr_has_unresolved_coderabbit_thread(repo: str, pr_number: int) -> bool:
    owner, name = repo.split("/", 1)
    query = """
query($owner: String!, $name: String!, $number: Int!) {
  repository(owner: $owner, name: $name) {
    pullRequest(number: $number) {
      reviewThreads(first: 100) {
        pageInfo { hasNextPage }
        nodes {
          isResolved
          comments(first: 20) {
            pageInfo { hasNextPage }
            nodes {
              author {
                login
              }
            }
          }
        }
      }
    }
  }
}
"""
    data = _run_gh_json(
        [
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
    )

    review_threads_data = (
        data.get("data", {})
        .get("repository", {})
        .get("pullRequest", {})
        .get("reviewThreads", {})
    )
    if not isinstance(review_threads_data, dict):
        return False
    if review_threads_data.get("pageInfo", {}).get("hasNextPage"):
        raise RuntimeError(
            f"PR #{pr_number} in '{repo}' has too many review threads to fetch (first: 100 truncated)"
        )
    threads = review_threads_data.get("nodes", [])
    if not isinstance(threads, list):
        return False

    for thread in threads:
        if not isinstance(thread, dict):
            continue
        if thread.get("isResolved"):
            continue
        comments_data = thread.get("comments", {})
        if isinstance(comments_data, dict) and comments_data.get("pageInfo", {}).get("hasNextPage"):
            raise RuntimeError(
                f"PR #{pr_number} in '{repo}' has a review thread with too many comments to fetch (first: 20 truncated)"
            )
        comments = comments_data.get("nodes", []) if isinstance(comments_data, dict) else []
        if not isinstance(comments, list):
            continue
        for comment in comments:
            if not isinstance(comment, dict):
                continue
            author = comment.get("author", {})
            login = author.get("login") if isinstance(author, dict) else None
            if isinstance(login, str) and login.startswith(CODERABBIT_BOT_LOGIN_PREFIX):
                return True
    return False


def check_review_targets(repos: list[str]) -> PrecheckResult:
    has_open_pr = False
    target_prs: list[str] = []

    for repo in repos:
        pr_numbers = _list_open_pr_numbers(repo)
        if not pr_numbers:
            continue
        has_open_pr = True
        for pr_number in pr_numbers:
            if _pr_has_coderabbit_review(repo, pr_number) or _pr_has_unresolved_coderabbit_thread(
                repo,
                pr_number,
            ):
                target_prs.append(f"{repo}#{pr_number}")

    return PrecheckResult(
        has_open_pr=has_open_pr,
        has_review_target=bool(target_prs),
        target_prs=target_prs,
    )


def _write_github_output(key: str, value: str) -> None:
    output_file = os.environ.get("GITHUB_OUTPUT", "").strip()
    if not output_file:
        return
    with open(output_file, "a", encoding="utf-8") as f:
        f.write(f"{key}={value}\n")


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
        _write_github_output("should_run", "true")
        return 0

    if not repos:
        print("No repositories to check after parsing REPOS.")
        _write_github_output("has_open_pr", "false")
        _write_github_output("has_review_target", "false")
        _write_github_output("should_run", "false")
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
        _write_github_output("should_run", "true")
        return 0

    print(f"has_open_pr={str(result.has_open_pr).lower()}")
    print(f"has_review_target={str(result.has_review_target).lower()}")
    if result.target_prs:
        print("target_prs:")
        for target in result.target_prs:
            print(f"  - {target}")

    _write_github_output("has_open_pr", str(result.has_open_pr).lower())
    _write_github_output("has_review_target", str(result.has_review_target).lower())
    _write_github_output("should_run", str(result.should_run).lower())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
