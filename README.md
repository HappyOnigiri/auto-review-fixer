# Refix

[Japanese version](README.ja.md)

A GitHub Action that automatically fixes unresolved CodeRabbit review comments
using Claude.

![Refix](.github/assets/refix.jpg)

## Features

- Detects and summarizes unresolved CodeRabbit reviews, then auto-fixes code
  with Claude Code
- CodeRabbit auto resume (automatically posts `@coderabbitai resume` when
  rate-limited or review fails, optional)
- Reads failed CI logs and auto-fixes errors
- Keeps PR branches up to date with the base branch and resolves merge conflicts
- Optionally auto-merges PRs after all fixes are applied
- Tracks progress with PR labels and state comments

## Setup

### 1. Add the workflow

Run the following command in your repository root:

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/HappyOnigiri/Refix/main/scripts/init.sh)
```

This creates `.github/workflows/run-refix.yml`, which triggers automatically on
PR events, CI completion, and comments.

### 2. Register secrets

Go to your repository's **Settings > Secrets and variables > Actions** and add:

- **`GH_TOKEN`** - Classic Personal Access Token
  - Create at: GitHub Settings > Developer settings > Personal access tokens >
    Tokens (classic)
  - Required scopes: `repo`, `read:org`, `read:discussion`
- **`CLAUDE_CODE_OAUTH_TOKEN`** - Claude Code OAuth token
  - Generate with the `claude setup-token` command

## Configuration (optional)

You can customize Refix behavior by placing a `.refix.yaml` file at your
repository root, or by setting `REFIX_CONFIG_YAML` as a GitHub Actions Variable.

See [`.refix.sample.yaml`](.refix.sample.yaml) for all available options.

## Contributing

Contributions are welcome.

- Open an issue for bugs, ideas, or questions.
- Submit a pull request for fixes, improvements, or documentation updates.
- Use the provided issue and pull request templates to keep reports actionable.

## License

This project is licensed under the MIT License. See [LICENSE](LICENSE).
