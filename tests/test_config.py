"""Unit tests for config loading and repository expansion."""

import pytest

import auto_fixer
import config
from errors import ConfigError
from type_defs import RepositoryEntry


class TestLoadConfig:
    def test_valid_config_with_all_keys(self, tmp_path):
        config_file = tmp_path / "config.yaml"
        config_file.write_text(
            """
models:
  summarize: claude-haiku
  fix: claude-sonnet
ci_log_max_lines: 250
write_result_to_comment: false
auto_merge: true
coderabbit_auto_resume: true
coderabbit_auto_resume_triggers:
  rate_limit: false
  draft_detected: true
coderabbit_auto_resume_max_per_run: 3
include_fork_repositories: false
state_comment_timezone: UTC
repositories:
  - repo: owner/repo1
    user_name: Bot User
    user_email: bot@example.com
  - repo: owner/repo2
""".strip()
        )

        cfg = config.load_config(str(config_file))
        assert cfg == {
            "models": {
                "summarize": "claude-haiku",
                "fix": "claude-sonnet",
            },
            "ci_log_max_lines": 250,
            "write_result_to_comment": False,
            "auto_merge": True,
            "enabled_pr_labels": [
                "running",
                "done",
                "merged",
                "auto_merge_requested",
                "ci_pending",
            ],
            "coderabbit_auto_resume": True,
            "coderabbit_auto_resume_triggers": {
                "rate_limit": False,
                "draft_detected": True,
            },
            "coderabbit_auto_resume_max_per_run": 3,
            "process_draft_prs": False,
            "include_fork_repositories": False,
            "state_comment_timezone": "UTC",
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
            "repositories": [
                {
                    "repo": "owner/repo1",
                    "user_name": "Bot User",
                    "user_email": "bot@example.com",
                },
                {
                    "repo": "owner/repo2",
                    "user_name": None,
                    "user_email": None,
                },
            ],
        }

    def test_optional_keys_use_defaults(self, tmp_path):
        config_file = tmp_path / "config.yaml"
        config_file.write_text(
            """
repositories:
  - repo: owner/repo1
""".strip()
        )

        cfg = config.load_config(str(config_file))
        assert cfg["models"]["summarize"] == "haiku"
        assert cfg["models"]["fix"] == "sonnet"
        assert cfg["ci_log_max_lines"] == 120
        assert cfg["write_result_to_comment"] is True
        assert cfg["auto_merge"] is False
        assert cfg["enabled_pr_labels"] == [
            "running",
            "done",
            "merged",
            "auto_merge_requested",
            "ci_pending",
        ]
        assert cfg["coderabbit_auto_resume"] is False
        assert cfg["coderabbit_auto_resume_triggers"] == {
            "rate_limit": True,
            "draft_detected": True,
        }
        assert cfg["coderabbit_auto_resume_max_per_run"] == 1
        assert cfg["process_draft_prs"] is False
        assert cfg["include_fork_repositories"] is True
        assert cfg["state_comment_timezone"] == "JST"
        assert cfg["merge_method"] == "auto"
        assert cfg["base_update_method"] == "merge"
        assert cfg["max_modified_prs_per_run"] == 0
        assert cfg["max_committed_prs_per_run"] == 2
        assert cfg["max_claude_prs_per_run"] == 0
        assert cfg["repositories"] == [
            {"repo": "owner/repo1", "user_name": None, "user_email": None}
        ]

    def test_auto_merge_requires_boolean(self, tmp_path):
        config_file = tmp_path / "config.yaml"
        config_file.write_text(
            """
auto_merge: "true"
repositories:
  - repo: owner/repo1
""".strip()
        )
        with pytest.raises(ConfigError):
            config.load_config(str(config_file))

    def test_enabled_pr_labels_can_be_subset(self, tmp_path):
        config_file = tmp_path / "config.yaml"
        config_file.write_text(
            """
enabled_pr_labels:
  - running
  - auto_merge_requested
  - running
repositories:
  - repo: owner/repo1
""".strip()
        )
        cfg = config.load_config(str(config_file))
        assert cfg["enabled_pr_labels"] == ["running", "auto_merge_requested"]

    def test_enabled_pr_labels_can_be_empty(self, tmp_path):
        config_file = tmp_path / "config.yaml"
        config_file.write_text(
            """
enabled_pr_labels: []
repositories:
  - repo: owner/repo1
""".strip()
        )
        cfg = config.load_config(str(config_file))
        assert cfg["enabled_pr_labels"] == []

    def test_enabled_pr_labels_must_be_known_values(self, tmp_path):
        config_file = tmp_path / "config.yaml"
        config_file.write_text(
            """
enabled_pr_labels:
  - running
  - unknown
repositories:
  - repo: owner/repo1
""".strip()
        )
        with pytest.raises(ConfigError):
            config.load_config(str(config_file))

    def test_write_result_to_comment_requires_boolean(self, tmp_path):
        config_file = tmp_path / "config.yaml"
        config_file.write_text(
            """
write_result_to_comment: "true"
repositories:
  - repo: owner/repo1
""".strip()
        )
        with pytest.raises(ConfigError):
            config.load_config(str(config_file))

    def test_process_draft_prs_can_be_enabled(self, tmp_path):
        config_file = tmp_path / "config.yaml"
        config_file.write_text(
            """
process_draft_prs: true
repositories:
  - repo: owner/repo1
""".strip()
        )
        cfg = config.load_config(str(config_file))
        assert cfg["process_draft_prs"] is True

    def test_coderabbit_auto_resume_requires_boolean(self, tmp_path):
        config_file = tmp_path / "config.yaml"
        config_file.write_text(
            """
coderabbit_auto_resume: "true"
repositories:
  - repo: owner/repo1
""".strip()
        )
        with pytest.raises(ConfigError):
            config.load_config(str(config_file))

    def test_coderabbit_auto_resume_triggers_accept_partial_override(self, tmp_path):
        config_file = tmp_path / "config.yaml"
        config_file.write_text(
            """
coderabbit_auto_resume_triggers:
  draft_detected: false
repositories:
  - repo: owner/repo1
""".strip()
        )
        cfg = config.load_config(str(config_file))
        assert cfg["coderabbit_auto_resume_triggers"] == {
            "rate_limit": True,
            "draft_detected": False,
        }

    def test_coderabbit_auto_resume_triggers_requires_mapping(self, tmp_path):
        config_file = tmp_path / "config.yaml"
        config_file.write_text(
            """
coderabbit_auto_resume_triggers: true
repositories:
  - repo: owner/repo1
""".strip()
        )
        with pytest.raises(ConfigError):
            config.load_config(str(config_file))

    def test_coderabbit_auto_resume_triggers_rejects_unknown_key(self, tmp_path):
        config_file = tmp_path / "config.yaml"
        config_file.write_text(
            """
coderabbit_auto_resume_triggers:
  unknown: true
repositories:
  - repo: owner/repo1
""".strip()
        )
        with pytest.raises(ConfigError):
            config.load_config(str(config_file))

    def test_coderabbit_auto_resume_triggers_rejects_non_boolean_value(self, tmp_path):
        config_file = tmp_path / "config.yaml"
        config_file.write_text(
            """
coderabbit_auto_resume_triggers:
  rate_limit: "true"
repositories:
  - repo: owner/repo1
""".strip()
        )
        with pytest.raises(ConfigError):
            config.load_config(str(config_file))

    def test_process_draft_prs_type_error_exits(self, tmp_path):
        config_file = tmp_path / "config.yaml"
        config_file.write_text(
            """
process_draft_prs: "true"
repositories:
  - repo: owner/repo1
""".strip()
        )
        with pytest.raises(ConfigError):
            config.load_config(str(config_file))

    def test_include_fork_repositories_requires_boolean(self, tmp_path):
        config_file = tmp_path / "config.yaml"
        config_file.write_text(
            """
include_fork_repositories: "false"
repositories:
  - repo: owner/repo1
""".strip()
        )
        with pytest.raises(ConfigError):
            config.load_config(str(config_file))

    def test_state_comment_timezone_requires_non_empty_string(self, tmp_path):
        config_file = tmp_path / "config.yaml"
        config_file.write_text(
            """
state_comment_timezone: ""
repositories:
  - repo: owner/repo1
""".strip()
        )
        with pytest.raises(ConfigError):
            config.load_config(str(config_file))

    def test_coderabbit_auto_resume_max_per_run_requires_positive_integer(
        self, tmp_path
    ):
        config_file = tmp_path / "config.yaml"
        config_file.write_text(
            """
coderabbit_auto_resume_max_per_run: 0
repositories:
  - repo: owner/repo1
""".strip()
        )
        with pytest.raises(ConfigError):
            config.load_config(str(config_file))

    def test_state_comment_timezone_requires_valid_timezone(self, tmp_path):
        config_file = tmp_path / "config.yaml"
        config_file.write_text(
            """
state_comment_timezone: "Not/AZone"
repositories:
  - repo: owner/repo1
""".strip()
        )
        with pytest.raises(ConfigError):
            config.load_config(str(config_file))

    def test_coderabbit_auto_resume_max_per_run_rejects_boolean(self, tmp_path):
        config_file = tmp_path / "config.yaml"
        config_file.write_text(
            """
coderabbit_auto_resume_max_per_run: true
repositories:
  - repo: owner/repo1
""".strip()
        )
        with pytest.raises(ConfigError):
            config.load_config(str(config_file))

    def test_yaml_parse_error_exits(self, tmp_path):
        config_file = tmp_path / "config.yaml"
        config_file.write_text(
            """
repositories:
  - repo: owner/repo1
    user_name: bot
   user_email: invalid-indent
""".strip()
        )
        with pytest.raises(ConfigError):
            config.load_config(str(config_file))

    def test_missing_repositories_exits(self, tmp_path):
        config_file = tmp_path / "config.yaml"
        config_file.write_text(
            """
models:
  summarize: custom
""".strip()
        )
        with pytest.raises(ConfigError):
            config.load_config(str(config_file))

    def test_empty_repositories_exits(self, tmp_path):
        config_file = tmp_path / "config.yaml"
        config_file.write_text(
            """
repositories: []
""".strip()
        )
        with pytest.raises(ConfigError):
            config.load_config(str(config_file))

    def test_unknown_top_level_key_raises(self, tmp_path):
        config_file = tmp_path / "config.yaml"
        config_file.write_text(
            """
invalid_top: true
repositories:
  - repo: owner/repo1
""".strip()
        )
        with pytest.raises(ConfigError) as excinfo:
            config.load_config(str(config_file))
        assert "Unknown config key(s) in top level" in str(excinfo.value)
        assert "'invalid_top'" in str(excinfo.value)

    def test_unknown_model_key_raises(self, tmp_path):
        config_file = tmp_path / "config.yaml"
        config_file.write_text(
            """
models:
  summarize: custom-haiku
  invalid_model_key: 123
repositories:
  - repo: owner/repo1
""".strip()
        )
        with pytest.raises(ConfigError) as excinfo:
            config.load_config(str(config_file))
        assert "Unknown config key(s) in 'models'" in str(excinfo.value)
        assert "'invalid_model_key'" in str(excinfo.value)

    def test_unknown_repository_key_raises(self, tmp_path):
        config_file = tmp_path / "config.yaml"
        config_file.write_text(
            """
repositories:
  - repo: owner/repo1
    invalid_repo_key: ignored
""".strip()
        )
        with pytest.raises(ConfigError) as excinfo:
            config.load_config(str(config_file))
        assert "Unknown config key(s) in 'repositories[0]'" in str(excinfo.value)
        assert "'invalid_repo_key'" in str(excinfo.value)

    def test_duplicate_repository_raises(self, tmp_path):
        config_file = tmp_path / "config.yaml"
        config_file.write_text(
            """
repositories:
  - repo: owner/repo1
  - repo: owner/repo2
  - repo: owner/repo1
""".strip()
        )
        with pytest.raises(ConfigError) as excinfo:
            config.load_config(str(config_file))
        assert "Duplicate repository 'owner/repo1'" in str(excinfo.value)

    def test_wildcard_repos_not_checked_for_duplicates(self, tmp_path):
        config_file = tmp_path / "config.yaml"
        config_file.write_text(
            """
repositories:
  - repo: owner/*
  - repo: owner/*
""".strip()
        )
        # Wildcards are not checked for duplicates at load time; should not raise
        cfg = config.load_config(str(config_file))
        assert len(cfg["repositories"]) == 2


