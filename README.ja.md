# Auto Review Fixer (refix)

[English README](README.md)

`refix` は、Claude と GitHub CLI を使って GitHub Pull Request 上の未解決な CodeRabbit フィードバックを自動的に整理・修正する Python CLI です。

## これは何をするツールか

`refix` は、CodeRabbit を使っているリポジトリで、レビュー指摘の消化を自動化したいケースを想定しています。

設定された各リポジトリに対して、`refix` は次のような処理を行えます。

- オープンな Pull Request を走査する
- 未解決の CodeRabbit レビューと未解決のインラインスレッドを検出する
- 修正前にレビュー内容を要約する
- 失敗している GitHub Actions のログ抜粋を修正プロンプトに含める
- ベースブランチに追従していない PR ブランチを更新する
- Claude にコード修正を依頼する
- 修正コミットを PR ブランチへ push する
- 修正後にレビュー スレッドを解決する
- PR 上の状態管理コメントと `refix: running` / `refix: done` ラベルで進捗を記録する

CodeRabbit がレビュー側のレートリミットに到達した場合でも、`refix` は PR を `refix: running` のまま維持し、CI 修正とベースブランチ追従だけを進め、レビュー修正と auto-merge は再開可能になるまで保留します。

## 主な機能

### レビュー要約

修正に入る前に、未解決レビューを AI エージェントが扱いやすい形に要約できます。

### 自動コード修正

Claude を使って PR ブランチ上のコードを直接修正し、生成されたコミットをそのまま push します。

### CI を踏まえた修正フロー

GitHub Actions が失敗している場合は、失敗ログの重要部分を収集して修正プロンプトに含め、まず CI エラーの解消を試みます。

### ブランチ追従と競合対応

PR ブランチがベースブランチに追従していない場合はマージして進められます。競合が発生した場合も Claude ベースの修正フローで扱えます。

### 複数リポジトリ対応

`owner/repo` の単体指定だけでなく、`owner/*` でオーナー配下の全リポジトリを対象にできます。

### 重複実行を避ける状態管理

処理済みのレビュー項目は PR 上に記録されるため、同じ未解決フィードバックを繰り返し処理しにくい設計です。

## 必要条件

- Python 3.12
- GitHub 認証済みの `gh` CLI
- 実際に修正を走らせる場合の Claude CLI 認証
- `.refix.yaml` 設定ファイル
- 必要に応じてローカル用の `.env` ファイル

## クイックスタート

### ローカル実行

1. 依存関係を入れ、テンプレートファイルを作成します。

   `make setup`

2. `.refix.sample.yaml` をもとに `.refix.yaml` を編集します。

3. 必要な CLI を認証します。

   - `gh auth login`
   - Claude CLI の認証、または `CLAUDE_CODE_OAUTH_TOKEN` の設定

4. 用途に応じてコマンドを実行します。

   - `make dry-run` — Claude を呼ばずに挙動だけ確認
   - `make run-summarize-only` — 要約のみ実行
   - `make run` — 詳細ログつきでフル実行
   - `make run-silent` — ログを抑えてフル実行

## YAML 設定リファレンス

`refix` はリポジトリルートの `.refix.yaml` を読み込みます。別パスを使う場合は `--config` で指定できます。

### 完全なスキーマ

```yaml
models:
  summarize: "haiku"
  fix: "sonnet"

ci_log_max_lines: 120

write_result_to_comment: true

auto_merge: false

enabled_pr_labels:
  - running
  - done
  - merged
  - auto_merge_requested

coderabbit_auto_resume: false

coderabbit_auto_resume_triggers:
  rate_limit: true
  draft_detected: true

coderabbit_auto_resume_max_per_run: 1

process_draft_prs: false

include_fork_repositories: true

state_comment_timezone: "JST"

repositories:
  - repo: "owner/repo"
    user_name: "Refix Bot"
    user_email: "bot@example.com"
```

### トップレベルキー

