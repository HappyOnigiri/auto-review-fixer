#!/usr/bin/env python3
"""
Auto Review Fixer - Automatically fix CodeRabbit reviews.
Fetches open PRs, gets unresolved reviews, and runs Claude to fix them.
"""

import argparse
import os
import shlex
import subprocess
import sys
from pathlib import Path
from typing import Any

# --list-commands は DB 等の依存なしで表示するため、先に処理して exit
if "--list-commands" in sys.argv or "--list-commands-en" in sys.argv:
    parser = argparse.ArgumentParser()
    parser.add_argument("--list-commands", action="store_true")
    parser.add_argument("--list-commands-en", action="store_true")
    args, _ = parser.parse_known_args()
    if args.list_commands_en:
        print("""Auto Review Fixer - Makefile targets:

  make run
    Summarize unresolved reviews with Haiku, fix and push with Sonnet,
    and record results in DB. Shows debug-level logs (full prompts, summaries).

  make run-silent
    Same as run, but minimize log output (for CI).

  make dry-run
    Show commands and dummy summaries without calling Haiku or Sonnet.

  make run-summarize-only
    Run Haiku summarization only and print results.
    Does not run Sonnet or update DB. (for verification)

  make reset
    Reset the processed reviews DB (delete all records).

  make setup
    Install dependencies and create .env template.""")
        sys.exit(0)
    if args.list_commands:
        print("""Auto Review Fixer - Makefile targets:

  make run
    未処理レビューを Haiku で要約し Sonnet で修正・push して DB に記録。
    デバッグレベルのログ（要約全文・プロンプト全文）を表示

  make run-silent
    本番実行と同じだが、ログを最小限に抑える（CI 向け）

  make dry-run
    Haiku/Sonnet を呼ばず、実行コマンドとダミー要約を表示

  make run-summarize-only
    Haiku による要約のみ実行して結果を表示（Sonnet 実行・DB 更新なし）

  make reset
    処理済みレビューの DB をリセット（全件削除）

  make setup
    依存パッケージをインストールし .env テンプレートを作成""")
        sys.exit(0)

from dotenv import load_dotenv

from github_pr_fetcher import fetch_open_prs
from pr_reviewer import fetch_pr_details, fetch_pr_review_comments, fetch_review_threads, resolve_review_thread
from review_db import count_processed_for_pr, init_db, is_processed, mark_processed, reset_all
from ci_log import _log_endgroup, _log_group
from summarizer import summarize_reviews

# REST API returns "coderabbitai[bot]", GraphQL returns "coderabbitai"
CODERABBIT_BOT_LOGIN_PREFIX = "coderabbitai"


def load_repos_from_env() -> list[dict[str, str]]:
    """Load repository list from REPOS environment variable.

    Format: owner/repo:user.name:user.email,owner2/repo2:name2:email2
    """
    repos_env = os.environ.get("REPOS", "").strip()
    if not repos_env:
        return []
    repos = []
    for entry in repos_env.split(","):
        entry = entry.strip()
        if not entry:
            continue
        parts = entry.split(":")
        repo = parts[0]
        segments = repo.split("/")
        if len(segments) != 2 or not segments[0] or not segments[1]:
            print(f"Warning: skipping invalid repo entry '{repo}' (expected owner/name)", file=sys.stderr)
            continue
        user_name = parts[1] if len(parts) > 1 else None
        user_email = parts[2] if len(parts) > 2 else None
        repos.append({"repo": repo, "user_name": user_name, "user_email": user_email})
    return repos


def load_repos_from_file(filepath: str) -> list[dict[str, str]]:
    """Load repository list from file with optional git user config.

    Format: owner/repo:user.name:user.email
    Example: HappyOnigiri/ComfyUI-Meld:Claude HappyOnigiri:253838257+NodeMeld@users.noreply.github.com
    """
    repos = []
    try:
        with open(filepath, "r", encoding="utf-8") as f:
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
    repo: str, branch_name: str, user_name: str | None = None, user_email: str | None = None
) -> Path:
    """Clone or update repository and checkout to the target branch.

    Optionally sets local git config for user.name and user.email.
    """
    repo_name = repo.split("/")[1]
    works_dir = Path("../works") / repo_name
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