class TestExpandRepositories:
    def test_no_wildcard_returns_original(self):
        repos: list[RepositoryEntry] = [
            {"repo": "owner/repo1"},
            {"repo": "owner/repo2"},
        ]
        expanded = auto_fixer.expand_repositories(repos)
        assert expanded == repos

    def test_expand_wildcard(self, mocker, make_cmd_result):
        repos: list[RepositoryEntry] = [{"repo": "owner/*", "user_name": "bot"}]
        mock_run = mocker.patch(
            "config.run_command",
            return_value=make_cmd_result("owner/repo1\nowner/repo2\n"),
        )
        expanded = auto_fixer.expand_repositories(repos)

        assert len(expanded) == 2
        assert expanded[0] == {"repo": "owner/repo1", "user_name": "bot"}
        assert expanded[1] == {"repo": "owner/repo2", "user_name": "bot"}
        mock_run.assert_called_once_with(
            [
                "gh",
                "repo",
                "list",
                "owner",
                "--json",
                "nameWithOwner",
                "--jq",
                ".[].nameWithOwner",
                "--limit",
                "1000",
            ],
            check=False,
        )

    def test_expand_wildcard_fail_aborts(self, mocker, make_cmd_result):
        repos: list[RepositoryEntry] = [{"repo": "owner/*"}]
        mocker.patch(
            "config.run_command",
            return_value=make_cmd_result("", returncode=1, stderr="error"),
        )
        with pytest.raises(ConfigError) as excinfo:
            auto_fixer.expand_repositories(repos)

        assert "failed to expand owner/*" in str(excinfo.value)

    def test_expand_wildcard_empty_results_aborts(self, mocker, make_cmd_result):
        repos: list[RepositoryEntry] = [{"repo": "owner/*"}]
        mocker.patch("config.run_command", return_value=make_cmd_result(""))
        with pytest.raises(ConfigError) as excinfo:
            auto_fixer.expand_repositories(repos)

        assert "no repositories found for owner/*" in str(excinfo.value)

    def test_expand_wildcard_with_include_forks_off(self, mocker, make_cmd_result):
        repos: list[RepositoryEntry] = [{"repo": "owner/*"}]
        mock_run = mocker.patch(
            "config.run_command", return_value=make_cmd_result("owner/repo1\n")
        )
        expanded = auto_fixer.expand_repositories(
            repos, include_fork_repositories=False
        )

        assert expanded == [{"repo": "owner/repo1"}]
        mock_run.assert_called_once_with(
            [
                "gh",
                "repo",
                "list",
                "owner",
                "--source",
                "--json",
                "nameWithOwner",
                "--jq",
                ".[].nameWithOwner",
                "--limit",
                "1000",
            ],
            check=False,
        )