#### `models`

Claude ベースの処理で使うモデル設定です。

- 型: マッピング
- 必須: いいえ
- デフォルト:

  ```yaml
  models:
    summarize: "haiku"
    fix: "sonnet"
  ```

利用できる子キーは次の 2 つです。

- `summarize`
- `fix`

未知の子キーは警告を出して無視されます。

#### `ci_log_max_lines`

修正プロンプトに含める失敗 CI ログの最大行数です。

- 型: 整数
- 必須: いいえ
- デフォルト: `120`
- 実効最小値: `20`

PR に失敗中の GitHub Actions がある場合、この値でプロンプトへ渡すログ量を調整できます。値を小さくするとプロンプトは軽くなり、大きくすると文脈を多く渡せます。

#### `write_result_to_comment`

Claude の stdout を PR の状態管理コメントに折りたたみログとして書き込むかどうかを設定します。

- 型: boolean
- 必須: いいえ
- デフォルト: `true`

有効にすると、各フェーズの stdout が PR の状態管理コメントの `実行ログ` セクションに記録されます。

#### `auto_merge`

PR が `refix: done` 状態になった際に自動マージします。

- 型: boolean
- 必須: いいえ
- デフォルト: `false`

有効にすると、`refix` は修正適用後に GitHub の auto-merge をトリガーします。auto-merge は必須のステータスチェックがすべて通過した後に完了します。

#### `enabled_pr_labels`

Refix が有効化する PR ラベルを選択します。

- 型: 文字列のリスト
- 必須: いいえ
- デフォルト: `["running", "done", "merged", "auto_merge_requested"]`
- 許可値: `running`, `done`, `merged`, `auto_merge_requested`

この設定は ON 方式です。指定したラベルだけを `refix` が作成・付与・除去します。`[]` を指定すると Refix のラベル操作をすべて無効化できます。

#### `process_draft_prs`

ドラフト PR を処理対象に含めるかどうかを設定します。

- 型: boolean
- 必須: いいえ
- デフォルト: `false`

`false`（デフォルト）の場合、ドラフト PR はスキップされます。`true` にすると、通常のオープン PR と同様にドラフト PR も処理されます。

#### `include_fork_repositories`

`owner/*` のワイルドカード展開時に fork リポジトリを含めるかどうかを設定します。

- 型: boolean
- 必須: いいえ
- デフォルト: `true`

`true`（デフォルト）の場合、展開結果に source リポジトリと fork の両方を含めます。`false` にすると source リポジトリのみを対象にし、fork は除外します。この設定が効くのは `owner/*` 展開時のみで、明示指定した `owner/repo` は常に対象です。

#### `coderabbit_auto_resume`

CodeRabbit を自動で再開できる状態になったときに、`@coderabbitai resume` コメントを自動投稿するかどうかを設定します。

- 型: boolean
- 必須: いいえ
- デフォルト: `false`

レートリミット中は、`refix` は PR を `refix: running` に保ち、レビュー修正と auto-merge を止めつつ、CI 修正とベースブランチ取り込みは継続します。この設定を `true` にすると、待機時間経過後に自動で CodeRabbit の再開を促します。さらに、CodeRabbit が head commit 変更により `Review failed` ステータスコメントを投稿した場合も自動 resume し、`Review skipped` が `Draft detected` だった場合は PR が Draft 解除されたあとに `@coderabbitai review` で再レビューを依頼できます。

#### `coderabbit_auto_resume_triggers`

CodeRabbit の自動再トリガを理由ごとに ON/OFF する設定です。

- 型: マッピング
- 必須: いいえ
- デフォルト:

  ```yaml
  coderabbit_auto_resume_triggers:
    rate_limit: true
    draft_detected: true
  ```

利用できる子キーは次の 2 つです。

- `rate_limit`
- `draft_detected`

