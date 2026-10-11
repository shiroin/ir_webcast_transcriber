# IR Webcast Transcriber v23 修正内容

1. Google Drive TXTの正しいURLでも拒否される問題を修正。`urllib.parse` によるURL判定に変更。
2. Google Docs（`/document/d/`）・Drive TXT（`/file/d/`）・Drive `open?id=` を判定。
3. 原文TXT内の `原本ファイル` / `原文ファイル名` を優先して出力名を決定。翻訳TXTは `Japanese_Translation_` で始まります。
4. 元のメタデータを**変更せずにそのまま出力**（翻訳状況の古い記載も改変しないため、翻訳完了の状態はUIと本文セクションで判別）。
5. 既存のIR Webcast文字起こしを保持。

**注意**: Google Driveファイルが非公開の場合、URL判定が成功してもダウンロードには適切なOAuthアクセストークンが必要です。アクセストークンがない場合はTXTを直接アップロードできます。

**更新**: GitHubのアプリ直下に `app.py` と `translation_page.py` を上書きし、Streamlit Cloudで再起動。

**検証**: Python構文チェックとURLパーサー単体テスト実施。Google認証・OpenAI API・Streamlit Cloudの実接続は未確認。
