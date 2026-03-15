# Refix

[Japanese version](README.ja.md)

`refix` is a Python CLI that automatically triages and fixes unresolved CodeRabbit feedback on GitHub pull requests by using Claude and the GitHub CLI.

## What this tool does

`refix` is designed for repositories that use CodeRabbit for automated PR review and want to close the loop automatically.

For each configured repository, `refix` can:

- scan open pull requests,
- detect unresolved CodeRabbit reviews and unresolved inline review threads,
- summarize review feedback before starting a fix pass,
- inspect failing GitHub Actions checks and include failed log excerpts in the fix prompt,
- update PR branches that are behind the base branch,
- ask Claude to apply code fixes,
- push commits back to the PR branch,
- resolve review threads after successful fixes, and
- persist progress in a PR state comment and with labels such as `refix: running` and `refix: done`.

When CodeRabbit reports a review-side rate limit, `refix` keeps the PR in `refix: running`, still allows CI repair and base-branch catch-up, and skips review-fix / auto-merge until CodeRabbit can resume.

## Features

### Review summarization

Before fixing code, `refix` can summarize unresolved review feedback into a more actionable prompt for the coding agent.

### Automatic code fixing

The tool invokes Claude to apply the requested changes directly in the checked-out pull request branch and pushes the resulting commits.

### CI-aware repair flow

When a pull request has failing GitHub Actions checks, `refix` fetches failed log output and feeds the most relevant excerpts into the fix prompt so the agent can repair CI failures first.

### Branch catch-up and conflict handling

If a PR branch is behind the base branch, `refix` can merge the latest base branch into it and continue the repair flow. Merge conflicts are also routed through the Claude-based fixing flow.

### Batch mode (multi-repository)

You can target a single repository such as `owner/repo`, or expand all repositories under an owner with `owner/*`.

### Single PR mode

You can target a specific pull request directly with `--repo` and `--pr`, without listing repositories in the configuration file. This is useful for manual testing or one-off fixes.

```bash
python auto_fixer.py --repo owner/repo --pr 42
python auto_fixer.py --repo owner/repo --pr 42 --dry-run
python auto_fixer.py --repo owner/repo --pr 42 --config .refix-batch.yaml
```

In single PR mode:

- The `repositories` field in `.refix-batch.yaml` is not required.
- If `--config` points to a file that exists, top-level settings (such as `models`, `ci_log_max_lines`, etc.) are loaded from it, but `repositories` is ignored.
- If no config file exists, built-in defaults are used.
- Both `--repo` and `--pr` must be specified together.

### Repeat-safe state tracking

Processed review items are recorded back to the pull request, which prevents the same unresolved feedback from being handled repeatedly.

## Requirements

- Python 3.12
- `gh` CLI authenticated against GitHub
- Claude CLI authentication for actual fix runs
- A `.refix-batch.yaml` configuration file (for batch mode)
- Optional `.env` file for local environment variables

## Quick start

### Local setup

1. Install dependencies and create local template files:

   `make setup`

2. Edit `.refix-batch.yaml` based on `.refix-batch.sample.yaml`.

3. Authenticate the required CLIs:

   - `gh auth login`
   - Claude CLI authentication, or set `CLAUDE_CODE_OAUTH_TOKEN`

4. Run one of the following commands:

   - `make dry-run` - show what would happen without calling Claude
   - `make run-summarize-only` - summarize review feedback only
   - `make run` - run the full fix flow with verbose logs
   - `make run-silent` - run the full fix flow with reduced logs

## YAML configuration reference

`refix` reads batch configuration from `.refix-batch.yaml` in the repository root, or from a path passed with `--config`.

### Full schema