class TestPerRunLimitsConfig:
    """load_config のPR処理件数制限キーのバリデーションテスト。"""

    def test_limit_keys_accept_valid_integers(self, tmp_path):
        config_file = tmp_path / "config.yaml"
        config_file.write_text(
            """
max_modified_prs_per_run: 5
max_committed_prs_per_run: 3
max_claude_prs_per_run: 1
repositories:
  - repo: owner/repo1
""".strip()
        )
        cfg = config.load_config(str(config_file))
        assert cfg["max_modified_prs_per_run"] == 5
        assert cfg["max_committed_prs_per_run"] == 3
        assert cfg["max_claude_prs_per_run"] == 1

    def test_limit_keys_accept_zero(self, tmp_path):
        config_file = tmp_path / "config.yaml"
        config_file.write_text(
            """
max_modified_prs_per_run: 0
max_committed_prs_per_run: 0
max_claude_prs_per_run: 0
repositories:
  - repo: owner/repo1
""".strip()
        )
        cfg = config.load_config(str(config_file))
        assert cfg["max_modified_prs_per_run"] == 0
        assert cfg["max_committed_prs_per_run"] == 0
        assert cfg["max_claude_prs_per_run"] == 0

    @pytest.mark.parametrize(
        "key",
        [
            "max_modified_prs_per_run",
            "max_committed_prs_per_run",
            "max_claude_prs_per_run",
        ],
    )
    def test_limit_key_rejects_negative(self, tmp_path, key):
        config_file = tmp_path / "config.yaml"
        config_file.write_text(
            f"""
{key}: -1
repositories:
  - repo: owner/repo1
""".strip()
        )
        with pytest.raises(ConfigError):
            config.load_config(str(config_file))

    @pytest.mark.parametrize(
        "key",
        [
            "max_modified_prs_per_run",
            "max_committed_prs_per_run",
            "max_claude_prs_per_run",
        ],
    )
    def test_limit_key_rejects_string(self, tmp_path, key):
        config_file = tmp_path / "config.yaml"
        config_file.write_text(
            f"""
{key}: "abc"
repositories:
  - repo: owner/repo1
""".strip()
        )
        with pytest.raises(ConfigError):
            config.load_config(str(config_file))

    @pytest.mark.parametrize(
        "key",
        [
            "max_modified_prs_per_run",
            "max_committed_prs_per_run",
            "max_claude_prs_per_run",
        ],
    )
    def test_limit_key_rejects_boolean(self, tmp_path, key):
        config_file = tmp_path / "config.yaml"
        config_file.write_text(
            f"""
{key}: true
repositories:
  - repo: owner/repo1
""".strip()
        )
        with pytest.raises(ConfigError):
            config.load_config(str(config_file))


