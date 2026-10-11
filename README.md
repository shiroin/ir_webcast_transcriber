# IR Webcast Transcriber v22
- Google Docs URL `/document/d/<id>` と Google Drive TXT URL `/file/d/<id>`、`open?id=<id>` に対応
- Google Drive TXTは公開共有なら直接ダウンロード。非公開はDrive APIでOAuthアクセストークンを使用
- HTMLログイン画面や非テキスト応答はエラーとして表示
- 出力名は `Japanese_Translation_` + 入力ファイル名（Google URLでは `GoogleDrive_<id>.txt` / `GoogleDoc_<id>.txt`）
- メタデータヘッダーは**原文のまま一切書き換えず**保持。翻訳本文は別セクションに配置
- 既存のIR Webcast文字起こし機能は保持
- Python構文チェック済み。クラウド環境での動作は未確認

GitHubリポジトリ直下の `app.py` と `translation_page.py` を上書きしてください。
