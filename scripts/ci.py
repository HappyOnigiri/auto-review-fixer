#!/usr/bin/env python3
"""Repository CI runner for Python-focused checks."""

from __future__ import annotations

import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed

COLOR_GREEN = "\033[92m"
COLOR_RED = "\033[91m"
COLOR_RESET = "\033[0m"

# Each task is a tuple of (name, command[, cwd]).
TASKS: list[tuple[str, str] | tuple[str, str, str]] = [
    ("Python-Lint-ruff-format", f"{sys.executable} -m ruff format src tests scripts"),
    (
        "Python-Lint-ruff-check",
        f"{sys.executable} -m ruff check src tests scripts --fix",
    ),
    ("Fix-Newlines", f"{sys.executable} scripts/fix_newlines.py"),
    ("Check-Non-ASCII", f"{sys.executable} scripts/check_non_ascii.py"),
    ("Check-Dict-Any", f"{sys.executable} scripts/check_dict_any.py"),
    ("Python-Lint-mypy", f"{sys.executable} -m mypy src tests scripts"),
    ("Python-Lint-pyright", "npx --yes pyright"),
    ("Python-Tests", f"{sys.executable} -m pytest -q --ignore=works"),
]


MUTATING_TASK_NAMES = {
    "Python-Lint-ruff-format",
    "Python-Lint-ruff-check",
    "Fix-Newlines",
}


def _unpack_task(
    task: tuple[str, str] | tuple[str, str, str],
) -> tuple[str, str, str | None]:
    if len(task) == 3:
        return task[0], task[1], task[2]
    return task[0], task[1], None


def _log_filename(name: str) -> str:
    safe_name = name.replace(" ", "_").replace("/", "_")
    return os.path.join(".logs", f"{safe_name}.log")


def run_task(name: str, command: str, cwd: str | None = None) -> tuple[bool, str, str]:
    try:
        resolved_cwd = os.path.realpath(cwd) if cwd else None
        if sys.platform == "win32":
            process = subprocess.Popen(
                command,
                shell=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                text=True,
                encoding="utf-8",
                errors="replace",
                cwd=resolved_cwd,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
        else:
            process = subprocess.Popen(
                command,
                shell=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                text=True,
                encoding="utf-8",
                errors="replace",
                cwd=resolved_cwd,
            )
        output, _ = process.communicate()
        return process.returncode == 0, name, output
    except Exception as exc:  # pragma: no cover - exceptional path
        return False, name, str(exc)


def _run_and_record(
    name: str,
    command: str,
    cwd: str | None,
    results: dict[str, tuple[bool, str]],
) -> None:
    success, _, output = run_task(name, command, cwd)
    results[name] = (success, output)
    with open(_log_filename(name), "w", encoding="utf-8") as handle:
        handle.write(output)


def main() -> None:
    reconfigure = getattr(sys.stdout, "reconfigure", None)
    if callable(reconfigure):
        reconfigure(errors="replace")
    if sys.platform == "win32":
        os.system("")

    os.makedirs(".logs", exist_ok=True)
    results: dict[str, tuple[bool, str]] = {}

    mutating_tasks: list[tuple[str, str, str | None]] = []
    non_mutating_tasks: list[tuple[str, str, str | None]] = []
    for task in TASKS:
        name, cmd, cwd = _unpack_task(task)
        if name in MUTATING_TASK_NAMES:
            mutating_tasks.append((name, cmd, cwd))
        else:
            non_mutating_tasks.append((name, cmd, cwd))

    for name, cmd, cwd in mutating_tasks:
        _run_and_record(name, cmd, cwd, results)

    with ThreadPoolExecutor(max_workers=4) as executor:
        futures = [
            executor.submit(run_task, name, cmd, cwd)
            for name, cmd, cwd in non_mutating_tasks
        ]
        for future in as_completed(futures):
            success, name, output = future.result()
            results[name] = (success, output)
            with open(_log_filename(name), "w", encoding="utf-8") as handle:
                handle.write(output)

    sys.stdout.write("\033[0m")
    sys.stdout.flush()

    failed_tasks: list[tuple[str, str]] = []
    print("\n" + "-" * 60, flush=True)
    for task in TASKS:
        name = task[0]
        success, output = results[name]
        if success:
            status_text = f"{COLOR_GREEN}SUCCESS{COLOR_RESET}"
            symbol = "[+]"
        else:
            status_text = f"{COLOR_RED}FAILED{COLOR_RESET}"
            symbol = "[-]"
            failed_tasks.append((name, output))
        print(f"  {symbol} {name:<35} {status_text}", flush=True)
    print("-" * 60, flush=True)

    if failed_tasks:
        print(
            f"\n{COLOR_RED}CI FAILED ({len(failed_tasks)} tasks failed){COLOR_RESET}",
            flush=True,
        )
        print("=" * 80, flush=True)
        for name, output in failed_tasks:
            print(f"\n--- Detailed log for {name} ---", flush=True)
            print(output, flush=True)
        sys.exit(1)

    print(f"\n{COLOR_GREEN}CI SUCCESSFUL{COLOR_RESET}", flush=True)
    sys.exit(0)


if __name__ == "__main__":
    main()