class TestExcludeFilters:
    def _base_yaml(self, extra: str = "") -> str:
        return f"""{extra}
repositories:
  - repo: owner/repo1
""".strip()

    def test_exclude_authors_valid(self, tmp_path):
        config_file = tmp_path / "config.yaml"
        config_file.write_text(
            self._base_yaml('exclude_authors: ["*[bot]", "some-user"]')
        )
        cfg = config.load_config(str(config_file))
        assert cfg["exclude_authors"] == ["*[bot]", "some-user"]

    def test_exclude_labels_valid(self, tmp_path):
        config_file = tmp_path / "config.yaml"
        config_file.write_text(
            self._base_yaml('exclude_labels: ["autorelease: *", "do-not-merge"]')
        )
        cfg = config.load_config(str(config_file))
        assert cfg["exclude_labels"] == ["autorelease: *", "do-not-merge"]

    def test_exclude_authors_defaults_to_empty(self, tmp_path):
        config_file = tmp_path / "config.yaml"
        config_file.write_text(self._base_yaml())
        cfg = config.load_config(str(config_file))
        assert cfg["exclude_authors"] == []

    def test_exclude_labels_defaults_to_empty(self, tmp_path):
        config_file = tmp_path / "config.yaml"
        config_file.write_text(self._base_yaml())
        cfg = config.load_config(str(config_file))
        assert cfg["exclude_labels"] == []

    @pytest.mark.parametrize(
        "key",
        ["exclude_authors", "exclude_labels", "target_authors", "auto_merge_authors"],
    )
    def test_invalid_type_string_raises(self, tmp_path, key):
        config_file = tmp_path / "config.yaml"
        config_file.write_text(self._base_yaml(f'{key}: "not-a-list"'))
        with pytest.raises(ConfigError):
            config.load_config(str(config_file))

    @pytest.mark.parametrize(
        "key",
        ["exclude_authors", "exclude_labels", "target_authors", "auto_merge_authors"],
    )
    def test_invalid_type_int_raises(self, tmp_path, key):
        config_file = tmp_path / "config.yaml"
        config_file.write_text(self._base_yaml(f"{key}: 123"))
        with pytest.raises(ConfigError):
            config.load_config(str(config_file))

    @pytest.mark.parametrize(
        "key",
        ["exclude_authors", "exclude_labels", "target_authors", "auto_merge_authors"],
    )
    def test_invalid_list_of_int_raises(self, tmp_path, key):
        config_file = tmp_path / "config.yaml"
        config_file.write_text(self._base_yaml(f"{key}: [1, 2, 3]"))
        with pytest.raises(ConfigError):
            config.load_config(str(config_file))