`rate_limit` は CodeRabbit のレートリミット時の自動再開を制御します。`draft_detected` は CodeRabbit が Draft PR を理由に `Review skipped` したとき、Ready for review になった後で `@coderabbitai review` を投げるかどうかを制御します。PR がまだ Draft の間は `draft_detected` が有効でも再トリガしません。

#### `coderabbit_auto_resume_max_per_run`

1回の実行で `refix` が投稿できる CodeRabbit 自動再トリガコメント数の上限です。

- 型: 整数（`1` 以上）
- 必須: いいえ
- デフォルト: `1`

同じ実行で処理された全リポジトリ・全PRに対して共通で適用されるため、一度に大量の `@coderabbitai resume` / `@coderabbitai review` を投げることを防げます。

#### `state_comment_timezone`

PR の状態管理コメントに記録する `処理日時` のタイムゾーンです。

- 型: 文字列
- 必須: いいえ
- デフォルト: `"JST"`

`JST`（`Asia/Tokyo` のエイリアス）または `UTC` / `Asia/Tokyo` / `America/Los_Angeles` などの IANA タイムゾーン名を指定できます。

#### `repositories`

`refix` が処理する対象リポジトリの一覧です。

- 型: 空でないリスト
- 必須: はい
- デフォルト: なし

各要素では次のキーを使えます。

### リポジトリエントリのキー

#### `repositories[].repo`

`owner/repo` 形式の対象リポジトリです。

- 型: 文字列
- 必須: はい
- 例: `octocat/Hello-World`

`owner/*` を指定すると、そのオーナー配下の全リポジトリへ展開できます。一方で `owner/repo*` のような別形式のワイルドカードは現在の実装では対応していません。
`include_fork_repositories: false` を指定した場合、この展開では fork リポジトリは除外されます。

#### `repositories[].user_name`

`refix` が作成するコミットに使う Git author 名です。

- 型: 文字列
- 必須: いいえ
- デフォルト: 未設定

省略した場合は、実行環境で有効な Git identity にフォールバックします。

#### `repositories[].user_email`

`refix` が作成するコミットに使う Git author メールアドレスです。

- 型: 文字列
- 必須: いいえ
- デフォルト: 未設定

省略した場合は、実行環境で有効な Git identity にフォールバックします。

### 挙動とバリデーションの補足

- YAML のルートはマッピングである必要があります。
- `repositories` は必須で、1 件以上の要素が必要です。
- 未知のキーは即エラーではなく、警告を出して無視されます。
- `enabled_pr_labels` は `running` / `done` / `merged` / `auto_merge_requested` のみを含むリストである必要があります。
- `state_comment_timezone` は有効な IANA タイムゾーン名（または `JST` エイリアス）である必要があります。
- `include_fork_repositories` は `owner/*` 展開時に fork を含めるか（`true`）除外するか（`false`）を制御します。
- `models.summarize` で要約処理で使用するモデルを指定します。この設定は環境変数 `REFIX_MODEL_SUMMARIZE` より優先されます。
- `models.fix` で修正処理で使用するモデルを指定します。
- `coderabbit_auto_resume` は、最新の CodeRabbit レートリミット通知、`Review failed` ステータス通知（review 中の head commit 変更）、および対応済みの `Review skipped` 理由に対して適用されます。
- `coderabbit_auto_resume_triggers` で、対応している skip 理由ごとに自動再トリガを制御できます。現在は `rate_limit` と `draft_detected` をサポートしています。
- 最新の該当ステータス通知より後にすでに `@coderabbitai resume` / `@coderabbitai review` コメントがある場合、重複投稿しません。
- `coderabbit_auto_resume_max_per_run` で、1回の実行で投稿する自動再トリガコメント件数を制限できます（デフォルト: 1）。

## リポジトリ別プロジェクト設定

**対象リポジトリ**（Refix が管理するリポジトリ）のルートに `.refix-project.yaml` を配置することで、Refix がそのリポジトリをクローンまたは更新した後に実行するセットアップコマンドを定義できます。

### スキーマ