```yaml
models:
  summarize: "haiku"
  fix: "sonnet"

ci_log_max_lines: 120

# Whether to write Claude's stdout to the PR state comment (optional, default true)
write_result_to_comment: true

# Automatically merge PR when it reaches refix: done state (optional, default false)
# When merge completes, the refix: merged label is applied
auto_merge: false

# Enable only selected Refix PR labels (optional, default: all enabled)
# Allowed values: running, done, merged, auto_merge_requested
# Use [] to disable all Refix label operations
enabled_pr_labels:
  - running
  - done
  - merged
  - auto_merge_requested

# Automatically post `@coderabbitai resume` when CodeRabbit can be resumed automatically
# (rate-limit wait expiry, "Review failed" status caused by head commit changes,
# or re-requesting a skipped review)
# (optional, default false)
coderabbit_auto_resume: false

# Optional per-reason auto-trigger switches for CodeRabbit review skip handling
# Default: both true
coderabbit_auto_resume_triggers:
  rate_limit: true
  draft_detected: true

# Maximum number of `@coderabbitai resume` comments posted per single run
# (optional, default 1)
coderabbit_auto_resume_max_per_run: 1

# Whether to process draft PRs (optional)
# Default: false (draft PRs are skipped)
process_draft_prs: false

# Whether to include fork repositories when expanding owner/* (optional)
# Default: true
include_fork_repositories: true

# Timezone used for timestamps in PR state comments (optional)
# Default: JST
state_comment_timezone: "JST"

# Repository targets (required)
repositories:
  - repo: "owner/repo"
    user_name: "Refix Bot"
    user_email: "bot@example.com"
```

### Top-level keys

#### `models`

Model settings for Claude-based processing.

- Type: mapping
- Required: no
- Default:

  ```yaml
  models:
    summarize: "haiku"
    fix: "sonnet"
  ```

Supported nested keys:

- `summarize`
- `fix`

Unknown nested keys are ignored with a warning.

#### `ci_log_max_lines`

Maximum number of failed CI log lines included in the fix prompt.

- Type: integer
- Required: no
- Default: `120`
- Minimum effective value: `20`

Use this to control prompt size when a PR has failing GitHub Actions checks. Smaller values reduce prompt volume, while larger values include more context from failed jobs.

#### `write_result_to_comment`

Whether to write Claude's stdout to the PR state comment as a collapsible log.

- Type: boolean
- Required: no
- Default: `true`

When enabled, each phase's stdout is embedded in the PR state comment under `Execution logs`.

#### `auto_merge`

Automatically merge the fix PR when it reaches the `refix: done` state.

- Type: boolean
- Required: no
- Default: `false`

When enabled, `refix` will trigger GitHub's auto-merge on the PR after applying fixes. Auto-merge only completes once all required status checks pass.

#### `enabled_pr_labels`

Select which Refix PR labels are enabled.

- Type: list of strings
- Required: no
- Default: `["running", "done", "merged", "auto_merge_requested"]`
- Allowed values: `running`, `done`, `merged`, `auto_merge_requested`

This is an opt-in list: only listed labels are managed (created/added/removed) by `refix`. Set `[]` to disable all Refix label operations.

#### `process_draft_prs`

Whether to include draft PRs in the processing targets.

- Type: boolean
- Required: no
- Default: `false`

When set to `false` (the default), draft PRs are skipped. Set to `true` to process draft PRs alongside regular open PRs.

#### `include_fork_repositories`

Whether wildcard expansion via `owner/*` should include fork repositories.

- Type: boolean
- Required: no
- Default: `true`

When set to `true` (default), expansion includes both source repositories and forks under the owner. Set to `false` to target only source repositories (forks are excluded). This setting affects wildcard expansion only; explicitly listed repositories are always kept.

#### `coderabbit_auto_resume`

Whether `refix` should automatically post `@coderabbitai resume` when CodeRabbit can be resumed automatically.

- Type: boolean
- Required: no
- Default: `false`

