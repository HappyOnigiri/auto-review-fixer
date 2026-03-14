"""設定ファイル（.refix.yaml）の読み込みと検証を行うモジュール。"""

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from errors import ConfigError
from state_manager import ensure_valid_state_timezone
from subprocess_helpers import SubprocessError, run_command

# --- デフォルト設定 ---
DEFAULT_CONFIG: dict[str, Any] = {
    "models": {
        "summarize": "haiku",
        "fix": "sonnet",
    },
    "ci_log_max_lines": 120,
    "write_result_to_comment": True,
    "auto_merge": False,
    "enabled_pr_labels": ["running", "done", "merged", "auto_merge_requested"],
    "coderabbit_auto_resume": False,
    "coderabbit_auto_resume_triggers": {
        "rate_limit": True,
        "draft_detected": True,
    },
    "coderabbit_auto_resume_max_per_run": 1,
    "process_draft_prs": False,
    "include_fork_repositories": True,
    "state_comment_timezone": "JST",
    "merge_method": "auto",
    "base_update_method": "merge",
    "max_modified_prs_per_run": 0,
    "max_committed_prs_per_run": 2,
    "max_claude_prs_per_run": 0,
    "ci_empty_as_success": True,
    "ci_empty_grace_minutes": 5,
    "exclude_authors": [],
    "exclude_labels": [],
    "repositories": [],
}

# --- 許可キー定義 ---
ALLOWED_CONFIG_TOP_LEVEL_KEYS = {
    "models",
    "ci_log_max_lines",
    "write_result_to_comment",
    "auto_merge",
    "enabled_pr_labels",
    "coderabbit_auto_resume",
    "coderabbit_auto_resume_triggers",
    "coderabbit_auto_resume_max_per_run",
    "process_draft_prs",
    "include_fork_repositories",
    "state_comment_timezone",
    "merge_method",
    "base_update_method",
    "max_modified_prs_per_run",
    "max_committed_prs_per_run",
    "max_claude_prs_per_run",
    "ci_empty_as_success",
    "ci_empty_grace_minutes",
    "exclude_authors",
    "exclude_labels",
    "repositories",
}
ALLOWED_MERGE_METHODS = ("auto", "merge", "squash", "rebase")
ALLOWED_BASE_UPDATE_METHODS = ("merge", "rebase")
ALLOWED_MODEL_KEYS = {"summarize", "fix"}
ALLOWED_REPOSITORY_KEYS = {"repo", "user_name", "user_email"}
ALLOWED_CODERABBIT_AUTO_RESUME_TRIGGER_KEYS = {"rate_limit", "draft_detected"}

# --- PR ラベルキー定義（config 用） ---
PR_LABEL_KEYS = ("running", "done", "merged", "auto_merge_requested")


@dataclass
class FieldSpec:
    """スカラー設定フィールドの検証仕様。"""

    type_: type  # bool または int
    min_value: int | None = None
    clamp: bool = False  # True の場合、下限未満を拒否せず min_value にクランプ
    reject_bool: bool = False  # int フィールドで bool 値を拒否する


_SCALAR_FIELDS: dict[str, FieldSpec] = {
    "write_result_to_comment": FieldSpec(bool),
    "auto_merge": FieldSpec(bool),
    "coderabbit_auto_resume": FieldSpec(bool),
    "process_draft_prs": FieldSpec(bool),
    "include_fork_repositories": FieldSpec(bool),
    "ci_empty_as_success": FieldSpec(bool),
    "ci_log_max_lines": FieldSpec(int, min_value=20, clamp=True),
    "coderabbit_auto_resume_max_per_run": FieldSpec(int, min_value=1, reject_bool=True),
    "max_modified_prs_per_run": FieldSpec(int, min_value=0, reject_bool=True),
    "max_committed_prs_per_run": FieldSpec(int, min_value=0, reject_bool=True),
    "max_claude_prs_per_run": FieldSpec(int, min_value=0, reject_bool=True),
}


