# IR Webcast Transcriber v25 — 翻訳APIコスト表示

既存のWebcast文字起こし・全文翻訳・フォルダTXT→Googleドキュメント変換を保持。

## 新機能
- OpenAI Chat Completionsレスポンスの `usage.prompt_tokens`, `usage.completion_tokens`, `usage.prompt_tokens_details.cached_tokens` をチャンク単位で記録
- 翻訳進行中および完了後に、入力・出力トークン、キャッシュ済み入力、推定API料金（USD/JPY）を表示
- 料金単価はサイドバーで編集可能（USD/100万トークン）。初期値はgpt-4.1-mini参考単価：入力0.40、出力1.60、キャッシュ入力0.10
- USD/JPYは手動入力（自動取得ではない）
- 再開時に完了済みチャンクを再課金として二重計上しない
- 以前のバージョンで翻訳済みのチャンクは文字列から移行し、使用量不明として表示
- 失敗・再試行したAPIリクエストの請求が発生した場合は含まれないことがあります。請求額はOpenAI Usage/Billingが正です。
- ファイル名は `Japanese_Translation_`、メタデータヘッダーはそのまま保持

## 更新方法
ZIP内の `app.py`、`translation_page.py`、`requirements.txt`、`packages.txt` を既存のGitHubリポジトリ直下へ上書きし、Streamlit Cloudを再起動。

Python構文チェック済み。実APIでの課金計測は未検証。
