# IR Webcast Transcriber v31

PDF → メタデータ付き原文TXT → 全文日本語翻訳 → Google Driveに原文・日本語訳をネイティブGoogleドキュメントとして保存。

デフォルト画面は「PDF → 全文翻訳 → Google Drive」です。企業名、決算期、Event Dateを必須入力します。翻訳のチャンク再開、トークン使用量・概算API料金表示、Googleログイン、指定フォルダ保存に対応します。原文とJapanese_Translation_付きの日本語訳をGoogle Docsで作成します。同名のドキュメントはスキップします。

ZIP内の全ファイルをGitHubリポジトリ直下へ配置し、Streamlit Cloudで再デプロイしてください。新規ファイル pdf_pipeline.py と translation_core.py も必須です。

Google CloudのOAuthクライアントに次のリダイレクトURIを登録し、Streamlit Cloud SecretsのGOOGLE_REDIRECT_URIにも完全一致で設定します：
https://irwebcasttranscriber-2ytgsid2pfpmajk8qrvqvv.streamlit.app/

必要なSecrets: OPENAI_API_KEY, GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET, GOOGLE_REDIRECT_URI, GOOGLE_OAUTH_STATE_SECRET。Google Drive API有効化・保存先フォルダへの書き込み権限も必要です。

PDF文字抽出はPyMuPDF（OCRなし）。画像PDFや複雑な表は抽出できない場合があります。Event Dateは自動推測せず手入力します。PDF変換は無料ですがOpenAI翻訳は従量課金です。翻訳開始とDrive保存はそれぞれボタンを押します。

Python構文チェック済み。実Google OAuth/Drive書き込みと本番Streamlit Cloud上の動作は未検証です。URL先の公開アプリはZIPをデプロイするまで更新されません。