When a rate-limit notice is active, `refix` keeps the PR in `refix: running`, skips review-fix / auto-merge, and still performs CI repair plus base-branch merge handling. Enabling this option lets `refix` resume CodeRabbit automatically once the wait window has passed. It also auto-resumes when CodeRabbit posts a `Review failed` status comment caused by head commit changes during review, and can re-request a skipped review via `@coderabbitai review` once a PR is no longer draft.

#### `coderabbit_auto_resume_triggers`

Per-reason switches for CodeRabbit auto-trigger behavior.

- Type: mapping
- Required: no
- Default:

  ```yaml
  coderabbit_auto_resume_triggers:
    rate_limit: true
    draft_detected: true
  ```

Supported nested keys:

- `rate_limit`
- `draft_detected`

`rate_limit` controls automatic retry behavior for rate-limited CodeRabbit runs. `draft_detected` controls whether `refix` posts `@coderabbitai review` after CodeRabbit skipped review because the PR was draft. `draft_detected` is ignored while the PR is still draft, so `refix` only re-requests the review after the PR becomes ready for review.

#### `coderabbit_auto_resume_max_per_run`

Maximum number of CodeRabbit auto-trigger comments that `refix` can post in a single execution.

- Type: integer (`>= 1`)
- Required: no
- Default: `1`

This cap is applied across all repositories and PRs processed by the same run, which helps avoid posting too many `@coderabbitai resume` / `@coderabbitai review` commands at once.

#### `state_comment_timezone`

Timezone used when writing `Processed at` in the PR state comment.

- Type: string
- Required: no
- Default: `"JST"`

You can set either `JST` (alias for `Asia/Tokyo`) or any valid IANA timezone such as `UTC`, `Asia/Tokyo`, or `America/Los_Angeles`.

Commonly used timezone examples:

- `JST` (alias of `Asia/Tokyo`)
- `UTC`
- `Asia/Seoul`
- `Asia/Shanghai`
- `Asia/Singapore`
- `Asia/Kolkata`
- `Europe/London`
- `Europe/Berlin`
- `Europe/Paris`
- `America/New_York`
- `America/Chicago`
- `America/Denver`
- `America/Los_Angeles`
- `Australia/Sydney`
- `Pacific/Auckland`

#### `repositories`

List of repositories that `refix` should process.

- Type: non-empty list
- Required: yes
- Default: none

Each entry supports the keys below.

### Repository entry keys

#### `repositories[].repo`

Repository target in `owner/repo` format.

- Type: string
- Required: yes
- Example: `octocat/Hello-World`

You can also use `owner/*` to expand all repositories under a GitHub user or organization. Other wildcard styles such as `owner/repo*` are not supported by the current implementation.
When `include_fork_repositories` is `false`, this expansion excludes forks and includes only source repositories.

#### `repositories[].user_name`

Git author name used for commits created by `refix`.

- Type: string
- Required: no
- Default: not set

If omitted, `refix` falls back to the effective Git identity available in the execution environment.

#### `repositories[].user_email`

Git author email used for commits created by `refix`.

- Type: string
- Required: no
- Default: not set

If omitted, `refix` falls back to the effective Git identity available in the execution environment.

### Behavior and validation notes

- The YAML root must be a mapping.
- `repositories` must be present and must contain at least one entry.
- Unknown keys are ignored with warnings rather than treated as hard errors.
- `enabled_pr_labels` must be a list containing only `running`, `done`, `merged`, and/or `auto_merge_requested`.
- `state_comment_timezone` must be a valid IANA timezone name (or `JST` alias).
- `include_fork_repositories` controls whether `owner/*` expansion includes forks (`true`) or only source repositories (`false`).
- `models.summarize` in YAML takes priority over the `REFIX_MODEL_SUMMARIZE` environment variable when selecting the summarization model.
- The `coderabbit_auto_resume` option applies to active CodeRabbit rate-limit comments, active `Review failed` status comments (head commit changed during review), and supported `Review skipped` reasons.
- `coderabbit_auto_resume_triggers` lets you enable or disable automatic retry per supported skip reason. Currently supported reasons are `rate_limit` and `draft_detected`.
- Duplicate `@coderabbitai resume` / `@coderabbitai review` comments are avoided when one has already been posted after the latest matching status comment.
- `coderabbit_auto_resume_max_per_run` limits how many auto-trigger comments can be posted per execution (default: 1).

