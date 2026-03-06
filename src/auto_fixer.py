#!/usr/bin/env python3
"""
Auto Review Fixer - Automatically fix CodeRabbit reviews.
Fetches open PRs, gets unresolved reviews, and runs Claude to fix them.
"""

import argparse
import shlex
import subprocess
import sys
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from github_pr_fetcher import fetch_open_prs
from pr_reviewer import fetch_pr_details
from review_db import init_db, is_processed, mark_processed, reset_all


def load_repos_from_file(filepath: str) -> list[dict[str, str]]:
    """Load repository list from file with optional git user config.

    Format: owner/repo:user.name:user.email
    Example: HappyOnigiri/ComfyUI-Meld:Claude HappyOnigiri:253838257+NodeMeld@users.noreply.github.com
    """
    repos = []
    try:
        with open(filepath, "r") as f:
            for line in f:
                line = line.strip()
                # Skip empty lines and comments
                if not line or line.startswith("#"):
                    continue

                # Parse repo entry
                parts = line.split(":")
                repo = parts[0]
                user_name = parts[1] if len(parts) > 1 else None
                user_email = parts[2] if len(parts) > 2 else None

                repos.append({
                    "repo": repo,
                    "user_name": user_name,
                    "user_email": user_email,
                })
    except FileNotFoundError:
        print(f"Error: {filepath} not found", file=sys.stderr)
        sys.exit(1)
    return repos


def prepare_repository(
    repo: str, branch_name: str, user_name: str = None, user_email: str = None
) -> Path:
    """Clone or update repository and checkout to the target branch.

    Optionally sets local git config for user.name and user.email.
    """
    repo_name = repo.split("/")[1]
    works_dir = Path("works") / repo_name
    works_dir.parent.mkdir(parents=True, exist_ok=True)

    if not works_dir.exists():
        print(f"Cloning {repo}...")
        subprocess.run(
            ["git", "clone", f"https://github.com/{repo}.git", str(works_dir)],
            check=True,
        )
    else:
        print(f"Updating {repo}...")
        # Clean any pending merge/conflicts
        subprocess.run(
            ["git", "reset", "--hard"],
            cwd=works_dir,
            check=True,
        )
        subprocess.run(
            ["git", "fetch", "--all"],
            cwd=works_dir,
            check=True,
        )

    # Set local git config if provided
    if user_name:
        print(f"Setting git user.name to '{user_name}'...")
        subprocess.run(
            ["git", "config", "user.name", user_name],
            cwd=works_dir,
            check=True,
        )
    if user_email:
        print(f"Setting git user.email to '{user_email}'...")
        subprocess.run(
            ["git", "config", "user.email", user_email],
            cwd=works_dir,
            check=True,
        )

    print(f"Checking out branch {branch_name}...")
    subprocess.run(
        ["git", "checkout", branch_name],
        cwd=works_dir,
        check=True,
    )
    # Reset to clean state before pulling
    subprocess.run(
        ["git", "reset", "--hard", f"origin/{branch_name}"],
        cwd=works_dir,
        check=True,
    )

    return works_dir


def generate_prompt(pr_number: int, title: str, unresolved_reviews: list[dict[str, Any]]) -> str:
    """Generate prompt for Claude from unresolved PR reviews."""
    review_bodies = [r.get("body", "") for r in unresolved_reviews if r.get("body")]
    review_content = "\n\n".join(review_bodies)

    prompt = f"""以下は PR #{pr_number} "{title}" に対する CodeRabbit のレビューコメントです。
指摘事項を確認し、必要であればコードを修正してください。
修正後は git commit して push してください。

--- レビュー内容 ---
{review_content}
"""
    return prompt


