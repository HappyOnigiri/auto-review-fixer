"""設定ファイルの読み込みと検証を行うモジュール。

シングルモード設定: .refix.yaml（または REFIX_CONFIG_YAML）
バッチモード設定: .refix-batch.yaml（または REFIX_CONFIG_BATCH_YAML）
"""

import copy
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

import yaml

from errors import ConfigError
from state_manager import ensure_valid_state_timezone
from subprocess_helpers import SubprocessError, run_command
from type_defs import AppConfig, RepositoryEntry

# --- デフォルト設定 ---
DEFAULT_CONFIG: AppConfig = {
    "user_name": None,
    "user_email": None,
    "setup": None,
    "models": {
        "summarize": "haiku",
        "fix": "sonnet",
    },
    "ci_log_max_lines": 120,
    "write_result_to_comment": True,
    "auto_merge": False,
    "enabled_pr_labels": [
        "running",
        "done",
        "merged",
        "auto_merge_requested",
        "ci_pending",
    ],
    "coderabbit_auto_resume": False,
    "coderabbit_auto_resume_triggers": {
        "rate_limit": True,
        "draft_detected": True,
    },
    "coderabbit_auto_resume_max_per_run": 1,
    "coderabbit_require_review": True,
    "coderabbit_block_while_processing": True,
    "coderabbit_ignore_nitpick": False,
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
    "target_authors": [],
    "auto_merge_authors": [],
    "triggers": {},
    "repositories": [],
}

# --- 許可キー定義 ---

# すべてのモードで共通の operational settings（git identity / setup / repo 名を除く）
_BASE_OPERATIONAL_KEYS = {
    "models",
    "ci_log_max_lines",
    "write_result_to_comment",
    "auto_merge",
    "enabled_pr_labels",
    "coderabbit_auto_resume",
    "coderabbit_auto_resume_triggers",
    "coderabbit_auto_resume_max_per_run",
    "coderabbit_require_review",
    "coderabbit_block_while_processing",
    "coderabbit_ignore_nitpick",
    "process_draft_prs",
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
    "target_authors",
    "auto_merge_authors",
    "triggers",
}

# シングルモード設定（.refix.yaml）で許可されるキー
# include_fork_repositories はバッチモード専用のため含まない
SINGLE_MODE_ALLOWED_KEYS = _BASE_OPERATIONAL_KEYS | {"user_name", "user_email", "setup"}

# バッチモード設定（.refix-batch.yaml）のトップレベルキー
BATCH_TOP_LEVEL_KEYS = {"global", "repositories"}

# バッチモードの global セクションで許可されるキー
BATCH_GLOBAL_KEYS = _BASE_OPERATIONAL_KEYS | {
    "user_name",
    "user_email",
    "include_fork_repositories",
}

# バッチモードの repositories[] エントリで許可されるキー
BATCH_REPOSITORY_KEYS = BATCH_GLOBAL_KEYS | {"repo", "setup"}

ALLOWED_MERGE_METHODS = ("auto", "merge", "squash", "rebase")
ALLOWED_BASE_UPDATE_METHODS = ("merge", "rebase")
ALLOWED_MODEL_KEYS = {"summarize", "fix"}
ALLOWED_CODERABBIT_AUTO_RESUME_TRIGGER_KEYS = {"rate_limit", "draft_detected"}
# triggers セクション: 現在は issue_comment のみ。将来他のイベントタイプに拡張予定。
ALLOWED_TRIGGERS_KEYS = {"issue_comment"}
ALLOWED_ISSUE_COMMENT_TRIGGER_KEYS = {"authors"}
VALID_SETUP_WHEN_VALUES = {"always", "clone_only"}

# --- PR ラベルキー定義（config 用） ---
PR_LABEL_KEYS = ("running", "done", "merged", "auto_merge_requested", "ci_pending")


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
    "coderabbit_require_review": FieldSpec(bool),
    "coderabbit_block_while_processing": FieldSpec(bool),
    "coderabbit_ignore_nitpick": FieldSpec(bool),
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
    config_section: dict[str, Any],  # dict-any: ok
    allowed_keys: set[str],
    section: str,
) -> None:
    unknown_keys = sorted(set(config_section.keys()) - allowed_keys)
    if unknown_keys:
        keys_str = ", ".join(f"'{k}'" for k in unknown_keys)
        raise ConfigError(f"Unknown config key(s) in {section}: {keys_str}")