def _validate_scalar_field(key: str, value: Any, spec: FieldSpec) -> Any:
    """スカラーフィールドを FieldSpec に従って検証・変換する。"""
    if spec.type_ is bool:
        if not isinstance(value, bool):
            raise ConfigError(f"{key} must be a boolean.")
        return value
    # int field
    if isinstance(value, float):
        raise ConfigError(f"{key} must be an integer.")
    if isinstance(value, bool):
        min_str = f">= {spec.min_value}" if spec.min_value is not None else ""
        raise ConfigError(
            f"{key} must be a non-negative integer{(' ' + min_str) if min_str else ''}."
        )
    try:
        int_value = int(value)
    except (TypeError, ValueError) as exc:
        raise ConfigError(f"{key} must be an integer.") from exc
    if spec.min_value is not None:
        if spec.clamp:
            int_value = max(spec.min_value, int_value)
        elif int_value < spec.min_value:
            raise ConfigError(f"{key} must be an integer >= {spec.min_value}.")
    return int_value


def _reject_unknown_config_keys(
    config_section: dict[str, Any], allowed_keys: set[str], section: str
) -> None:
    unknown_keys = sorted(set(config_section.keys()) - allowed_keys)
    if unknown_keys:
        keys_str = ", ".join(f"'{k}'" for k in unknown_keys)
        raise ConfigError(f"Unknown config key(s) in {section}: {keys_str}")


def normalize_auto_resume_state(
    runtime_config: dict[str, Any],
    default_config: dict[str, Any],
    auto_resume_run_state: dict[str, int] | None = None,
) -> dict[str, int]:
    """CodeRabbit の auto-resume 状態を正規化する。"""
    raw_max_per_run = runtime_config.get(
        "coderabbit_auto_resume_max_per_run",
        default_config["coderabbit_auto_resume_max_per_run"],
    )
    if (
        isinstance(raw_max_per_run, int)
        and not isinstance(raw_max_per_run, bool)
        and raw_max_per_run >= 1
    ):
        max_per_run = raw_max_per_run
    else:
        max_per_run = default_config["coderabbit_auto_resume_max_per_run"]

    if auto_resume_run_state is None:
        auto_resume_run_state = {"posted": 0, "max_per_run": max_per_run}
    else:
        auto_resume_run_state["posted"] = int(auto_resume_run_state.get("posted", 0))
        auto_resume_run_state["max_per_run"] = max_per_run

    return auto_resume_run_state


def get_coderabbit_auto_resume_triggers(
    runtime_config: dict[str, Any],
    default_config: dict[str, Any],
) -> dict[str, bool]:
    """CodeRabbit 自動再トリガの理由別設定を取得する。"""
    default_triggers = default_config["coderabbit_auto_resume_triggers"]
    normalized = {
        key: bool(default_triggers.get(key, False))
        for key in default_triggers
    }
    configured = runtime_config.get("coderabbit_auto_resume_triggers")
    if not isinstance(configured, dict):
        return normalized
    for key in normalized:
        value = configured.get(key)
        if isinstance(value, bool):
            normalized[key] = value
    return normalized


def get_process_draft_prs(
    runtime_config: dict[str, Any],
    default_config: dict[str, Any],
) -> bool:
    """process_draft_prs フラグを取得する。"""
    return bool(
        runtime_config.get("process_draft_prs", default_config["process_draft_prs"])
    )


def get_enabled_pr_label_keys(
    runtime_config: dict[str, Any],
    default_config: dict[str, Any],
) -> set[str]:
    """有効な PR ラベルキーの集合を取得する。"""
    configured_labels = runtime_config.get(
        "enabled_pr_labels", default_config["enabled_pr_labels"]
    )
    if not isinstance(configured_labels, list):
        configured_labels = default_config["enabled_pr_labels"]
    return {
        label_key
        for label_key in configured_labels
        if isinstance(label_key, str) and label_key in PR_LABEL_KEYS
    }


