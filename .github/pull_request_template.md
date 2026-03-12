## 概要 / Summary

<!-- 変更の概要を1〜2行程度で簡潔に記載してください。 -->
<!-- Briefly describe the changes in 1–2 lines. -->

<!-- 例 / Example: ドライランモードのサポートを追加。Claude API を呼ばずに動作確認できるようにする。 -->
<!-- Example: Add dry-run mode support to verify behavior without calling Claude API. -->

- 

## 変更内容 / Changes

<!-- 具体的な実装や制約などを詳細に記載してください。 -->
<!-- Describe the implementation details and constraints. -->

<!-- 例 
- `--dry-run` フラグを追加。指定時は Claude API を呼ばず、レビュー取得・パース・修正案生成までを実行
- `.refix.yaml` の `dry_run` 設定を CLI フラグで上書き可能にした（CLI 優先）
- `argparse` に `--dry-run` を追加し、`main()` で `Config` に渡すよう変更
- `AutoFixer` 内で `dry_run` が True のとき、`_call_claude()` の代わりにモック応答を返す分岐を追加
-->
<!-- Example
- Add `--dry-run` flag. When specified, skips Claude API calls and runs up to review fetch, parse, and fix proposal generation
- `.refix.yaml` `dry_run` config can now be overridden by the CLI flag (CLI takes precedence)
- Added `--dry-run` to `argparse` and pass it to `Config` in `main()`
- In `AutoFixer`, when `dry_run` is True, return mock response instead of calling `_call_claude()`
-->

- 

## テスト / Testing

<!-- 動作確認をした内容を記載してください。 -->
<!-- Describe what you verified. -->

<!-- 例 / Example: `make dry-run` で動作確認済み / Verified with `make dry-run` -->

- 

## 関連 issues / Related issues

<!-- 関連 Issue がない場合は「なし」と記載してください。 -->
<!-- If no related issues, write "none". -->

<!-- 例 / Example: なし / none -->
<!-- 例 / Example: Closes `#123` -->
