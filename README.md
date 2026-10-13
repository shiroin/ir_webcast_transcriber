# IR Webcast Transcriber v35 — 入力値保持・Google Drive自動保存

## 変更内容
- PDF単体翻訳と Earnings Call + Follow-up Call 統合翻訳の両方で、企業名・決算期・Event Date・保存先Google DriveフォルダURLを新しいファイルに切り替えても保持します。
- 画面移動でStreamlitがウィジェットを削除してもセッション内の控えを維持します。
- Google OAuthから戻る際、署名済みstateに非秘密の設定値を含め、フォルダURLとメタデータおよび元の画面を復元します。OAuthコードはURLから除去します。
- Googleログイン・保存先フォルダ設定をファイルアップロードより前に移動しました。先に接続してからファイルを投入してください。
- 日本語翻訳完了後、原文・日本語訳の**2つのGoogleドキュメントを自動保存**します。追加の保存ボタンは不要です。
- 保存結果は処理ジョブ＋保存先フォルダごとに記録し、Streamlitの再実行による重複保存を防ぎます。途中失敗時は「保存のみ再試行」で未完了ファイルから再開できます。同名ドキュメントが既にある場合はスキップします。
- 統合翻訳ファイルに誤って `\\n` が文字列として出力されていた不具合を修正しました。
- ファイル名はメタデータベース。日本語訳には `Japanese_Translation_` を付けます。

## デプロイ
ZIP内の `app.py`, `pdf_pipeline.py`, `merge_calls.py`, `workflow_utils.py`, `google_drive_integration.py`, `translation_core.py`, `translation_page.py`, `requirements.txt`, `packages.txt` をGitHubの既存リポジトリ直下に上書き・追加し、Streamlit Cloudで再デプロイしてください。既存のOpenAI APIキー・Google OAuthのSecretsを使用します。

## 使い方
1. 「Google Drive接続・保存先設定」でGoogleにログインし、フォルダURLを入力。
2. PDFまたはEarnings Call / Follow-up Callをアップロード。
3. 企業名・決算期・Event Dateを入力（次回以降も保持）。
4. 「翻訳開始／再開」を押す。翻訳が完了すると自動的にGoogle Driveに原文と日本語訳を保存。
5. 新しいファイルをアップロードして繰り返す。

## 注意
- 入力値は**Streamlitセッション中**に保持されます。Google OAuthリダイレクト時も署名付きstateから復元します。ブラウザを閉じたりサーバーを再起動した後まで永続保存する機能ではありません。
- 翻訳を開始するまではGoogle Driveへ保存しません。翻訳にはOpenAI APIの従量課金が発生します。
- 保存先Google Driveへの書き込み権限とGoogle OAuth接続が必要です。
- 同名のGoogleドキュメントがある場合は上書きせず、内容更新は行いません。
- スキャンPDFはOCR非対応です。
- Python構文チェックとZIP検証済み。Google API/OpenAI APIへの実接続は未検証です。