## Per-repository project configuration

You can place a `.refix.yaml` file in the root of the **target repository** (the one being managed by Refix) to define setup commands that Refix runs after cloning or updating that repository.

### Schema

| Key | Type | Required | Default | Description |
|-----|------|----------|---------|-------------|
| `version` | integer | Yes | - | Schema version. Currently only `1` is supported. |
| `setup.when` | string | No | `"always"` | When to run setup commands. `"always"` runs on every clone and update; `"clone_only"` runs only on the first clone. |
| `setup.commands[].run` | string | Yes | - | Shell command to execute (via `sh -c`) in the repository root. |
| `setup.commands[].name` | string | No | - | Human-readable label shown in logs. |

### Example

```yaml
version: 1

setup:
  when: always
  commands:
    - run: npm install
      name: Install Node.js dependencies
    - run: make generate
```

### Behavior

- Commands are executed with `sh -c` in the repository root.
- Each command has a **300-second timeout**.
- If a command fails, subsequent commands are **not** executed.

A template with comments is available at `.refix.sample.yaml` in this repository.

## Running in CI with GitHub Actions

This repository already includes the workflow used to run `refix` in GitHub Actions: `.github/workflows/run-auto-review.yml`.

### What the workflow does

The workflow:

1. checks out the repository,
2. installs Python 3.12 and Python dependencies,
3. installs the Claude CLI,
4. writes `.refix-batch.yaml` from the GitHub Actions variable `REFIX_CONFIG_YAML`,
5. configures Git authentication for push operations, and
6. changes to the `src` directory and runs `python auto_fixer.py --config ../.refix-batch.yaml`.

### Required GitHub Actions configuration

Set the following values in the target repository or organization:

#### Repository or organization variables

- `REFIX_CONFIG_YAML`
  - The full YAML configuration text for `refix`.
  - Store the same content that you would place in a local `.refix-batch.yaml` file.

#### Repository or organization secrets

- `GH_TOKEN`
  - Personal access token used for GitHub API access and pushing fix commits.
- `CLAUDE_CODE_OAUTH_TOKEN`
  - Token used by the Claude CLI during automated fix runs.

### Step-by-step setup

1. Open `Settings` -> `Secrets and variables` -> `Actions`.
2. Add the `REFIX_CONFIG_YAML` variable.
3. Add the `GH_TOKEN` and `CLAUDE_CODE_OAUTH_TOKEN` secrets.
4. Open the `Run auto review` workflow in the Actions tab.
5. Trigger the workflow with `Run workflow`.

### Example `REFIX_CONFIG_YAML`

```yaml
models:
  summarize: "haiku"
  fix: "sonnet"

ci_log_max_lines: 120

enabled_pr_labels:
  - running
  - done
  - merged
  - auto_merge_requested

state_comment_timezone: "JST"

repositories:
  - repo: "your-org/your-repo"
    user_name: "Refix Bot"
    user_email: "bot@example.com"
```

### Included workflows in this repository

- `.github/workflows/run-auto-review.yml`
  - Manual (`workflow_dispatch`) execution for the actual auto-fix flow.
- `.github/workflows/test.yml`
  - Test workflow triggered on pull requests and on manual dispatch.

## Contributing

Contributions are welcome.

- Open an issue for bugs, ideas, or questions.
- Submit a pull request for fixes, improvements, or documentation updates.
- Use the provided issue and pull request templates to keep reports actionable.

## License

This project is licensed under the MIT License. See [LICENSE](LICENSE).
