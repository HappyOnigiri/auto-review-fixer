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
- persist progress in a PR state comment and with labels such as `refix:running` and `refix:done`.

When CodeRabbit reports a review-side rate limit, `refix` keeps the PR in `refix:running`, still allows CI repair and base-branch catch-up, and skips review-fix / auto-merge until CodeRabbit can resume.

## Features

### Review summarization

Before fixing code, `refix` can summarize unresolved review feedback into a more actionable prompt for the coding agent.

### Automatic code fixing

The tool invokes Claude to apply the requested changes directly in the checked-out pull request branch and pushes the resulting commits.

### CI-aware repair flow

When a pull request has failing GitHub Actions checks, `refix` fetches failed log output and feeds the most relevant excerpts into the fix prompt so the agent can repair CI failures first.

### Branch catch-up and conflict handling

If a PR branch is behind the base branch, `refix` can merge the latest base branch into it and continue the repair flow. Merge conflicts are also routed through the Claude-based fixing flow.

### Multi-repository targeting

You can target a single repository such as `owner/repo`, or expand all repositories under an owner with `owner/*`.

### Repeat-safe state tracking

Processed review items are recorded back to the pull request, which prevents the same unresolved feedback from being handled repeatedly.

## Requirements

- Python 3.12
- `gh` CLI authenticated against GitHub
- Claude CLI authentication for actual fix runs
- A `.refix.yaml` configuration file
- Optional `.env` file for local environment variables

## Quick start

### Local setup

1. Install dependencies and create local template files:

   `make setup`

2. Edit `.refix.yaml` based on `.refix.yaml.sample`.

3. Authenticate the required CLIs:

   - `gh auth login`
   - Claude CLI authentication, or set `CLAUDE_CODE_OAUTH_TOKEN`

4. Run one of the following commands:

   - `make dry-run` — show what would happen without calling Claude
   - `make run-summarize-only` — summarize review feedback only
   - `make run` — run the full fix flow with verbose logs
   - `make run-silent` — run the full fix flow with reduced logs

## YAML configuration reference

`refix` reads configuration from `.refix.yaml` in the repository root, or from a path passed with `--config`.

### Full schema

```yaml
models:
  summarize: "haiku"
  fix: "sonnet"

ci_log_max_lines: 120

# Automatically merge PR when it reaches refix:done state (optional, default false)
# When merge completes, the refix:merged label is applied
auto_merge: false

# Automatically post `@coderabbitai resume` when CodeRabbit can be resumed automatically
# (rate-limit wait expiry or "Review failed" status caused by head commit changes)
# (optional, default false)
coderabbit_auto_resume: false

# Maximum number of `@coderabbitai resume` comments posted per single run
# (optional, default 1)
coderabbit_auto_resume_max_per_run: 1

# Whether to process draft PRs (optional)
# Default: false (draft PRs are skipped)
process_draft_prs: false

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

#### `auto_merge`

Automatically merge the fix PR when it reaches the `refix:done` state.

- Type: boolean
- Required: no
- Default: `false`

When enabled, `refix` will trigger GitHub's auto-merge on the PR after applying fixes. Auto-merge only completes once all required status checks pass.

#### `process_draft_prs`

Whether to include draft PRs in the processing targets.

- Type: boolean
- Required: no
- Default: `false`

When set to `false` (the default), draft PRs are skipped. Set to `true` to process draft PRs alongside regular open PRs.

#### `coderabbit_auto_resume`

Whether `refix` should automatically post `@coderabbitai resume` when CodeRabbit can be resumed automatically.

- Type: boolean
- Required: no
- Default: `false`

When a rate-limit notice is active, `refix` keeps the PR in `refix:running`, skips review-fix / auto-merge, and still performs CI repair plus base-branch merge handling. Enabling this option lets `refix` resume CodeRabbit automatically once the wait window has passed. It also auto-resumes when CodeRabbit posts a `Review failed` status comment caused by head commit changes during review.

#### `coderabbit_auto_resume_max_per_run`

Maximum number of `@coderabbitai resume` comments that `refix` can post in a single execution.

- Type: integer (`>= 1`)
- Required: no
- Default: `1`

This cap is applied across all repositories and PRs processed by the same run, which helps avoid hitting CodeRabbit rate limits again by posting too many resume comments at once.

#### `state_comment_timezone`

Timezone used when writing `処理日時` in the PR state comment.

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
- `state_comment_timezone` must be a valid IANA timezone name (or `JST` alias).
- `models.summarize` in YAML takes priority over the `REFIX_MODEL_SUMMARIZE` environment variable when selecting the summarization model.
- The `coderabbit_auto_resume` option applies to active CodeRabbit rate-limit comments and active `Review failed` status comments (head commit changed during review). Duplicate `@coderabbitai resume` comments are avoided when one has already been posted after the latest matching status comment.
- `coderabbit_auto_resume_max_per_run` limits how many auto-resume comments can be posted per execution (default: 1).

## Running in CI with GitHub Actions

This repository already includes the workflow used to run `refix` in GitHub Actions: `.github/workflows/run-auto-review.yml`.

### What the workflow does

The workflow:

1. checks out the repository,
2. installs Python 3.12 and Python dependencies,
3. installs the Claude CLI,
4. writes `.refix.yaml` from the GitHub Actions variable `REFIX_CONFIG_YAML`,
5. configures Git authentication for push operations, and
6. changes to the `src` directory and runs `python auto_fixer.py --config ../.refix.yaml`.

### Required GitHub Actions configuration

Set the following values in the target repository or organization:

#### Repository or organization variables

- `REFIX_CONFIG_YAML`
  - The full YAML configuration text for `refix`.
  - Store the same content that you would place in a local `.refix.yaml` file.

#### Repository or organization secrets

- `GH_PAT`
  - Personal access token used for GitHub API access and pushing fix commits.
- `CLAUDE_CODE_OAUTH_TOKEN`
  - Token used by the Claude CLI during automated fix runs.

### Step-by-step setup

1. Open `Settings` -> `Secrets and variables` -> `Actions`.
2. Add the `REFIX_CONFIG_YAML` variable.
3. Add the `GH_PAT` and `CLAUDE_CODE_OAUTH_TOKEN` secrets.
4. Open the `Run auto review` workflow in the Actions tab.
5. Trigger the workflow with `Run workflow`.

### Example `REFIX_CONFIG_YAML`

```yaml
models:
  summarize: "haiku"
  fix: "sonnet"

ci_log_max_lines: 120

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
