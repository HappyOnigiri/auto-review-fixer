# AGENTS.md

## Overview

Refix — GitHub PR 上の CodeRabbit レビューコメントを Claude AI で自動修正する Python CLI ツール。単一サービス構成。

## Prerequisites

- Python 3.12
- `gh` CLI（認証済み）— 実行時に GitHub API 呼び出しで使用
- `.env` ファイル — `.env.sample` からコピー（ローカル開発・テストでは全項目オプション）
- `.refix.yaml` ファイル — 対象リポジトリやモデルなどの設定

## Project Structure & Development Rules

- **ソースコード**: `src/` 配下に配置する。
- **テスト**: `tests/` 配下に配置する。
- **依存関係**: 新たなパッケージを追加する場合は `requirements.txt` に追記し、`make setup` を実行すること。手動での無秩序な `pip install` は避ける。
- **型ヒント**: Python 3.12 に準拠した型ヒント（標準の `list` や `dict`、`str | int` など）を積極的に記述し、静的解析の精度を高めること。
- **モック化**: 外部API（Claude、GitHub API など）の呼び出しを伴う実装を追加・変更する場合は、`tests/` 内のテストケースで必ず全モック化すること。シークレット情報を必要とするテストは書かない。
- **品質保証 (CI)**: コードを変更した後は必ず `make ci` を実行し、テストがすべて成功することを確認すること。エラーが発生した場合はただちに修正すること。
- **README 更新**: ユーザーに影響のある大きな機能改修や設定変更を行った場合は、`README.md` と `README.ja.md` を実装に合わせて更新すること。

## Test Conventions

- **`mocker.patch()` を使用**: `unittest.mock.patch` の context manager (`with patch(...)`) は使わない。pytest-mock の `mocker` fixture で `mocker.patch(...)` / `mocker.patch.object(...)` / `mocker.patch.dict(...)` に統一する。
- **`make_cmd_result(stdout)` で subprocess 結果モック**: `Mock(returncode=..., stdout=..., stderr=...)` を直接使わず、`tests/conftest.py` の `make_cmd_result` fixture を使う。`stdout` は第一引数（位置引数）、`returncode` / `stderr` はキーワード専用引数（デフォルト `0` / `""`）。
- **`make_process_mock(stdout, stderr)` で Popen モック**: `subprocess.Popen` の戻り値には `make_process_mock` fixture を使う。`communicate()` と `returncode` が自動設定される。
- **ネストされた `with patch()` ブロックの禁止**: 複数パッチが必要な場合は `mocker.patch(...)` を関数冒頭で順に呼び出す。

## Key commands

主要コマンドは `Makefile` に定義済み。`make help-en`（英語）/ `make help`（日本語）で一覧表示。

| Command        | Description                                                                         |
| -------------- | ----------------------------------------------------------------------------------- |
| `make setup`   | venv の作成 + pip 依存インストール + `.env` および `.refix.yaml` のテンプレート作成 |
| `make test`    | pytest 実行（外部呼び出しは全モック、シークレット不要）                             |
| `make dry-run` | Claude を呼ばずにアプリ実行（`.refix.yaml` が必要）                                 |
| `make run`        | 本番実行（Claude CLI + gh CLI + 認証情報が必要）                                    |
| `make sync-ruler` | ruler apply 実行後に AGENTS.md の整形と Source 行の除去を行う                      |

## Caveats

- **リンター** — 未設定。CI は `make ci`（`make test` のエイリアス）のみ実行するため、フォーマット等の自動修正は不要。
- `Makefile` は `.venv/bin/python` を自動検出するため、venv の `activate` なしで `make test` や `make run` などが動作する。
- 処理済みレビュー状態の Source of Truth は GitHub PR 上の State Comment（Markdown テーブル）であり、外部DBやローカルキャッシュは使用しない。
- スモークテスト: `make dry-run` (事前に `.refix.yaml` を用意)
