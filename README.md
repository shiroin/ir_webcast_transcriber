# IR Webcast Transcriber v30 — 文字起こし・日本語訳をGoogle Docsで自動保存

## 使い方
1. ZIP内の `app.py`、`translation_page.py`、`google_drive_integration.py`、`requirements.txt`、`packages.txt` をGitHubリポジトリ直下に配置し、Streamlit Cloudを再デプロイ。
2. 既存のStreamlit Secretsに `OPENAI_API_KEY` と、Google OAuth用の `GOOGLE_CLIENT_ID`、`GOOGLE_CLIENT_SECRET`、`GOOGLE_REDIRECT_URI`、`GOOGLE_OAUTH_STATE_SECRET` を設定。
3. 「🎧 IR Webcast 文字起こし」画面で「原文と日本語訳を両方作成する」をオン。
4. 「文字起こし完了後、原文と日本語訳を指定フォルダに保存する」をオン。Googleアカウントでログイン。
5. Google DriveフォルダURLを入力し、「文字起こし開始」。
6. 文字起こし・翻訳が完了すると、Google Driveフォルダ直下に以下の2つの**ネイティブGoogleドキュメント**を自動作成：
   - `<企業名>_<決算期>_<日付>_transcript`
   - `Japanese_Translation_<企業名>_<決算期>_<日付>_transcript`
   Google Docsは拡張子 `.txt` を付けずに作成します。
7. 自動保存に失敗した場合は画面下部の「Google Driveにアップロード」で再試行。再翻訳・再文字起こし不要。既存同名Googleドキュメントは重複作成せずスキップ。

## 注意
- 保存先URLは `https://drive.google.com/drive/u/0/folders/<ID>` など。
- GoogleログインにはDrive全体へのアクセス権限 `https://www.googleapis.com/auth/drive` が必要です。共有フォルダではログインしたアカウントに書き込み権限が必要です。
- 「日本語訳を作成する」がオフなら原文のみ保存。
- 元のTXT形式と同じメタデータヘッダーを両方のGoogle Docs本文に含めます。Google Docsへのインポートは表示上の段落・書式を変える場合があります。
- 自動保存は文字起こし・翻訳が正常に完了した後に行われます。
- OAuthトークンはStreamlitのセッション内に保持。秘密鍵はGitHubにアップロードしないでください。
- `google_drive_integration.py` を必ず一緒にデプロイしてください。
- Google APIへの書き込みを実行するため、Google側の権限設定・OAuth同意画面が必要です。
- Python構文チェック済み。Google OAuthとDrive APIの実接続は未検証。
