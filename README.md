# IR Webcast Transcriber v27 — Googleログイン対応

## 新機能
Google DriveのTXT一括変換、およびGoogle Docs / Drive URLからの全文翻訳で、OAuthアクセストークンの手動入力を廃止しました。Googleアカウントでログインすると、アクセス権のあるGoogle Driveファイルを利用できます。トークンはStreamlitセッション内に保持し、期限切れ時はリフレッシュトークンで更新します。

既存のIR Webcast文字起こし、TXT全文翻訳、API料金表示、`Japanese_Translation_` 出力、原文メタデータ保持、フォルダTXT→Googleドキュメント一括変換を維持します。

## 初回設定（Google Cloud）
1. Google Cloud Consoleでプロジェクトを作成し、Google Drive APIを有効化。
2. Google Auth PlatformでOAuth同意画面を設定。外部アプリで「テスト中」の場合、利用するGoogleアカウントをテストユーザーとして追加。
3. OAuthクライアントを「ウェブアプリケーション」で作成。承認済みリダイレクトURIにStreamlit Cloudの公開URL（例 `https://YOUR-APP.streamlit.app/`）を**完全一致**で登録。
4. Streamlit Cloud → App settings → Secrets に以下を登録（実際の値に置換）：

```toml
GOOGLE_CLIENT_ID = "xxxx.apps.googleusercontent.com"
GOOGLE_CLIENT_SECRET = "xxxx"
GOOGLE_REDIRECT_URI = "https://YOUR-APP.streamlit.app/"
GOOGLE_OAUTH_STATE_SECRET = "32文字以上のランダムな秘密文字列"
```

5. GitHubの `app.py`, `translation_page.py`, `requirements.txt`, `packages.txt` をv27のものに更新し再デプロイ。
6. 「TXT・Google Docs 全文翻訳」→「フォルダ内TXTをGoogleドキュメントに一括変換」→「Googleアカウントでログイン」。

**必要スコープ**：`https://www.googleapis.com/auth/drive`。既存のTXTを一覧・読み込み、同一フォルダにGoogleドキュメントを新規作成するための権限です。GoogleによるOAuthアプリ検証が必要になる場合があります。個人利用・テストではテストユーザー登録で利用可能な場合があります。

**セキュリティ**：OAuthクライアントシークレット・stateシークレットはGitHubにコミットせずStreamlit Secretsへ。アクセストークンやリフレッシュトークンを画面に表示したり、ログに出したりしないでください。Googleの認証URLは公式ドメインです。トークンはアプリサーバー側セッションに保持します。Streamlitセッションが失われた場合は再ログインが必要になる場合があります。アプリ上のログアウトはアプリ内のトークンを破棄するもので、Google側の認可取り消しではありません。

**制限**：指定フォルダ直下のTXTのみ変換。元のTXTは保持。同名のGoogleドキュメントはスキップ。フォルダ一括変換自体は翻訳を行いません。

**検証**：Python構文チェック済み。Google OAuth認証・Drive書き込み・Streamlit Cloudの実動作は未検証。