def _validate_setup_section(setup_raw: Any, context: str = "'setup'") -> dict:
    """setup セクションを検証して正規化した dict を返す。ConfigError を送出する。"""
    if not isinstance(setup_raw, dict):
        raise ConfigError(f"{context} must be a mapping/object.")

    when = setup_raw.get("when", "always")
    if when not in VALID_SETUP_WHEN_VALUES:
        raise ConfigError(
            f"{context}.when の値が不正です: {when!r}"
            f" (有効な値: {sorted(VALID_SETUP_WHEN_VALUES)})"
        )

    commands = setup_raw.get("commands")
    if commands is None:
        return {"when": when, "commands": []}

    if not isinstance(commands, list):
        raise ConfigError(f"{context}.commands must be a list.")

    result: list[dict] = []
    for i, entry in enumerate(commands):
        if not isinstance(entry, dict):
            raise ConfigError(f"{context}.commands[{i}] must be a mapping/object.")
        run_str = entry.get("run")
        if not isinstance(run_str, str) or not run_str.strip():
            raise ConfigError(
                f"{context}.commands[{i}] には非空の run フィールドが必要です"
            )
        normalized: dict = {"run": run_str}
        name = entry.get("name")
        if name is not None:
            if not isinstance(name, str):
                raise ConfigError(
                    f"{context}.commands[{i}].name は文字列でなければなりません"
                )
            normalized["name"] = name
        result.append(normalized)

    return {"when": when, "commands": result}


def _validate_operational_settings(
    parsed: dict[str, Any],  # dict-any: ok
    config: AppConfig,
) -> None:
    """operational settings を parsed から読み込み config に反映する。

    config に既存の値がある場合（グローバル設定）、指定されたキーで上書きする。
    config が空の場合（リポジトリエントリ）、指定されたキーのみ設定する。
    """
    models = parsed.get("models")
    if models is not None:
        if not isinstance(models, dict):
            raise ConfigError("models must be a mapping/object.")
        _reject_unknown_config_keys(models, ALLOWED_MODEL_KEYS, section="'models'")

        existing_models = config.get("models") or {}
        validated_models = dict(existing_models)

        summarize_model = models.get("summarize")
        if summarize_model is not None:
            if not isinstance(summarize_model, str) or not summarize_model.strip():
                raise ConfigError("models.summarize must be a non-empty string.")
            validated_models["summarize"] = summarize_model.strip()

        fix_model = models.get("fix")
        if fix_model is not None:
            if not isinstance(fix_model, str) or not fix_model.strip():
                raise ConfigError("models.fix must be a non-empty string.")
            validated_models["fix"] = fix_model.strip()

        config["models"] = validated_models

    for _key, _spec in _SCALAR_FIELDS.items():
        _raw = parsed.get(_key)
        if _raw is not None:
            config[_key] = _validate_scalar_field(_key, _raw, _spec)

    coderabbit_auto_resume_triggers = parsed.get("coderabbit_auto_resume_triggers")
    if coderabbit_auto_resume_triggers is not None:
        if not isinstance(coderabbit_auto_resume_triggers, dict):
            raise ConfigError(
                "coderabbit_auto_resume_triggers must be a mapping/object."
            )
        _reject_unknown_config_keys(
            coderabbit_auto_resume_triggers,
            ALLOWED_CODERABBIT_AUTO_RESUME_TRIGGER_KEYS,
            section="'coderabbit_auto_resume_triggers'",
        )
        existing_triggers = config.get("coderabbit_auto_resume_triggers") or {}
        normalized_triggers = dict(existing_triggers)
        for trigger_key in ALLOWED_CODERABBIT_AUTO_RESUME_TRIGGER_KEYS:
            if trigger_key not in coderabbit_auto_resume_triggers:
                continue
            trigger_value = coderabbit_auto_resume_triggers[trigger_key]
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

    for _list_key in (
        "exclude_authors",
        "exclude_labels",
        "target_authors",
        "auto_merge_authors",
    ):
        _list_value = parsed.get(_list_key)
        if _list_value is not None:
            if not isinstance(_list_value, list):
                raise ConfigError(f"{_list_key} must be a list.")
            _normalized: list[str] = []
            for _idx, _item in enumerate(_list_value):
                if not isinstance(_item, str):
                    raise ConfigError(
                        f"{_list_key}[{_idx}] must be a non-empty string."
                    )
                _item = _item.strip()
                if not _item:
                    raise ConfigError(
                        f"{_list_key}[{_idx}] must be a non-empty string."
                    )
                _normalized.append(_item)
            config[_list_key] = _normalized

    triggers = parsed.get("triggers")
    if triggers is not None:
        if not isinstance(triggers, dict):
            raise ConfigError("triggers must be a mapping/object.")
        _reject_unknown_config_keys(
            triggers, ALLOWED_TRIGGERS_KEYS, section="'triggers'"
        )
        normalized_triggers_cfg = {}
        issue_comment_trigger = triggers.get("issue_comment")
        if issue_comment_trigger is not None:
            if not isinstance(issue_comment_trigger, dict):
                raise ConfigError("triggers.issue_comment must be a mapping/object.")
            _reject_unknown_config_keys(
                issue_comment_trigger,
                ALLOWED_ISSUE_COMMENT_TRIGGER_KEYS,
                section="'triggers.issue_comment'",
            )
            authors = issue_comment_trigger.get("authors")
            if authors is not None:
                if not isinstance(authors, list):
                    raise ConfigError("triggers.issue_comment.authors must be a list.")
                normalized_authors: list[str] = []
                for idx, item in enumerate(authors):
                    if not isinstance(item, str):
                        raise ConfigError(
                            f"triggers.issue_comment.authors[{idx}] must be a non-empty string."
                        )
                    item = item.strip()
                    if not item:
                        raise ConfigError(
                            f"triggers.issue_comment.authors[{idx}] must be a non-empty string."
                        )
                    normalized_authors.append(item)
                normalized_triggers_cfg["issue_comment"] = {
                    "authors": normalized_authors
                }
            else:
                normalized_triggers_cfg["issue_comment"] = {}
        config["triggers"] = normalized_triggers_cfg