def load_config(filepath: str) -> dict[str, Any]:
    """YAML 設定ファイルを読み込み、検証する。"""
    try:
        config_text = Path(filepath).read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise ConfigError(f"config file not found: {filepath}") from exc
    except OSError as exc:
        raise ConfigError(f"failed to read config file '{filepath}': {exc}") from exc

    try:
        parsed = yaml.safe_load(config_text)
    except yaml.YAMLError as e:
        raise ConfigError(f"failed to parse YAML config '{filepath}': {e}") from e

    if parsed is None:
        parsed = {}
    if not isinstance(parsed, dict):
        raise ConfigError("config root must be a mapping/object.")

    _reject_unknown_config_keys(
        parsed, ALLOWED_CONFIG_TOP_LEVEL_KEYS, section="top level"
    )

    config: dict[str, Any] = {
        "models": dict(DEFAULT_CONFIG["models"]),
        "ci_log_max_lines": DEFAULT_CONFIG["ci_log_max_lines"],
        "write_result_to_comment": DEFAULT_CONFIG["write_result_to_comment"],
        "auto_merge": DEFAULT_CONFIG["auto_merge"],
        "enabled_pr_labels": list(DEFAULT_CONFIG["enabled_pr_labels"]),
        "coderabbit_auto_resume": DEFAULT_CONFIG["coderabbit_auto_resume"],
        "coderabbit_auto_resume_triggers": dict(
            DEFAULT_CONFIG["coderabbit_auto_resume_triggers"]
        ),
        "coderabbit_auto_resume_max_per_run": DEFAULT_CONFIG[
            "coderabbit_auto_resume_max_per_run"
        ],
        "process_draft_prs": DEFAULT_CONFIG["process_draft_prs"],
        "include_fork_repositories": DEFAULT_CONFIG["include_fork_repositories"],
        "state_comment_timezone": DEFAULT_CONFIG["state_comment_timezone"],
        "merge_method": DEFAULT_CONFIG["merge_method"],
        "base_update_method": DEFAULT_CONFIG["base_update_method"],
        "max_modified_prs_per_run": DEFAULT_CONFIG["max_modified_prs_per_run"],
        "max_committed_prs_per_run": DEFAULT_CONFIG["max_committed_prs_per_run"],
        "max_claude_prs_per_run": DEFAULT_CONFIG["max_claude_prs_per_run"],
        "ci_empty_as_success": DEFAULT_CONFIG["ci_empty_as_success"],
        "ci_empty_grace_minutes": DEFAULT_CONFIG["ci_empty_grace_minutes"],
        "exclude_authors": [],
        "exclude_labels": [],
        "repositories": [],
    }

    models = parsed.get("models")
    if models is not None:
        if not isinstance(models, dict):
            raise ConfigError("models must be a mapping/object.")
        _reject_unknown_config_keys(models, ALLOWED_MODEL_KEYS, section="'models'")

        summarize_model = models.get("summarize")
        if summarize_model is not None:
            if not isinstance(summarize_model, str) or not summarize_model.strip():
                raise ConfigError("models.summarize must be a non-empty string.")
            config["models"]["summarize"] = summarize_model.strip()

        fix_model = models.get("fix")
        if fix_model is not None:
            if not isinstance(fix_model, str) or not fix_model.strip():
                raise ConfigError("models.fix must be a non-empty string.")
            config["models"]["fix"] = fix_model.strip()

    for _key, _spec in _SCALAR_FIELDS.items():
        _raw = parsed.get(_key)
        if _raw is not None:
            config[_key] = _validate_scalar_field(_key, _raw, _spec)

    coderabbit_auto_resume_triggers = parsed.get("coderabbit_auto_resume_triggers")
    if coderabbit_auto_resume_triggers is not None:
        if not isinstance(coderabbit_auto_resume_triggers, dict):
            raise ConfigError("coderabbit_auto_resume_triggers must be a mapping/object.")
        _reject_unknown_config_keys(
            coderabbit_auto_resume_triggers,
            ALLOWED_CODERABBIT_AUTO_RESUME_TRIGGER_KEYS,
            section="'coderabbit_auto_resume_triggers'",
        )
        normalized_triggers = dict(DEFAULT_CONFIG["coderabbit_auto_resume_triggers"])
        for trigger_key in ALLOWED_CODERABBIT_AUTO_RESUME_TRIGGER_KEYS:
            trigger_value = coderabbit_auto_resume_triggers.get(trigger_key)
            if trigger_value is None:
                continue
            if not isinstance(trigger_value, bool):
                raise ConfigError(
                    f"coderabbit_auto_resume_triggers.{trigger_key} must be a boolean."
                )
            normalized_triggers[trigger_key] = trigger_value
        config["coderabbit_auto_resume_triggers"] = normalized_triggers

    enabled_pr_labels = parsed.get("enabled_pr_labels")
    if enabled_pr_labels is not None:
        if not isinstance(enabled_pr_labels, list):
            raise ConfigError("enabled_pr_labels must be a list.")
        normalized_enabled_labels: list[str] = []
        seen_enabled_labels: set[str] = set()
        allowed_label_keys = ", ".join(sorted(PR_LABEL_KEYS))
        for index, label_key in enumerate(enabled_pr_labels):
            if not isinstance(label_key, str) or not label_key.strip():
                raise ConfigError(
                    f"enabled_pr_labels[{index}] must be a non-empty string."
                )
            normalized_label_key = label_key.strip()
            if normalized_label_key not in PR_LABEL_KEYS:
                raise ConfigError(
                    f"enabled_pr_labels[{index}] must be one of: {allowed_label_keys}."
                )
            if normalized_label_key in seen_enabled_labels:
                continue
            seen_enabled_labels.add(normalized_label_key)
            normalized_enabled_labels.append(normalized_label_key)
        if "merged" in seen_enabled_labels and not (
            seen_enabled_labels & {"running", "done", "auto_merge_requested"}
        ):
            allowed_merge_sub_keys = ", ".join(
                sorted({"running", "done", "auto_merge_requested"})
            )
            raise ConfigError(
                f'enabled_pr_labels includes "merged" but none of: {allowed_merge_sub_keys}. '
                f'At least one of these must be included alongside "merged".'
            )
        config["enabled_pr_labels"] = normalized_enabled_labels

    state_comment_timezone = parsed.get("state_comment_timezone")
    if state_comment_timezone is not None:
        if (
            not isinstance(state_comment_timezone, str)
            or not state_comment_timezone.strip()
        ):
            raise ConfigError("state_comment_timezone must be a non-empty string.")
        timezone_name = state_comment_timezone.strip()
        try:
            ensure_valid_state_timezone(timezone_name)
        except ValueError as exc:
            raise ConfigError(
                "state_comment_timezone must be a valid IANA timezone (e.g. Asia/Tokyo) or JST."
            ) from exc
        config["state_comment_timezone"] = timezone_name

    merge_method = parsed.get("merge_method")
    if merge_method is not None:
        if not isinstance(merge_method, str) or not merge_method.strip():
            raise ConfigError("merge_method must be a non-empty string.")
        normalized_merge_method = merge_method.strip()
        if normalized_merge_method not in ALLOWED_MERGE_METHODS:
            allowed_str = ", ".join(f'"{m}"' for m in ALLOWED_MERGE_METHODS)
            raise ConfigError(f"merge_method must be one of: {allowed_str}.")
        config["merge_method"] = normalized_merge_method

    base_update_method = parsed.get("base_update_method")
    if base_update_method is not None:
        if not isinstance(base_update_method, str) or not base_update_method.strip():
            raise ConfigError("base_update_method must be a non-empty string.")
        normalized_base_update_method = base_update_method.strip()
        if normalized_base_update_method not in ALLOWED_BASE_UPDATE_METHODS:
            allowed_str = ", ".join(f'"{m}"' for m in ALLOWED_BASE_UPDATE_METHODS)
            raise ConfigError(f"base_update_method must be one of: {allowed_str}.")
        config["base_update_method"] = normalized_base_update_method

    ci_empty_grace_minutes = parsed.get("ci_empty_grace_minutes")
    if ci_empty_grace_minutes is not None:
        if isinstance(ci_empty_grace_minutes, bool) or isinstance(
            ci_empty_grace_minutes, float
        ):
            raise ConfigError("ci_empty_grace_minutes must be a non-negative integer.")
        if isinstance(ci_empty_grace_minutes, int):
            grace_int = ci_empty_grace_minutes
        elif (
            isinstance(ci_empty_grace_minutes, str) and ci_empty_grace_minutes.isdigit()
        ):
            grace_int = int(ci_empty_grace_minutes)
        else:
            raise ConfigError("ci_empty_grace_minutes must be a non-negative integer.")
        if grace_int < 0:
            raise ConfigError("ci_empty_grace_minutes must be a non-negative integer.")
        config["ci_empty_grace_minutes"] = grace_int

    for _list_key in ("exclude_authors", "exclude_labels"):
        _list_value = parsed.get(_list_key)
        if _list_value is not None:
            if not isinstance(_list_value, list):
                raise ConfigError(f"{_list_key} must be a list.")
            _normalized: list[str] = []
            for _idx, _item in enumerate(_list_value):
                if not isinstance(_item, str) or not _item:
                    raise ConfigError(
                        f"{_list_key}[{_idx}] must be a non-empty string."
                    )
                _normalized.append(_item)
            config[_list_key] = _normalized

    repositories = parsed.get("repositories")
    if not isinstance(repositories, list) or not repositories:
        raise ConfigError("repositories is required and must be a non-empty list.")

    normalized_repositories: list[dict[str, str | None]] = []
    for index, item in enumerate(repositories):
        if not isinstance(item, dict):
            raise ConfigError(f"repositories[{index}] must be a mapping/object.")
        _reject_unknown_config_keys(
            item, ALLOWED_REPOSITORY_KEYS, section=f"'repositories[{index}]'"
        )

        repo_name = item.get("repo")
        if not isinstance(repo_name, str) or not repo_name.strip():
            raise ConfigError(
                f"repositories[{index}].repo is required and must be a non-empty string."
            )
        repo_slug = repo_name.strip()
        if (
            "/" not in repo_slug
            or repo_slug.count("/") != 1
            or repo_slug.startswith("/")
            or repo_slug.endswith("/")
        ):
            raise ConfigError(
                f"repositories[{index}].repo must be in 'owner/repo' format."
            )

        user_name = item.get("user_name")
        if user_name is not None and not isinstance(user_name, str):
            raise ConfigError(
                f"repositories[{index}].user_name must be a string when specified."
            )

        user_email = item.get("user_email")
        if user_email is not None and not isinstance(user_email, str):
            raise ConfigError(
                f"repositories[{index}].user_email must be a string when specified."
            )

        normalized_repositories.append(
            {
                "repo": repo_name.strip(),
                "user_name": user_name.strip()
                if isinstance(user_name, str) and user_name.strip()
                else None,
                "user_email": user_email.strip()
                if isinstance(user_email, str) and user_email.strip()
                else None,
            }
        )

    seen_repos: set[str] = set()
    for entry in normalized_repositories:
        slug = entry["repo"]
        if not slug or slug.endswith("/*"):
            continue
        normalized_slug = slug.strip().casefold()
        if normalized_slug in seen_repos:
            raise ConfigError(f"Duplicate repository '{slug}' in repositories.")
        seen_repos.add(normalized_slug)

    config["repositories"] = normalized_repositories
    return config


