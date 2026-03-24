"""Unit tests for config loading and repository expansion."""

import pytest

import auto_fixer
import config
from errors import ConfigError
from type_defs import RepositoryEntry


class TestLoadConfig:
    """バッチモード設定（global: + repositories:）のテスト。"""

    def test_valid_config_with_all_keys(self, tmp_path):
        config_file = tmp_path / "config.yaml"
        config_file.write_text(
            """
global:
  user_name: "Bot User"
  user_email: "bot@example.com"
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
  - repo: owner/repo2
""".strip()
        )

        cfg = config.load_config(str(config_file))
        assert cfg["user_name"] == "Bot User"
        assert cfg["user_email"] == "bot@example.com"
        assert cfg["models"] == {
            "summarize": "claude-haiku",
            "fix": "claude-sonnet",
        }
        assert cfg["ci_log_max_lines"] == 250
        assert cfg["write_result_to_comment"] is False
        assert cfg["auto_merge"] is True
        assert cfg["coderabbit_auto_resume"] is True
        assert cfg["coderabbit_auto_resume_triggers"] == {
            "rate_limit": False,
            "draft_detected": True,
        }
        assert cfg["coderabbit_auto_resume_max_per_run"] == 3
        assert cfg["include_fork_repositories"] is False
        assert cfg["state_comment_timezone"] == "UTC"
        assert cfg["enabled_pr_labels"] == [
            "running",
            "done",
            "merged",
            "auto_merge_requested",
            "ci_pending",
        ]
        assert cfg["repositories"] == [
            {"repo": "owner/repo1"},
            {"repo": "owner/repo2"},
        ]

    def test_optional_keys_use_defaults(self, tmp_path):
        config_file = tmp_path / "config.yaml"
        config_file.write_text(
            """
repositories:
  - repo: owner/repo1
""".strip()
        )

        cfg = config.load_config(str(config_file))
        assert cfg["user_name"] is None
        assert cfg["user_email"] is None
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
        assert cfg["repositories"] == [{"repo": "owner/repo1"}]

    def _batch_yaml(self, global_extra: str = "", repo: str = "owner/repo1") -> str:
        if global_extra:
            indented = "\n".join(
                "  " + line for line in global_extra.strip().split("\n")
            )
            return f"global:\n{indented}\nrepositories:\n  - repo: {repo}"
        return f"repositories:\n  - repo: {repo}"

    def test_auto_merge_requires_boolean(self, tmp_path):
        config_file = tmp_path / "config.yaml"
        config_file.write_text(self._batch_yaml('auto_merge: "true"'))
        with pytest.raises(ConfigError):
            config.load_config(str(config_file))

    def test_enabled_pr_labels_can_be_subset(self, tmp_path):
        config_file = tmp_path / "config.yaml"
        config_file.write_text(
            self._batch_yaml(
                "enabled_pr_labels:\n  - running\n  - auto_merge_requested\n  - running"
            )
        )
        cfg = config.load_config(str(config_file))
        assert cfg["enabled_pr_labels"] == ["running", "auto_merge_requested"]

    def test_enabled_pr_labels_can_be_empty(self, tmp_path):
        config_file = tmp_path / "config.yaml"
        config_file.write_text(self._batch_yaml("enabled_pr_labels: []"))
        cfg = config.load_config(str(config_file))
        assert cfg["enabled_pr_labels"] == []

    def test_enabled_pr_labels_must_be_known_values(self, tmp_path):
        config_file = tmp_path / "config.yaml"
        config_file.write_text(
            self._batch_yaml("enabled_pr_labels:\n  - running\n  - unknown")
        )
        with pytest.raises(ConfigError):
            config.load_config(str(config_file))

    def test_write_result_to_comment_requires_boolean(self, tmp_path):
        config_file = tmp_path / "config.yaml"
        config_file.write_text(self._batch_yaml('write_result_to_comment: "true"'))
        with pytest.raises(ConfigError):
            config.load_config(str(config_file))

    def test_process_draft_prs_can_be_enabled(self, tmp_path):
        config_file = tmp_path / "config.yaml"
        config_file.write_text(self._batch_yaml("process_draft_prs: true"))
        cfg = config.load_config(str(config_file))
        assert cfg["process_draft_prs"] is True

    def test_coderabbit_auto_resume_requires_boolean(self, tmp_path):
        config_file = tmp_path / "config.yaml"
        config_file.write_text(self._batch_yaml('coderabbit_auto_resume: "true"'))
        with pytest.raises(ConfigError):
            config.load_config(str(config_file))

    def test_coderabbit_auto_resume_triggers_accept_partial_override(self, tmp_path):
        config_file = tmp_path / "config.yaml"
        config_file.write_text(
            self._batch_yaml(
                "coderabbit_auto_resume_triggers:\n  draft_detected: false"
            )
        )
        cfg = config.load_config(str(config_file))
        assert cfg["coderabbit_auto_resume_triggers"] == {
            "rate_limit": True,
            "draft_detected": False,
        }

    def test_coderabbit_auto_resume_triggers_requires_mapping(self, tmp_path):
        config_file = tmp_path / "config.yaml"
        config_file.write_text(
            self._batch_yaml("coderabbit_auto_resume_triggers: true")
        )
        with pytest.raises(ConfigError):
            config.load_config(str(config_file))

    def test_coderabbit_auto_resume_triggers_rejects_unknown_key(self, tmp_path):
        config_file = tmp_path / "config.yaml"
        config_file.write_text(
            self._batch_yaml("coderabbit_auto_resume_triggers:\n  unknown: true")
        )
        with pytest.raises(ConfigError):
            config.load_config(str(config_file))

    def test_coderabbit_auto_resume_triggers_rejects_non_boolean_value(self, tmp_path):
        config_file = tmp_path / "config.yaml"
        config_file.write_text(
            self._batch_yaml('coderabbit_auto_resume_triggers:\n  rate_limit: "true"')
        )
        with pytest.raises(ConfigError):
            config.load_config(str(config_file))

    def test_process_draft_prs_type_error_exits(self, tmp_path):
        config_file = tmp_path / "config.yaml"
        config_file.write_text(self._batch_yaml('process_draft_prs: "true"'))
        with pytest.raises(ConfigError):
            config.load_config(str(config_file))

    def test_include_fork_repositories_requires_boolean(self, tmp_path):
        config_file = tmp_path / "config.yaml"
        config_file.write_text(self._batch_yaml('include_fork_repositories: "false"'))
        with pytest.raises(ConfigError):
            config.load_config(str(config_file))

    def test_state_comment_timezone_requires_non_empty_string(self, tmp_path):
        config_file = tmp_path / "config.yaml"
        config_file.write_text(self._batch_yaml('state_comment_timezone: ""'))
        with pytest.raises(ConfigError):
            config.load_config(str(config_file))

    def test_coderabbit_auto_resume_max_per_run_requires_positive_integer(
        self, tmp_path
    ):
        config_file = tmp_path / "config.yaml"
        config_file.write_text(
            self._batch_yaml("coderabbit_auto_resume_max_per_run: 0")
        )
        with pytest.raises(ConfigError):
            config.load_config(str(config_file))

    def test_state_comment_timezone_requires_valid_timezone(self, tmp_path):
        config_file = tmp_path / "config.yaml"
        config_file.write_text(self._batch_yaml('state_comment_timezone: "Not/AZone"'))
        with pytest.raises(ConfigError):
            config.load_config(str(config_file))

    def test_coderabbit_auto_resume_max_per_run_rejects_boolean(self, tmp_path):
        config_file = tmp_path / "config.yaml"
        config_file.write_text(
            self._batch_yaml("coderabbit_auto_resume_max_per_run: true")
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
global:
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

    def test_unknown_global_key_raises(self, tmp_path):
        config_file = tmp_path / "config.yaml"
        config_file.write_text(
            """
global:
  invalid_global_key: true
repositories:
  - repo: owner/repo1
""".strip()
        )
        with pytest.raises(ConfigError) as excinfo:
            config.load_config(str(config_file))
        assert "Unknown config key(s) in 'global'" in str(excinfo.value)
        assert "'invalid_global_key'" in str(excinfo.value)

    def test_unknown_model_key_raises(self, tmp_path):
        config_file = tmp_path / "config.yaml"
        config_file.write_text(
            """
global:
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

    def test_global_setup_is_parsed(self, tmp_path):
        config_file = tmp_path / "config.yaml"
        config_file.write_text(
            """
global:
  setup:
    when: always
    commands:
      - run: npm install -g some-tool
        name: Install global tool
repositories:
  - repo: owner/repo1
""".strip()
        )
        cfg = config.load_config(str(config_file))
        assert cfg["global_setup"] == {
            "when": "always",
            "commands": [
                {"run": "npm install -g some-tool", "name": "Install global tool"}
            ],
        }

    def test_global_setup_defaults_when(self, tmp_path):
        config_file = tmp_path / "config.yaml"
        config_file.write_text(
            """
global:
  setup:
    commands:
      - run: echo hello
repositories:
  - repo: owner/repo1
""".strip()
        )
        cfg = config.load_config(str(config_file))
        assert cfg["global_setup"]["when"] == "always"
        assert cfg["global_setup"]["commands"] == [{"run": "echo hello"}]

    def test_global_setup_and_repo_setup_both_present(self, tmp_path):
        config_file = tmp_path / "config.yaml"
        config_file.write_text(
            """
global:
  setup:
    commands:
      - run: npm install -g tool
repositories:
  - repo: owner/repo1
    setup:
      when: clone_only
      commands:
        - run: npm install
""".strip()
        )
        cfg = config.load_config(str(config_file))
        assert cfg["global_setup"] == {
            "when": "always",
            "commands": [{"run": "npm install -g tool"}],
        }
        assert cfg["repositories"][0]["setup"] == {
            "when": "clone_only",
            "commands": [{"run": "npm install"}],
        }

    def test_global_setup_absent_no_global_setup_key(self, tmp_path):
        config_file = tmp_path / "config.yaml"
        config_file.write_text(
            """
repositories:
  - repo: owner/repo1
""".strip()
        )
        cfg = config.load_config(str(config_file))
        assert "global_setup" not in cfg

    def test_repo_setup_is_parsed(self, tmp_path):
        config_file = tmp_path / "config.yaml"
        config_file.write_text(
            """
repositories:
  - repo: owner/repo1
    setup:
      when: clone_only
      commands:
        - run: npm install
          name: Install
""".strip()
        )
        cfg = config.load_config(str(config_file))
        assert cfg["repositories"][0]["setup"] == {
            "when": "clone_only",
            "commands": [{"run": "npm install", "name": "Install"}],
        }

    def test_coderabbit_ignore_nitpick_default(self, tmp_path):
        config_file = tmp_path / "config.yaml"
        config_file.write_text(self._batch_yaml())
        cfg = config.load_config(str(config_file))
        assert cfg["coderabbit_ignore_nitpick"] is False

    def test_coderabbit_ignore_nitpick_true(self, tmp_path):
        config_file = tmp_path / "config.yaml"
        config_file.write_text(self._batch_yaml("coderabbit_ignore_nitpick: true"))
        cfg = config.load_config(str(config_file))
        assert cfg["coderabbit_ignore_nitpick"] is True

    def test_coderabbit_ignore_nitpick_non_bool_raises(self, tmp_path):
        config_file = tmp_path / "config.yaml"
        config_file.write_text(self._batch_yaml('coderabbit_ignore_nitpick: "true"'))
        with pytest.raises(ConfigError):
            config.load_config(str(config_file))

    def test_repo_per_repo_model_override(self, tmp_path):
        config_file = tmp_path / "config.yaml"
        config_file.write_text(
            """
global:
  models:
    summarize: haiku
    fix: sonnet
repositories:
  - repo: owner/repo1
    models:
      fix: opus
""".strip()
        )
        cfg = config.load_config(str(config_file))
        # Global config has full models
        assert cfg["models"] == {"summarize": "haiku", "fix": "sonnet"}
        # Repo entry only has the override
        assert cfg["repositories"][0]["models"] == {"fix": "opus"}


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

    def _batch_yaml(self, extra: str = "") -> str:
        if extra:
            indented = "\n".join("  " + line for line in extra.strip().split("\n"))
            return f"global:\n{indented}\nrepositories:\n  - repo: owner/repo1"
        return "repositories:\n  - repo: owner/repo1"

    def test_limit_keys_accept_valid_integers(self, tmp_path):
        config_file = tmp_path / "config.yaml"
        config_file.write_text(
            self._batch_yaml(
                "max_modified_prs_per_run: 5\nmax_committed_prs_per_run: 3\nmax_claude_prs_per_run: 1"
            )
        )
        cfg = config.load_config(str(config_file))
        assert cfg["max_modified_prs_per_run"] == 5
        assert cfg["max_committed_prs_per_run"] == 3
        assert cfg["max_claude_prs_per_run"] == 1

    def test_limit_keys_accept_zero(self, tmp_path):
        config_file = tmp_path / "config.yaml"
        config_file.write_text(
            self._batch_yaml(
                "max_modified_prs_per_run: 0\nmax_committed_prs_per_run: 0\nmax_claude_prs_per_run: 0"
            )
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
        config_file.write_text(self._batch_yaml(f"{key}: -1"))
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
        config_file.write_text(self._batch_yaml(f'{key}: "abc"'))
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
        config_file.write_text(self._batch_yaml(f"{key}: true"))
        with pytest.raises(ConfigError):
            config.load_config(str(config_file))


class TestExcludeFilters:
    def _batch_yaml(self, extra: str = "") -> str:
        if extra:
            indented = "\n".join("  " + line for line in extra.strip().split("\n"))
            return f"global:\n{indented}\nrepositories:\n  - repo: owner/repo1"
        return "repositories:\n  - repo: owner/repo1"

    def test_exclude_authors_valid(self, tmp_path):
        config_file = tmp_path / "config.yaml"
        config_file.write_text(
            self._batch_yaml('exclude_authors: ["*[bot]", "some-user"]')
        )
        cfg = config.load_config(str(config_file))
        assert cfg["exclude_authors"] == ["*[bot]", "some-user"]

    def test_exclude_labels_valid(self, tmp_path):
        config_file = tmp_path / "config.yaml"
        config_file.write_text(
            self._batch_yaml('exclude_labels: ["autorelease: *", "do-not-merge"]')
        )
        cfg = config.load_config(str(config_file))
        assert cfg["exclude_labels"] == ["autorelease: *", "do-not-merge"]

    def test_exclude_authors_defaults_to_empty(self, tmp_path):
        config_file = tmp_path / "config.yaml"
        config_file.write_text(self._batch_yaml())
        cfg = config.load_config(str(config_file))
        assert cfg["exclude_authors"] == []

    def test_exclude_labels_defaults_to_empty(self, tmp_path):
        config_file = tmp_path / "config.yaml"
        config_file.write_text(self._batch_yaml())
        cfg = config.load_config(str(config_file))
        assert cfg["exclude_labels"] == []

    @pytest.mark.parametrize(
        "key",
        ["exclude_authors", "exclude_labels", "target_authors", "auto_merge_authors"],
    )
    def test_invalid_type_string_raises(self, tmp_path, key):
        config_file = tmp_path / "config.yaml"
        config_file.write_text(self._batch_yaml(f'{key}: "not-a-list"'))
        with pytest.raises(ConfigError):
            config.load_config(str(config_file))

    @pytest.mark.parametrize(
        "key",
        ["exclude_authors", "exclude_labels", "target_authors", "auto_merge_authors"],
    )
    def test_invalid_type_int_raises(self, tmp_path, key):
        config_file = tmp_path / "config.yaml"
        config_file.write_text(self._batch_yaml(f"{key}: 123"))
        with pytest.raises(ConfigError):
            config.load_config(str(config_file))

    @pytest.mark.parametrize(
        "key",
        ["exclude_authors", "exclude_labels", "target_authors", "auto_merge_authors"],
    )
    def test_invalid_list_of_int_raises(self, tmp_path, key):
        config_file = tmp_path / "config.yaml"
        config_file.write_text(self._batch_yaml(f"{key}: [1, 2, 3]"))
        with pytest.raises(ConfigError):
            config.load_config(str(config_file))


class TestMergeMethodConfig:
    def _batch_yaml(self, extra: str = "") -> str:
        if extra:
            indented = "\n".join("  " + line for line in extra.strip().split("\n"))
            return f"global:\n{indented}\nrepositories:\n  - repo: owner/repo1"
        return "repositories:\n  - repo: owner/repo1"

    @pytest.mark.parametrize("method", ["auto", "merge", "squash", "rebase"])
    def test_valid_merge_method_accepted(self, tmp_path, method):
        config_file = tmp_path / "config.yaml"
        config_file.write_text(self._batch_yaml(f'merge_method: "{method}"'))
        cfg = config.load_config(str(config_file))
        assert cfg["merge_method"] == method

    def test_merge_method_defaults_to_auto(self, tmp_path):
        config_file = tmp_path / "config.yaml"
        config_file.write_text(self._batch_yaml())
        cfg = config.load_config(str(config_file))
        assert cfg["merge_method"] == "auto"

    def test_invalid_merge_method_raises(self, tmp_path):
        config_file = tmp_path / "config.yaml"
        config_file.write_text(self._batch_yaml('merge_method: "fast-forward"'))
        with pytest.raises(ConfigError, match="merge_method must be one of"):
            config.load_config(str(config_file))

    def test_non_string_merge_method_raises(self, tmp_path):
        config_file = tmp_path / "config.yaml"
        config_file.write_text(self._batch_yaml("merge_method: 123"))
        with pytest.raises(
            ConfigError, match="merge_method must be a non-empty string"
        ):
            config.load_config(str(config_file))


class TestBaseUpdateMethodConfig:
    def _batch_yaml(self, extra: str = "") -> str:
        if extra:
            indented = "\n".join("  " + line for line in extra.strip().split("\n"))
            return f"global:\n{indented}\nrepositories:\n  - repo: owner/repo1"
        return "repositories:\n  - repo: owner/repo1"

    @pytest.mark.parametrize("method", ["merge", "rebase"])
    def test_valid_base_update_method_accepted(self, tmp_path, method):
        config_file = tmp_path / "config.yaml"
        config_file.write_text(self._batch_yaml(f'base_update_method: "{method}"'))
        cfg = config.load_config(str(config_file))
        assert cfg["base_update_method"] == method

    def test_base_update_method_defaults_to_merge(self, tmp_path):
        config_file = tmp_path / "config.yaml"
        config_file.write_text(self._batch_yaml())
        cfg = config.load_config(str(config_file))
        assert cfg["base_update_method"] == "merge"

    def test_invalid_base_update_method_raises(self, tmp_path):
        config_file = tmp_path / "config.yaml"
        config_file.write_text(self._batch_yaml('base_update_method: "squash"'))
        with pytest.raises(ConfigError, match="base_update_method must be one of"):
            config.load_config(str(config_file))

    def test_non_string_base_update_method_raises(self, tmp_path):
        config_file = tmp_path / "config.yaml"
        config_file.write_text(self._batch_yaml("base_update_method: true"))
        with pytest.raises(
            ConfigError, match="base_update_method must be a non-empty string"
        ):
            config.load_config(str(config_file))


class TestLoadSingleConfig:
    def test_returns_default_when_path_is_none(self):
        cfg = config.load_single_config(None)
        assert cfg["repositories"] == []
        assert cfg["user_name"] is None
        assert cfg["user_email"] is None
        assert cfg["setup"] is None
        assert cfg["models"]["summarize"] == "haiku"
        assert cfg["models"]["fix"] == "sonnet"
        assert "ci_pending" in cfg["enabled_pr_labels"]

    def test_returns_default_when_path_does_not_exist(self, tmp_path):
        cfg = config.load_single_config(str(tmp_path / "nonexistent.yaml"))
        assert cfg["repositories"] == []
        assert cfg["models"]["summarize"] == "haiku"

    def test_loads_settings_from_existing_file(self, tmp_path):
        config_file = tmp_path / ".refix.yaml"
        config_file.write_text(
            """
user_name: "MyBot"
user_email: "bot@example.com"
models:
  summarize: claude-haiku-custom
  fix: claude-sonnet-custom
auto_merge: true
""".strip()
        )
        cfg = config.load_single_config(str(config_file))
        assert cfg["repositories"] == []
        assert cfg["user_name"] == "MyBot"
        assert cfg["user_email"] == "bot@example.com"
        assert cfg["models"]["summarize"] == "claude-haiku-custom"
        assert cfg["models"]["fix"] == "claude-sonnet-custom"
        assert cfg["auto_merge"] is True

    def test_loads_setup_section(self, tmp_path):
        config_file = tmp_path / ".refix.yaml"
        config_file.write_text(
            """
setup:
  when: clone_only
  commands:
    - run: pip install -r requirements.txt
      name: Install deps
""".strip()
        )
        cfg = config.load_single_config(str(config_file))
        assert cfg["setup"] == {
            "when": "clone_only",
            "commands": [
                {"run": "pip install -r requirements.txt", "name": "Install deps"}
            ],
        }

    def test_rejects_repositories_key(self, tmp_path):
        config_file = tmp_path / ".refix.yaml"
        config_file.write_text(
            """
repositories:
  - repo: owner/other-repo
""".strip()
        )
        with pytest.raises(ConfigError) as excinfo:
            config.load_single_config(str(config_file))
        assert "Unknown config key(s)" in str(excinfo.value)
        assert "'repositories'" in str(excinfo.value)

    def test_rejects_include_fork_repositories_key(self, tmp_path):
        config_file = tmp_path / ".refix.yaml"
        config_file.write_text("include_fork_repositories: false\n")
        with pytest.raises(ConfigError) as excinfo:
            config.load_single_config(str(config_file))
        assert "Unknown config key(s)" in str(excinfo.value)

    def test_raises_on_invalid_yaml(self, tmp_path):
        config_file = tmp_path / ".refix.yaml"
        config_file.write_text("{bad: yaml: syntax:")
        with pytest.raises(ConfigError):
            config.load_single_config(str(config_file))

    def test_raises_on_invalid_field_value(self, tmp_path):
        config_file = tmp_path / ".refix.yaml"
        config_file.write_text("auto_merge: not-a-bool\n")
        with pytest.raises(ConfigError):
            config.load_single_config(str(config_file))

    def test_coderabbit_ignore_nitpick(self, tmp_path):
        config_file = tmp_path / ".refix.yaml"
        config_file.write_text("coderabbit_ignore_nitpick: true\n")
        cfg = config.load_single_config(str(config_file))
        assert cfg["coderabbit_ignore_nitpick"] is True


class TestMergeRepoConfig:
    def test_scalar_override(self):
        global_cfg = config._make_default_config()
        global_cfg["auto_merge"] = False
        global_cfg["merge_method"] = "auto"

        repo_entry = {"repo": "owner/repo1", "auto_merge": True}
        merged = config.merge_repo_config(global_cfg, repo_entry)

        assert merged["auto_merge"] is True
        assert merged["merge_method"] == "auto"  # unchanged

    def test_dict_deep_merge_models(self):
        global_cfg = config._make_default_config()
        global_cfg["models"] = {"summarize": "haiku", "fix": "sonnet"}

        repo_entry = {"repo": "owner/repo1", "models": {"fix": "opus"}}
        merged = config.merge_repo_config(global_cfg, repo_entry)

        assert merged["models"] == {"summarize": "haiku", "fix": "opus"}

    def test_dict_deep_merge_triggers(self):
        global_cfg = config._make_default_config()
        global_cfg["triggers"] = {"issue_comment": {"authors": ["bot1"]}}

        repo_entry = {
            "repo": "owner/repo1",
            "triggers": {"issue_comment": {"authors": ["bot2"]}},
        }
        merged = config.merge_repo_config(global_cfg, repo_entry)

        assert merged["triggers"] == {"issue_comment": {"authors": ["bot2"]}}

    def test_setup_from_repo_entry(self):
        global_cfg = config._make_default_config()
        assert global_cfg["setup"] is None

        repo_setup = {"when": "always", "commands": [{"run": "npm install"}]}
        repo_entry = {"repo": "owner/repo1", "setup": repo_setup}
        merged = config.merge_repo_config(global_cfg, repo_entry)

        assert merged["setup"] == repo_setup

    def test_user_name_email_override(self):
        global_cfg = config._make_default_config()
        global_cfg["user_name"] = "GlobalBot"
        global_cfg["user_email"] = "global@example.com"

        repo_entry = {
            "repo": "owner/repo1",
            "user_name": "RepoBot",
            "user_email": "repo@example.com",
        }
        merged = config.merge_repo_config(global_cfg, repo_entry)

        assert merged["user_name"] == "RepoBot"
        assert merged["user_email"] == "repo@example.com"

    def test_global_values_used_when_repo_has_no_overrides(self):
        global_cfg = config._make_default_config()
        global_cfg["user_name"] = "GlobalBot"
        global_cfg["auto_merge"] = True

        repo_entry = {"repo": "owner/repo1"}
        merged = config.merge_repo_config(global_cfg, repo_entry)

        assert merged["user_name"] == "GlobalBot"
        assert merged["auto_merge"] is True

    def test_does_not_mutate_global_config(self):
        global_cfg = config._make_default_config()
        global_cfg["models"] = {"summarize": "haiku", "fix": "sonnet"}

        repo_entry = {"repo": "owner/repo1", "models": {"fix": "opus"}}
        config.merge_repo_config(global_cfg, repo_entry)

        # global config should be unchanged
        assert global_cfg["models"] == {"summarize": "haiku", "fix": "sonnet"}

    def test_list_value_replaced_not_merged(self):
        global_cfg = config._make_default_config()
        global_cfg["exclude_authors"] = ["bot1", "bot2"]

        repo_entry = {"repo": "owner/repo1", "exclude_authors": ["bot3"]}
        merged = config.merge_repo_config(global_cfg, repo_entry)

        assert merged["exclude_authors"] == ["bot3"]

    def test_coderabbit_auto_resume_triggers_deep_merge(self):
        global_cfg = config._make_default_config()
        global_cfg["coderabbit_auto_resume_triggers"] = {
            "rate_limit": True,
            "draft_detected": True,
        }

        repo_entry = {
            "repo": "owner/repo1",
            "coderabbit_auto_resume_triggers": {"rate_limit": False},
        }
        merged = config.merge_repo_config(global_cfg, repo_entry)

        assert merged["coderabbit_auto_resume_triggers"] == {
            "rate_limit": False,
            "draft_detected": True,
        }


class TestTriggersConfig:
    def _batch_yaml(self, extra: str = "") -> str:
        if extra:
            indented = "\n".join("  " + line for line in extra.strip().split("\n"))
            return f"global:\n{indented}\nrepositories:\n  - repo: owner/repo1"
        return "repositories:\n  - repo: owner/repo1"

    def test_triggers_issue_comment_authors_valid(self, tmp_path):
        config_file = tmp_path / "config.yaml"
        config_file.write_text(
            self._batch_yaml(
                "triggers:\n  issue_comment:\n    authors:\n      - coderabbitai[bot]\n      - other-bot[bot]"
            )
        )
        cfg = config.load_config(str(config_file))
        assert cfg["triggers"] == {
            "issue_comment": {"authors": ["coderabbitai[bot]", "other-bot[bot]"]}
        }

    def test_triggers_defaults_to_empty(self, tmp_path):
        config_file = tmp_path / "config.yaml"
        config_file.write_text(self._batch_yaml())
        cfg = config.load_config(str(config_file))
        assert cfg["triggers"] == {}

    def test_triggers_empty_mapping_accepted(self, tmp_path):
        config_file = tmp_path / "config.yaml"
        config_file.write_text(self._batch_yaml("triggers: {}"))
        cfg = config.load_config(str(config_file))
        assert cfg["triggers"] == {}

    def test_triggers_issue_comment_no_authors_accepted(self, tmp_path):
        config_file = tmp_path / "config.yaml"
        config_file.write_text(self._batch_yaml("triggers:\n  issue_comment: {}"))
        cfg = config.load_config(str(config_file))
        assert cfg["triggers"] == {"issue_comment": {}}

    def test_triggers_requires_mapping(self, tmp_path):
        config_file = tmp_path / "config.yaml"
        config_file.write_text(self._batch_yaml("triggers: true"))
        with pytest.raises(ConfigError, match="triggers must be a mapping"):
            config.load_config(str(config_file))

    def test_triggers_unknown_key_raises(self, tmp_path):
        config_file = tmp_path / "config.yaml"
        config_file.write_text(self._batch_yaml("triggers:\n  unknown_event: {}"))
        with pytest.raises(ConfigError, match="Unknown config key"):
            config.load_config(str(config_file))

    def test_triggers_issue_comment_requires_mapping(self, tmp_path):
        config_file = tmp_path / "config.yaml"
        config_file.write_text(self._batch_yaml("triggers:\n  issue_comment: true"))
        with pytest.raises(
            ConfigError, match="triggers.issue_comment must be a mapping"
        ):
            config.load_config(str(config_file))

    def test_triggers_issue_comment_unknown_key_raises(self, tmp_path):
        config_file = tmp_path / "config.yaml"
        config_file.write_text(
            self._batch_yaml("triggers:\n  issue_comment:\n    unknown: foo")
        )
        with pytest.raises(ConfigError, match="Unknown config key"):
            config.load_config(str(config_file))

    def test_triggers_issue_comment_authors_requires_list(self, tmp_path):
        config_file = tmp_path / "config.yaml"
        config_file.write_text(
            self._batch_yaml(
                "triggers:\n  issue_comment:\n    authors: coderabbitai[bot]"
            )
        )
        with pytest.raises(
            ConfigError, match="triggers.issue_comment.authors must be a list"
        ):
            config.load_config(str(config_file))

    def test_triggers_issue_comment_authors_rejects_non_string_element(self, tmp_path):
        config_file = tmp_path / "config.yaml"
        config_file.write_text(
            self._batch_yaml("triggers:\n  issue_comment:\n    authors:\n      - 123")
        )
        with pytest.raises(ConfigError, match="must be a non-empty string"):
            config.load_config(str(config_file))

    def test_triggers_issue_comment_authors_rejects_empty_string_element(
        self, tmp_path
    ):
        config_file = tmp_path / "config.yaml"
        config_file.write_text(
            self._batch_yaml('triggers:\n  issue_comment:\n    authors:\n      - ""')
        )
        with pytest.raises(ConfigError, match="must be a non-empty string"):
            config.load_config(str(config_file))


class TestGetUsePrLabels:
    def test_get_use_pr_labels_returns_default_true(self):
        runtime_config = config._make_default_config()
        result = config.get_use_pr_labels(runtime_config, config.DEFAULT_CONFIG)
        assert result is True

    def test_get_use_pr_labels_returns_false_when_configured(self, tmp_path):
        config_file = tmp_path / "config.yaml"
        config_file.write_text("use_pr_labels: false\n")
        cfg = config.load_single_config(str(config_file))
        result = config.get_use_pr_labels(cfg, config.DEFAULT_CONFIG)
        assert result is False

    def test_use_pr_labels_in_batch_config(self, tmp_path):
        config_file = tmp_path / "config.yaml"
        config_file.write_text(
            "global:\n  use_pr_labels: false\nrepositories:\n  - repo: owner/repo\n"
        )
        cfg = config.load_config(str(config_file))
        result = config.get_use_pr_labels(cfg, config.DEFAULT_CONFIG)
        assert result is False


class TestUseLocalState:
    def test_use_local_state_default_false(self):
        runtime_config = config._make_default_config()
        assert runtime_config.get("use_local_state") is False

    def test_use_local_state_accepted_in_single_config(self, tmp_path):
        config_file = tmp_path / "config.yaml"
        config_file.write_text("use_local_state: true\n")
        cfg = config.load_single_config(str(config_file))
        assert cfg.get("use_local_state") is True

    def test_use_local_state_accepted_in_batch_config(self, tmp_path):
        config_file = tmp_path / "config.yaml"
        config_file.write_text(
            "global:\n  use_local_state: true\nrepositories:\n  - repo: owner/repo\n"
        )
        cfg = config.load_config(str(config_file))
        assert cfg.get("use_local_state") is True

    def test_use_local_state_rejects_non_bool(self, tmp_path):
        config_file = tmp_path / "config.yaml"
        config_file.write_text("use_local_state: yes_string\n")
        with pytest.raises(ConfigError):
            config.load_single_config(str(config_file))