def _make_default_config() -> AppConfig:
    """DEFAULT_CONFIG をベースとした新しい設定 dict を返す。"""
    return {
        "user_name": None,
        "user_email": None,
        "setup": None,
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
        "coderabbit_require_review": DEFAULT_CONFIG["coderabbit_require_review"],
        "coderabbit_block_while_processing": DEFAULT_CONFIG[
            "coderabbit_block_while_processing"
        ],
        "coderabbit_ignore_nitpick": DEFAULT_CONFIG["coderabbit_ignore_nitpick"],
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
        "target_authors": [],
        "auto_merge_authors": [],
        "triggers": {},
        "repositories": [],
    }


def normalize_auto_resume_state(
    runtime_config: AppConfig,
    default_config: AppConfig,
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
    runtime_config: AppConfig,
    default_config: AppConfig,
) -> dict[str, bool]:
    """CodeRabbit 自動再トリガの理由別設定を取得する。"""
    default_triggers = default_config["coderabbit_auto_resume_triggers"]
    normalized = {
        key: bool(default_triggers.get(key, False)) for key in default_triggers
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
    runtime_config: AppConfig,
    default_config: AppConfig,
) -> bool:
    """process_draft_prs フラグを取得する。"""
    return bool(
        runtime_config.get("process_draft_prs", default_config["process_draft_prs"])
    )