def generate_prompt(
    pr_number: int,
    title: str,
    unresolved_reviews: list[dict[str, Any]],
    unresolved_comments: list[dict[str, Any]],
    summaries: dict[str, str],
    round_number: int = 1,
) -> str:
    """Generate prompt for Claude from unresolved PR reviews and inline comments."""
    sections = []

    review_bodies = []
    for r in unresolved_reviews:
        text = summaries.get(r["id"]) or r.get("body", "")
        if text:
            review_bodies.append(text)
    if review_bodies:
        sections.append("--- レビュー内容 ---\n" + "\n\n".join(review_bodies))

    if unresolved_comments:
        comment_parts = []
        for c in unresolved_comments:
            rid = f"discussion_r{c['id']}"
            path = c.get("path", "")
            line = c.get("line") or c.get("original_line", "")
            location = f"{path}:{line}" if path and line else path
            body = summaries.get(rid) or c.get("body", "")
            comment_parts.append(f"{location}\n{body}" if location else body)
        sections.append("--- インラインコメント ---\n" + "\n\n".join(comment_parts))

    if round_number >= 2:
        instruction = (
            f'以下は PR #{pr_number} "{title}" に対する CodeRabbit のレビューコメントです（第{round_number}ラウンド）。\n'
            "このPRはすでに一度修正済みです。クリティカルな問題（バグ、セキュリティ、ビルドエラー）のみ修正してください。\n"
            "軽微なスタイル提案や好みの問題はスキップして構いません。\n"
            "修正後は git commit して push してください。"
        )
    else:
        instruction = (
            f'以下は PR #{pr_number} "{title}" に対する CodeRabbit のレビューコメントです。\n'
            "指摘事項を確認し、必要であればコードを修正してください。\n"
            "修正後は git commit して push してください。"
        )

    prompt = f"""{instruction}

{"\n".join(sections)}
"""
    return prompt