class TestMergeMethodConfig:
    def _base_yaml(self, extra: str = "") -> str:
        lines = ["repositories:", "  - repo: owner/repo1"]
        if extra:
            lines.insert(0, extra)
        return "\n".join(lines)

    @pytest.mark.parametrize("method", ["auto", "merge", "squash", "rebase"])
    def test_valid_merge_method_accepted(self, tmp_path, method):
        config_file = tmp_path / "config.yaml"
        config_file.write_text(self._base_yaml(f'merge_method: "{method}"'))
        cfg = config.load_config(str(config_file))
        assert cfg["merge_method"] == method

    def test_merge_method_defaults_to_auto(self, tmp_path):
        config_file = tmp_path / "config.yaml"
        config_file.write_text(self._base_yaml())
        cfg = config.load_config(str(config_file))
        assert cfg["merge_method"] == "auto"

    def test_invalid_merge_method_raises(self, tmp_path):
        config_file = tmp_path / "config.yaml"
        config_file.write_text(self._base_yaml('merge_method: "fast-forward"'))
        with pytest.raises(ConfigError, match="merge_method must be one of"):
            config.load_config(str(config_file))

    def test_non_string_merge_method_raises(self, tmp_path):
        config_file = tmp_path / "config.yaml"
        config_file.write_text(self._base_yaml("merge_method: 123"))
        with pytest.raises(
            ConfigError, match="merge_method must be a non-empty string"
        ):
            config.load_config(str(config_file))