def expand_repositories(
    repos: list[dict[str, Any]],
    include_fork_repositories: bool = True,
) -> list[dict[str, Any]]:
    """ワイルドカード（例: owner/*）を含むリポジトリ定義を gh cli で展開する。"""
    expanded: list[dict[str, Any]] = []
    for repo_info in repos:
        repo_name = repo_info["repo"]
        if repo_name.endswith("/*"):
            owner = repo_name[:-2]
            print(f"Expanding wildcard repository: {repo_name}")
            cmd = [
                "gh",
                "repo",
                "list",
                owner,
            ]
            if not include_fork_repositories:
                cmd.extend(["--source"])
            cmd.extend(
                [
                    "--json",
                    "nameWithOwner",
                    "--jq",
                    ".[].nameWithOwner",
                    "--limit",
                    "1000",
                ]
            )
            try:
                result = run_command(cmd, check=False)
            except SubprocessError as exc:
                raise ConfigError(f"failed to expand {repo_name}: {exc}") from exc
            if result.returncode != 0:
                raise ConfigError(
                    f"failed to expand {repo_name}: {(result.stderr or '').strip()}"
                )

            lines = result.stdout.strip().splitlines()
            if not lines:
                raise ConfigError(f"no repositories found for {repo_name}")

            for line in lines:
                resolved_name = line.strip()
                if resolved_name:
                    new_info = dict(repo_info)
                    new_info["repo"] = resolved_name
                    expanded.append(new_info)
        else:
            expanded.append(repo_info)
    return expanded