def process_repo(repo_info: dict[str, str], dry_run: bool = False, silent: bool = False, summarize_only: bool = False) -> None:
    """Process a single repository for PR fixes.

    Args:
        repo_info: Dict with 'repo', 'user_name', 'user_email' keys
        dry_run: If True, show command without executing
        silent: If True, minimize log output (default: False = show debug-level logs)
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

        # Filter reviews not yet processed (bot reviews only)
        reviews = pr_data.get("reviews", [])
        unresolved_reviews = []
        for r in reviews:
            if not r.get("id"):
                continue
            if not r.get("author", {}).get("login", "").startswith(CODERABBIT_BOT_LOGIN_PREFIX):
                continue
            processed = is_processed(r["id"])
            if not silent:
                print(f"  [DB] review {r['id']}: {'processed' if processed else 'NOT processed'}")
            if not processed:
                unresolved_reviews.append(r)

        # Filter inline review comments (discussion_r<id>) not yet processed
        # Also skip threads already resolved on GitHub
        try:
            review_comments = fetch_pr_review_comments(repo, pr_number)
        except Exception as e:
            print(f"Warning: could not fetch inline comments: {e}", file=sys.stderr)
            review_comments = []
        thread_map = fetch_review_threads(repo, pr_number)
        unresolved_thread_ids = set(thread_map.keys())
        unresolved_comments = []
        for c in review_comments:
            if not c.get("id"):
                continue
            if not c.get("user", {}).get("login", "").startswith(CODERABBIT_BOT_LOGIN_PREFIX):
                continue
            rid = f"discussion_r{c['id']}"
            processed = is_processed(rid)
            in_thread = c["id"] in unresolved_thread_ids
            if not silent:
                print(f"  [DB] comment {rid}: {'processed' if processed else 'NOT processed'}, thread_unresolved={in_thread}")
            if not processed and in_thread:
                unresolved_comments.append(c)

        if not unresolved_reviews and not unresolved_comments:
            print(f"No unresolved reviews for PR #{pr_number}")
            continue

        # Determine round number for this PR (1-based)
        past_count = count_processed_for_pr(repo, pr_number)
        round_number = past_count + 1
        if round_number >= 2:
            print(f"Round {round_number} for PR #{pr_number}: will skip minor suggestions")

        total = len(unresolved_reviews) + len(unresolved_comments)
        print(f"Found {total} unresolved review(s)/comment(s) - processing this PR")

        for i, r in enumerate(unresolved_reviews, 1):
            preview = (r.get("body") or "")[:100].replace("\n", " ")
            print(f"  Review {i}: {preview}")
        for i, c in enumerate(unresolved_comments, 1):
            path = c.get("path", "")
            line = c.get("line") or c.get("original_line", "")
            location = f"{path}:{line}" if path and line else path
            preview = (c.get("body") or "")[:100].replace("\n", " ")
            print(f"  Comment {i} [{location}]: {preview}")

        # Prepare repository
        try:
            _log_group("Git repository setup")
            works_dir = prepare_repository(repo, branch_name, user_name, user_email)
            _log_endgroup()
        except Exception as e:
            _log_endgroup()
            print(f"Error preparing repository: {e}", file=sys.stderr)
            continue

        # Summarize reviews with Haiku before passing to Sonnet
        print()
        if dry_run:
            # Show what the summarization command would look like
            print("\n[DRY RUN] Would summarize with Haiku:")
            print("  command: claude --model haiku -p 'Read the file <temp>.md ...'")
            print(f"  items: {len(unresolved_reviews)} review(s), {len(unresolved_comments)} inline comment(s)")
            # Build dummy summaries without calling claude
            summaries: dict[str, str] = {}
            for i, r in enumerate(unresolved_reviews, 1):
                if r.get("id"):
                    summaries[r["id"]] = f"（レビューコメント {i} の要約）"
            for i, c in enumerate(unresolved_comments, 1):
                if c.get("id"):
                    rid = f"discussion_r{c['id']}"
                    path = c.get("path", "")
                    label = f"{path} " if path else ""
                    summaries[rid] = f"（インラインコメント {i} {label}の要約）"
        else:
            summaries = summarize_reviews(unresolved_reviews, unresolved_comments)
            if (not silent or summarize_only) and summaries:
                print("\n[Haiku summaries]")
                for sid, summary in summaries.items():
                    print(f"  {sid}:\n    {summary}")

        if summarize_only:
            print("\nSummarize-only mode: stopping here (no Sonnet execution, no DB update)")
            return

        # Generate prompt and execute Claude
        prompt = generate_prompt(pr_number, pr_data.get("title", ""), unresolved_reviews, unresolved_comments, summaries, round_number=round_number)

        if not silent:
            print("\n[DEBUG] Full prompt for Sonnet:")
            print("-" * 60)
            print(prompt)
            print("-" * 60)

        # Write prompt to a file to avoid Windows command-line length limits
        prompt_file = works_dir / "_review_prompt.md"
        prompt_file.write_text(prompt, encoding="utf-8")

        claude_cmd = [
            "claude",
            "--model",
            "sonnet",
            "--dangerously-skip-permissions",
            "-p",
            "Read the file _review_prompt.md for instructions and follow them. Delete the file when done.",
        ]

        if dry_run:
            print("\n[DRY RUN] Would execute:")
            print(f"  cwd: {works_dir}")
            print(f"  command: {shlex.join(claude_cmd)}")
            print(f"  prompt written to: {prompt_file}")
            prompt_file.unlink(missing_ok=True)
        else:
            print("\nExecuting Claude...")
            _log_group("Sonnet command details")
            print(f"  cwd: {works_dir}")
            print(f"  command: {shlex.join(claude_cmd)}")
            print(f"  prompt file: {prompt_file}")
            if not silent:
                print("-" * 60)
                print(prompt)
                print("-" * 60)
            _log_endgroup()
            try:
                # Record HEAD before Claude runs to detect new commits afterward
                head_before = subprocess.run(
                    ["git", "rev-parse", "HEAD"],
                    cwd=str(works_dir),
                    capture_output=True,
                    text=True,
                ).stdout.strip()

                claude_env = os.environ.copy()
                claude_env.pop("CLAUDECODE", None)
                process = subprocess.Popen(
                    claude_cmd,
                    cwd=str(works_dir),
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    env=claude_env,
                )
                stdout, stderr = process.communicate()
                if process.returncode != 0:
                    raise subprocess.CalledProcessError(
                        process.returncode, claude_cmd,
                        output=stdout, stderr=stderr,
                    )
                print("Claude execution completed")

                # Show commits added by Claude
                new_commits = subprocess.run(
                    ["git", "log", "--oneline", f"{head_before}..HEAD"],
                    cwd=str(works_dir),
                    capture_output=True,
                    text=True,
                ).stdout.strip()
                print()
                if new_commits:
                    print("New commit(s) added:")
                    for line in new_commits.splitlines():
                        print(f"  {line}")
                else:
                    print("No new commits added")
                # Claude の終了コード 0 を「セッション完了」として全件 mark_processed する。
                # 「修正不要」と判断したコメントも既読化することで再処理ループを防ぐ。
                # Claude が実際に修正・push したかどうかはコード上で検証しない。
                # これは意図した仕様: Claude 自身がコメントへの対応要否を判断する。
                # exit code 非ゼロの場合は mark_processed を呼ばないため、
                # エラー時の再試行は保証される。
                for review in unresolved_reviews:
                    mark_processed(review["id"], repo, pr_number,
                                   body=review.get("body", ""),
                                   summary=summaries.get(review["id"], ""))
                for comment in unresolved_comments:
                    rid = f"discussion_r{comment['id']}"
                    mark_processed(rid, repo, pr_number,
                                   body=comment.get("body", ""),
                                   summary=summaries.get(rid, ""))
                # Resolve inline comment threads on GitHub
                if unresolved_comments:
                    resolved = 0
                    for comment in unresolved_comments:
                        thread_id = thread_map.get(comment["id"])
                        if thread_id and resolve_review_thread(thread_id):
                            resolved += 1
                    print(f"Resolved {resolved}/{len(unresolved_comments)} review thread(s)")
            except subprocess.CalledProcessError as e:
                print(f"Error executing Claude: {e}", file=sys.stderr)
                if e.output:
                    print(f"  stdout: {e.output.decode(errors='replace').strip()}", file=sys.stderr)
                if e.stderr:
                    print(f"  stderr: {e.stderr.decode(errors='replace').strip()}", file=sys.stderr)
            finally:
                prompt_file.unlink(missing_ok=True)

        # Process only the first PR with unresolved reviews
        return

    print(f"No unresolved reviews found in any PR for {repo}")


def main():
    # CI環境ではPythonのstdout/stderrがフルバッファモードになり、
    # subprocessの直接fd書き込みと順序が逆転する。
    # ラインバッファモードにして出力順序を保証する。
    sys.stdout.reconfigure(line_buffering=True)
    sys.stderr.reconfigure(line_buffering=True)

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
    parser.add_argument(
        "--list-commands",
        action="store_true",
        help="List available make commands in Japanese and exit",
    )
    parser.add_argument(
        "--list-commands-en",
        action="store_true",
        help="List available make commands in English and exit",
    )
    parser.add_argument(
        "--silent",
        action="store_true",
        help="Minimize log output (default: show debug-level logs)",
    )
    parser.add_argument(
        "--summarize-only",
        action="store_true",
        help="Run Haiku summarization only, print results, then exit without running Sonnet or updating DB",
    )

    args = parser.parse_args()

    load_dotenv()
    init_db()

    if args.reset:
        reset_all()
        print("Database reset complete")
        return

    # Get repositories: CLI args > REPOS env var > repos.txt file
    if args.repos:
        repos = [{"repo": r, "user_name": None, "user_email": None} for r in args.repos]
    else:
        repos = load_repos_from_env()
        if repos:
            print(f"Loaded {len(repos)} repository(ies) from REPOS environment variable")
        else:
            # Try repos.txt in current directory, then parent directory
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
    if args.summarize_only:
        print("[SUMMARIZE ONLY MODE]")

    for repo_info in repos:
        try:
            process_repo(repo_info, dry_run=args.dry_run, silent=args.silent, summarize_only=args.summarize_only)
        except KeyboardInterrupt:
            print("\nInterrupted by user")
            sys.exit(0)
        except Exception as e:
            print(f"Error processing {repo_info['repo']}: {e}", file=sys.stderr)
            continue

    print("\nDone!")


if __name__ == "__main__":
    main()