def process_repo(repo_info: dict[str, str], dry_run: bool = False) -> None:
    """Process a single repository for PR fixes.

    Args:
        repo_info: Dict with 'repo', 'user_name', 'user_email' keys
        dry_run: If True, show command without executing
    """
    repo = repo_info["repo"]
    user_name = repo_info.get("user_name")
    user_email = repo_info.get("user_email")

    print(f"\n{'=' * 80}")
    print(f"Processing: {repo}")
    if user_name or user_email:
        print(f"Git user: {user_name or 'default'} <{user_email or 'default'}>")
    print("=" * 80)

    # Fetch open PRs
    try:
        prs = fetch_open_prs(repo)
    except Exception as e:
        print(f"Error fetching PRs for {repo}: {e}", file=sys.stderr)
        return

    if not prs:
        print(f"No open PRs found in {repo}")
        return

    print(f"Found {len(prs)} open PR(s)")

    # Find first PR with unresolved reviews
    for pr in prs:
        pr_number = pr.get("number")
        print(f"\nChecking PR #{pr_number}: {pr.get('title')}")

        try:
            pr_data = fetch_pr_details(repo, pr_number)
        except Exception as e:
            print(f"Error fetching PR details: {e}", file=sys.stderr)
            continue

        # Get branch name
        branch_name = pr_data.get("headRefName")
        if not branch_name:
            print(f"Could not find branch name for PR #{pr_number}, skipping")
            continue

        # Filter reviews not yet processed
        reviews = pr_data.get("reviews", [])
        unresolved_reviews = [
            r for r in reviews
            if r.get("id") and not is_processed(r["id"])
        ]

        if not unresolved_reviews:
            print(f"No unresolved reviews for PR #{pr_number}")
            continue

        print(f"Found {len(unresolved_reviews)} unresolved review(s) - processing this PR")

        # Prepare repository
        try:
            works_dir = prepare_repository(repo, branch_name, user_name, user_email)
        except Exception as e:
            print(f"Error preparing repository: {e}", file=sys.stderr)
            continue

        # Generate prompt and execute Claude
        prompt = generate_prompt(pr_number, pr_data.get("title", ""), unresolved_reviews)

        claude_cmd = [
            "claude",
            "--model",
            "claude-sonnet-4-6",
            "--dangerously-skip-permissions",
            "-p",
            prompt,
        ]

        if dry_run:
            print("\n[DRY RUN] Would execute:")
            print(f"  cwd: {works_dir}")
            print(f"  command: {shlex.join(claude_cmd)}")
        else:
            print("\nExecuting Claude...")
            try:
                subprocess.run(claude_cmd, cwd=str(works_dir), check=True)
                print("Claude execution completed")
                for review in unresolved_reviews:
                    mark_processed(review["id"], repo, pr_number)
            except subprocess.CalledProcessError as e:
                print(f"Error executing Claude: {e}", file=sys.stderr)

        # Process only the first PR with unresolved reviews
        return

    print(f"No unresolved reviews found in any PR for {repo}")


def main():
    parser = argparse.ArgumentParser(
        description="Auto Review Fixer - Automatically fix CodeRabbit reviews"
    )
    parser.add_argument(
        "repos",
        nargs="*",
        help="Target repositories (owner/repo format). If not provided, reads from repos.txt",
    )
    parser.add_argument(
        "-n",
        "--dry-run",
        action="store_true",
        help="Show claude command without executing",
    )
    parser.add_argument(
        "-f",
        "--file",
        default="repos.txt",
        help="Repository list file (default: repos.txt)",
    )
    parser.add_argument(
        "--reset",
        action="store_true",
        help="Reset processed reviews database",
    )

    args = parser.parse_args()

    load_dotenv()
    init_db()

    if args.reset:
        reset_all()
        print("Database reset complete")
        return

    # Get repositories
    if args.repos:
        # Convert CLI repos to dict format (no user config)
        repos = [{"repo": r, "user_name": None, "user_email": None} for r in args.repos]
    else:
        # Try repos.txt in parent directory if not found in current directory
        repos_file = Path(args.file)
        if not repos_file.exists():
            repos_file = Path("..") / args.file
        repos = load_repos_from_file(str(repos_file))

    if not repos:
        print("No repositories to process")
        sys.exit(1)

    print(f"Processing {len(repos)} repository(ies)")
    if args.dry_run:
        print("[DRY RUN MODE]")

    for repo_info in repos:
        try:
            process_repo(repo_info, dry_run=args.dry_run)
        except KeyboardInterrupt:
            print("\nInterrupted by user")
            sys.exit(0)
        except Exception as e:
            print(f"Error processing {repo_info['repo']}: {e}", file=sys.stderr)
            continue

    print("\nDone!")


if __name__ == "__main__":
    main()
