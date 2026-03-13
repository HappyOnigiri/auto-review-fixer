#!/usr/bin/env python3
"""
GitHub PR fetcher using gh CLI.
Fetches open PRs from a GitHub repository.
"""

import json
import sys
from typing import Any

from subprocess_helpers import run_command


def fetch_open_prs(repo: str, limit: int = 1000) -> list[dict[str, Any]]:
    """
    Fetch open PRs from a GitHub repository.

    Args:
        repo: Repository in format 'owner/repo'
        limit: Maximum number of PRs to fetch

    Returns:
        List of PR data dictionaries
    """
    cmd = [
        "gh",
        "pr",
        "list",
        "--repo",
        repo,
        "--state",
        "open",
        "--json",
        "number,title,author,createdAt,updatedAt,labels,isDraft",
        "--limit",
        str(limit),
    ]

    result = run_command(cmd, check=False)

    if result.returncode != 0:
        raise RuntimeError(f"Error fetching PRs for '{repo}': {result.stderr.strip()}")

    try:
        data = json.loads(result.stdout) if result.stdout else []
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Failed to parse PR list for '{repo}'") from exc
    if not isinstance(data, list):
        raise RuntimeError(f"Unexpected PR list response for '{repo}'")
    return data


def format_pr_output(prs: list[dict[str, Any]]) -> str:
    """Format PR data for display."""
    if not prs:
        return "No open PRs found."

    output = f"Found {len(prs)} open PR(s):\n\n"
    for pr in prs:
        author = pr.get("author", {}).get("login", "Unknown")
        created = pr.get("createdAt", "Unknown")[:10]  # Date only
        labels = ", ".join([label.get("name", "") for label in pr.get("labels", [])])
        labels_str = f" [{labels}]" if labels else ""

        output += f"#{pr['number']}: {pr['title']}\n"
        output += f"   Author: {author}, Created: {created}{labels_str}\n\n"

    return output


def main():
    if len(sys.argv) < 2:
        print("Usage: python github_pr_fetcher.py <repo> [limit]")
        print("Example: python github_pr_fetcher.py HappyOnigiri/ComfyUI-Meld 10")
        sys.exit(1)

    repo = sys.argv[1]
    try:
        limit = int(sys.argv[2]) if len(sys.argv) > 2 else 1000
    except ValueError:
        print(f"Error: limit must be an integer, got '{sys.argv[2]}'", file=sys.stderr)
        print("Usage: python github_pr_fetcher.py <repo> [limit]")
        sys.exit(1)

    prs = fetch_open_prs(repo, limit)
    print(format_pr_output(prs))

    # Output JSON for programmatic use
    print("\n--- JSON Output ---")
    print(json.dumps(prs, indent=2))


if __name__ == "__main__":
    main()
