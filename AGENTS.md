## Cursor Cloud specific instructions

- **Python venv**: `.venv/bin` が PATH に含まれている前提。`make test` / `make ci` は `$(PYTHON)` 変数（`.venv/bin/python` 自動検出）で動作するが、`make run` / `make dry-run` / `make help` は `python` コマンドを直接使うため PATH 設定が必要。
- **リンターなし**: このプロジェクトにはリンター設定がない。品質チェックは `make ci`（= `make test` = pytest）のみ。
- **テストにシークレット不要**: テストは外部APIを全モック化しているため、`GH_TOKEN` や `CLAUDE_CODE_OAUTH_TOKEN` なしで実行可能。
- **`make dry-run` の動作**: `.refix.yaml` のサンプル設定（`owner/repo`）では「Repository not found」エラーが出るが、これはダミーリポジトリのため期待通りの動作。実際のリポジトリを設定すれば正常に動作する。
- **主要コマンド**: `Makefile` 参照。`make test` でテスト、`make dry-run` でスモークテスト。
- **Git ユーザー設定**: `.git/config` に local ユーザー（`HappyOnigiri` / `253838257+NodeMeld@users.noreply.github.com`）を設定済み。スナップショットで保持される。
- **Co-authored-by 無効化**: `commit-msg.cursor.co-author` フックの実行権限を除去して無効化済み。スナップショットで保持される。
