# Refix

[English version](README.md)

CodeRabbit のレビュー指摘を Claude で自動修正する GitHub Action です。

## 主な機能

- CodeRabbit の未解決レビューを検出・要約し、Claude Code でコードを自動修正
- CodeRabbit の自動 resume（レート制限・レビュー失敗時に `@coderabbitai resume` を自動投稿、オプション）
- 失敗した CI ログを読み取り、エラーも自動修正
- ベースブランチへの追従・コンフリクト解消
- 修正完了後の自動マージ（オプション）
- PR ラベルと状態コメントで進捗を管理

## セットアップ

### 1. ワークフローの追加

リポジトリのルートで以下を実行します:

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/HappyOnigiri/Refix/main/scripts/init.ja.sh)
```

`.github/workflows/run-refix.yml` が生成されます。PR イベント・CI 完了・コメントをトリガーに自動実行されます。

### 2. シークレットの登録

リポジトリの **Settings > Secrets and variables > Actions** に以下を追加します:

- **`GH_TOKEN`** — Fine-grained Personal Access Token
  - GitHub Settings > Developer settings > Personal access tokens > Fine-grained
    tokens で作成
  - 必要な Repository permissions:
    - Contents: Read and write（コミットの push）
    - Pull requests: Read and write（ラベル・コメント・auto-merge）
    - Issues: Read and write（issue_comment イベント対応）
    - Metadata: Read（デフォルトで付与）
- **`CLAUDE_CODE_OAUTH_TOKEN`** — Claude Code の OAuth トークン
  - `claude setup-token` コマンドで発行

## 設定（任意）

リポジトリルートに `.refix.yaml` を配置するか、GitHub Actions Variable に `REFIX_CONFIG_YAML`
として設定することで、Refix の動作をカスタマイズできます。

利用可能なオプションは [`.refix.sample.yaml`](.refix.sample.yaml) を参照してください。

## バッチモード

複数リポジトリを一括処理するには、バッチモードを使用します。ワークフローは `.github/workflows/run-refix-batch.yml`
を使用します。

リポジトリルートに `.refix-batch.yaml` を配置するか、GitHub Actions Variable に
`REFIX_CONFIG_BATCH_YAML` として設定することで、処理対象リポジトリや動作をカスタマイズできます。

設定フォーマットは [`.refix-batch.sample.yaml`](.refix-batch.sample.yaml) を参照してください。

## Contributing

コントリビュート歓迎です。

- バグ報告、要望、質問は Issue を作成してください。
- 修正、改善、ドキュメント更新は Pull Request を歓迎します。
- 追加した Issue / PR テンプレートを使うと、内容を整理しやすくなります。

## ライセンス

このプロジェクトは MIT License で提供されます。詳細は [LICENSE](LICENSE) を参照してください。