class TestBaseUpdateMethodConfig:
    def _base_yaml(self, extra: str = "") -> str:
        lines = ["repositories:", "  - repo: owner/repo1"]
        if extra:
            lines.insert(0, extra)
        return "\n".join(lines)

    @pytest.mark.parametrize("method", ["merge", "rebase"])
    def test_valid_base_update_method_accepted(self, tmp_path, method):
        config_file = tmp_path / "config.yaml"
        config_file.write_text(self._base_yaml(f'base_update_method: "{method}"'))
        cfg = config.load_config(str(config_file))
        assert cfg["base_update_method"] == method

    def test_base_update_method_defaults_to_merge(self, tmp_path):
        config_file = tmp_path / "config.yaml"
        config_file.write_text(self._base_yaml())
        cfg = config.load_config(str(config_file))
        assert cfg["base_update_method"] == "merge"

    def test_invalid_base_update_method_raises(self, tmp_path):
        config_file = tmp_path / "config.yaml"
        config_file.write_text(self._base_yaml('base_update_method: "squash"'))
        with pytest.raises(ConfigError, match="base_update_method must be one of"):
            config.load_config(str(config_file))

    def test_non_string_base_update_method_raises(self, tmp_path):
        config_file = tmp_path / "config.yaml"
        config_file.write_text(self._base_yaml("base_update_method: true"))
        with pytest.raises(
            ConfigError, match="base_update_method must be a non-empty string"
        ):
            config.load_config(str(config_file))


class TestLoadConfigForAction:
    def test_returns_default_config_when_path_is_none(self):
        cfg = config.load_config_for_action(None)
        assert cfg["repositories"] == []
        assert cfg["models"]["summarize"] == "haiku"
        assert cfg["models"]["fix"] == "sonnet"
        assert "ci_pending" in cfg["enabled_pr_labels"]

    def test_returns_default_config_when_path_does_not_exist(self, tmp_path):
        cfg = config.load_config_for_action(str(tmp_path / "nonexistent.yaml"))
        assert cfg["repositories"] == []
        assert cfg["models"]["summarize"] == "haiku"

    def test_loads_settings_from_existing_file(self, tmp_path):
        config_file = tmp_path / ".refix.yaml"
        config_file.write_text(
            """
models:
  summarize: claude-haiku-custom
  fix: claude-sonnet-custom
auto_merge: true
""".strip()
        )
        cfg = config.load_config_for_action(str(config_file))
        assert cfg["repositories"] == []
        assert cfg["models"]["summarize"] == "claude-haiku-custom"
        assert cfg["models"]["fix"] == "claude-sonnet-custom"
        assert cfg["auto_merge"] is True

    def test_ignores_repositories_in_file(self, tmp_path):
        config_file = tmp_path / ".refix.yaml"
        config_file.write_text(
            """
repositories:
  - repo: owner/other-repo
""".strip()
        )
        cfg = config.load_config_for_action(str(config_file))
        # repositories should be cleared (caller injects --repo value)
        assert cfg["repositories"] == []

    def test_raises_on_invalid_yaml(self, tmp_path):
        config_file = tmp_path / ".refix.yaml"
        config_file.write_text("{bad: yaml: syntax:")
        with pytest.raises(ConfigError):
            config.load_config_for_action(str(config_file))

    def test_raises_on_invalid_field_value(self, tmp_path):
        config_file = tmp_path / ".refix.yaml"
        config_file.write_text("auto_merge: not-a-bool\n")
        with pytest.raises(ConfigError):
            config.load_config_for_action(str(config_file))
