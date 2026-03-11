# Auto Review Fixer (refix)

GitHub PR 上の CodeRabbit レビューコメントを Claude AI で自動修正する Python CLI ツールです。

## 前提条件

- Python 3.12
- `gh` CLI（認証済み）
- `.env` ファイル
- `.refix.yaml` 実行設定ファイル

## 設定方法

本ツールはリポジトリ直下の `.refix.yaml` という YAML 形式のファイルを使用して動作設定を行います。

### 1. ローカルでの設定方法

リポジトリルートに `.refix.yaml` を作成し、以下のような設定を記述します。

```yaml
# モデル設定 (省略可能)
models:
  summarize: "haiku" # レビュー要約用モデル
  fix: "sonnet" # コード修正用モデル

# CIログの取得最大行数 (省略可能)
ci_log_max_lines: 120

# DRAFT PR も処理対象にするか (省略可能)
# デフォルト: false（DRAFT はスキップ）
process_draft_prs: false

# 実行対象のリポジトリ設定 (必須)
repositories:
  - repo: "owner/repo" # リポジトリ名 (必須: owner/repo 形式)
    user_name: "Refix Bot" # (オプション) git commit 時のユーザー名
    user_email: "bot@example.com" # (オプション) git commit 時のメールアドレス
```

作成後、以下のコマンドで実行できます。

```bash
make run
```

※ ドライランモード（Claudeの呼び出しなし）で実行する場合は `make dry-run` を使用します。

### 2. CI (GitHub Actions) での設定方法

本ツールを GitHub Actions で動作させる場合、YAML の内容を GitHub リポジトリの Variables（変数）として登録します。

1. 対象リポジトリ（オーガナイゼーション）の `Settings` > `Secrets and variables` > `Actions` > **`Variables`** タブを開きます。
2. `New repository variable` （または `New organization variable`）をクリックします。
3. **Name** に `REFIX_CONFIG_YAML` と入力します。
4. **Value** に、ローカルと同様の YAML 形式のテキストを貼り付け、保存します。

これにより、CI ワークフロー実行時に自動的に設定が読み込まれ、ツールが動作します。