def get_enabled_pr_label_keys(
    runtime_config: AppConfig,
    default_config: AppConfig,
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


def load_single_config(filepath: str | None) -> AppConfig:
    """シングルモード（single-PR / action）用の設定ロード。

    filepath が None またはファイル不在ならデフォルトを返す。
    repositories フィールドは含まない（呼び出し側が --repo 引数から注入する）。
    """
    if not filepath or not Path(filepath).exists():
        return _make_default_config()

    try:
        config_text = Path(filepath).read_text(encoding="utf-8")
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

    _reject_unknown_config_keys(parsed, SINGLE_MODE_ALLOWED_KEYS, section="top level")

    config = _make_default_config()
    _validate_operational_settings(parsed, config)

    user_name = parsed.get("user_name")
    if user_name is not None:
        if not isinstance(user_name, str):
            raise ConfigError("user_name must be a string when specified.")
        config["user_name"] = user_name.strip() or None

    user_email = parsed.get("user_email")
    if user_email is not None:
        if not isinstance(user_email, str):
            raise ConfigError("user_email must be a string when specified.")
        config["user_email"] = user_email.strip() or None

    setup_raw = parsed.get("setup")
    if setup_raw is not None:
        config["setup"] = _validate_setup_section(setup_raw)

    config["repositories"] = []
    return config


def load_config(filepath: str) -> AppConfig:
    """バッチモード設定ファイル（.refix-batch.yaml）を読み込み、検証する。"""
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

    _reject_unknown_config_keys(parsed, BATCH_TOP_LEVEL_KEYS, section="top level")

    config = _make_default_config()

    # global セクションの処理
    global_section = parsed.get("global")
    if global_section is not None:
        if not isinstance(global_section, dict):
            raise ConfigError("'global' must be a mapping/object.")
        _reject_unknown_config_keys(
            global_section, BATCH_GLOBAL_KEYS, section="'global'"
        )
        _validate_operational_settings(global_section, config)

        user_name = global_section.get("user_name")
        if user_name is not None:
            if not isinstance(user_name, str):
                raise ConfigError("global.user_name must be a string when specified.")
            config["user_name"] = user_name.strip() or None

        user_email = global_section.get("user_email")
        if user_email is not None:
            if not isinstance(user_email, str):
                raise ConfigError("global.user_email must be a string when specified.")
            config["user_email"] = user_email.strip() or None

    # repositories セクションの処理
    repositories = parsed.get("repositories")
    if not isinstance(repositories, list) or not repositories:
        raise ConfigError("repositories is required and must be a non-empty list.")

    normalized_repositories: list[dict] = []
    for index, item in enumerate(repositories):
        if not isinstance(item, dict):
            raise ConfigError(f"repositories[{index}] must be a mapping/object.")
        _reject_unknown_config_keys(
            item, BATCH_REPOSITORY_KEYS, section=f"'repositories[{index}]'"
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

        # per-repo operational overrides
        repo_entry: dict = {"repo": repo_slug}
        repo_config: AppConfig = {}
        _validate_operational_settings(item, repo_config)
        repo_entry.update(repo_config)

        user_name = item.get("user_name")
        if user_name is not None:
            if not isinstance(user_name, str):
                raise ConfigError(
                    f"repositories[{index}].user_name must be a string when specified."
                )
            repo_entry["user_name"] = user_name.strip() or None

        user_email = item.get("user_email")
        if user_email is not None:
            if not isinstance(user_email, str):
                raise ConfigError(
                    f"repositories[{index}].user_email must be a string when specified."
                )
            repo_entry["user_email"] = user_email.strip() or None

        setup_raw = item.get("setup")
        if setup_raw is not None:
            repo_entry["setup"] = _validate_setup_section(
                setup_raw, context=f"'repositories[{index}].setup'"
            )

        normalized_repositories.append(repo_entry)

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


def merge_repo_config(
    global_config: AppConfig,
    repo_entry: RepositoryEntry | dict[str, Any],  # dict-any: ok
) -> AppConfig:
    """グローバル設定とリポジトリエントリをマージした設定を返す。

    マージ優先順: リポジトリ設定 → グローバル設定 → デフォルト
    dict 値（models, coderabbit_auto_resume_triggers, triggers 等）はサブキーレベルでマージ。
    スカラー/リスト値はそのまま置換。
    """
    result = copy.deepcopy(global_config)
    for key, repo_value in repo_entry.items():
        if key == "repo":
            continue
        global_value = result.get(key)
        if isinstance(repo_value, dict) and isinstance(global_value, dict):
            # サブキーレベルのマージ: repo のサブキーが global のサブキーを上書き
            merged_dict = dict(global_value)
            merged_dict.update(repo_value)
            result[key] = merged_dict
        else:
            result[key] = repo_value
    return result


def expand_repositories(
    repos: list[RepositoryEntry],
    include_fork_repositories: bool = True,
) -> list[RepositoryEntry]:
    """ワイルドカード（例: owner/*）を含むリポジトリ定義を gh cli で展開する。"""
    expanded: list[RepositoryEntry] = []
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
                    expanded.append(
                        cast(RepositoryEntry, {**repo_info, "repo": resolved_name})
                    )
        else:
            expanded.append(repo_info)
    return expanded