| キー | 型 | 必須 | デフォルト | 説明 |
|------|----|------|-----------|------|
| `version` | integer | Yes | — | スキーマバージョン。現在は `1` のみサポート。 |
| `setup.when` | string | No | `"always"` | セットアップコマンドを実行するタイミング。`"always"` はクローン時・更新時の両方、`"clone_only"` は初回クローン時のみ。 |
| `setup.commands[].run` | string | Yes | — | リポジトリルートで実行するシェルコマンド（`sh -c` 経由）。 |
| `setup.commands[].name` | string | No | — | ログに表示される人間向けのラベル。 |

### 例

```yaml
version: 1

setup:
  when: always
  commands:
    - run: npm install
      name: Install Node.js dependencies
    - run: make generate
```

### 動作仕様

- コマンドはリポジトリルートで `sh -c` を使って実行されます。
- 各コマンドのタイムアウトは **300 秒** です。
- コマンドが失敗した場合、後続のコマンドは**実行されません**。

コメント付きのテンプレートはこのリポジトリの `.refix-project.sample.yaml` を参照してください。

## GitHub Actions での実行方法

このリポジトリには、`refix` を GitHub Actions で動かすためのワークフロー `.github/workflows/run-auto-review.yml` が含まれています。

### ワークフローが行うこと

このワークフローは次の順で動作します。

1. リポジトリを checkout する
2. Python 3.12 と Python 依存関係をセットアップする
3. Claude CLI をインストールする
4. GitHub Actions 変数 `REFIX_CONFIG_YAML` から `.refix.yaml` を生成する
5. push 用の Git 認証を設定する
6. `cd src && python auto_fixer.py --config ../.refix.yaml` を実行する

### 必要な GitHub Actions 設定

対象リポジトリ、またはオーガニゼーションに次の値を設定します。

#### Variables

- `REFIX_CONFIG_YAML`
  - `refix` 用 YAML 設定の全文です。
  - ローカルの `.refix.yaml` に書く内容をそのまま保存してください。

#### Secrets

- `GH_TOKEN`
  - GitHub API 利用と修正コミット push に使う Personal Access Token
- `CLAUDE_CODE_OAUTH_TOKEN`
  - Claude CLI が自動修正時に使うトークン

### セットアップ手順

1. `Settings` -> `Secrets and variables` -> `Actions` を開きます。
2. `REFIX_CONFIG_YAML` を Variable として追加します。
3. `GH_TOKEN` と `CLAUDE_CODE_OAUTH_TOKEN` を Secret として追加します。
4. Actions タブで `Run auto review` ワークフローを開きます。
5. `Run workflow` で手動実行します。

### `REFIX_CONFIG_YAML` の例

```yaml
models:
  summarize: "haiku"
  fix: "sonnet"

ci_log_max_lines: 120

auto_merge: false

enabled_pr_labels:
  - running
  - done
  - merged
  - auto_merge_requested

coderabbit_auto_resume: false

coderabbit_auto_resume_triggers:
  rate_limit: true
  draft_detected: true

coderabbit_auto_resume_max_per_run: 1

process_draft_prs: false

state_comment_timezone: "JST"

repositories:
  - repo: "your-org/your-repo"
    user_name: "Refix Bot"
    user_email: "bot@example.com"
```

### このリポジトリに含まれるワークフロー

- `.github/workflows/run-auto-review.yml`
  - 実際の自動修正フローを手動実行するためのワークフロー
- `.github/workflows/test.yml`
  - Pull Request 時と手動実行でテストを回すワークフロー

## Contributing

コントリビュート歓迎です。

- バグ報告、要望、質問は Issue を作成してください。
- 修正、改善、ドキュメント更新は Pull Request を歓迎します。
- 追加した Issue / PR テンプレートを使うと、内容を整理しやすくなります。

## ライセンス

このプロジェクトは MIT License で提供されます。詳細は [LICENSE](LICENSE) を参照してください。
