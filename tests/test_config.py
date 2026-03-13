"""Unit tests for config loading and repository expansion."""

from unittest.mock import Mock, patch

import pytest

import auto_fixer
import config
from errors import ConfigError


class TestLoadConfig:
    def test_valid_config_with_all_keys(self, tmp_path):
        config_file = tmp_path / "config.yaml"
        config_file.write_text(
            """
models:
  summarize: claude-haiku
  fix: claude-sonnet
ci_log_max_lines: 250
execution_report: true
auto_merge: true
coderabbit_auto_resume: true
coderabbit_auto_resume_max_per_run: 3
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
            "execution_report": True,
            "auto_merge": True,
            "enabled_pr_labels": [
                "running",
                "done",
                "merged",
                "auto_merge_requested",
            ],
            "coderabbit_auto_resume": True,
            "coderabbit_auto_resume_max_per_run": 3,
            "process_draft_prs": False,
            "state_comment_timezone": "UTC",
            "max_modified_prs_per_run": 0,
            "max_committed_prs_per_run": 2,
            "max_claude_prs_per_run": 0,
            "ci_empty_as_success": True,
            "ci_empty_grace_minutes": 5,
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
        assert cfg["execution_report"] is False
        assert cfg["auto_merge"] is False
        assert cfg["enabled_pr_labels"] == [
            "running",
            "done",
            "merged",
            "auto_merge_requested",
        ]
        assert cfg["coderabbit_auto_resume"] is False
        assert cfg["coderabbit_auto_resume_max_per_run"] == 1
        assert cfg["process_draft_prs"] is False
        assert cfg["state_comment_timezone"] == "JST"
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

    def test_execution_report_requires_boolean(self, tmp_path):
        config_file = tmp_path / "config.yaml"
        config_file.write_text(
            """
execution_report: "true"
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

    def test_unknown_keys_warns_and_continues(self, tmp_path, capsys):
        config_file = tmp_path / "config.yaml"
        config_file.write_text(
            """
invalid_top: true
models:
  summarize: custom-haiku
  invalid_model_key: 123
repositories:
  - repo: owner/repo1
    invalid_repo_key: ignored
""".strip()
        )

        cfg = config.load_config(str(config_file))
        err = capsys.readouterr().err
        assert "Warning: Unknown key 'invalid_top' found in config." in err
        assert "Warning: Unknown key 'invalid_model_key' found in config." in err
        assert "Warning: Unknown key 'invalid_repo_key' found in config." in err
        assert cfg["models"]["summarize"] == "custom-haiku"
        assert cfg["repositories"][0]["repo"] == "owner/repo1"


class TestExpandRepositories:
    def test_no_wildcard_returns_original(self):
        repos = [{"repo": "owner/repo1"}, {"repo": "owner/repo2"}]
        expanded = auto_fixer.expand_repositories(repos)
        assert expanded == repos

    def test_expand_wildcard(self):
        repos = [{"repo": "owner/*", "user_name": "bot"}]
        mock_stdout = "owner/repo1\nowner/repo2\n"
        with patch("config.run_command") as mock_run:
            mock_run.return_value = Mock(returncode=0, stdout=mock_stdout, stderr="")
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

    def test_expand_wildcard_fail_aborts(self):
        repos = [{"repo": "owner/*"}]
        with patch("config.run_command") as mock_run:
            mock_run.return_value = Mock(returncode=1, stdout="", stderr="error")
            with pytest.raises(ConfigError) as excinfo:
                auto_fixer.expand_repositories(repos)

        assert "failed to expand owner/*" in str(excinfo.value)

    def test_expand_wildcard_empty_results_aborts(self):
        repos = [{"repo": "owner/*"}]
        with patch("config.run_command") as mock_run:
            mock_run.return_value = Mock(returncode=0, stdout="", stderr="")
            with pytest.raises(ConfigError) as excinfo:
                auto_fixer.expand_repositories(repos)

        assert "no repositories found for owner/*" in str(excinfo.value)


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
