# IR Webcast Transcriber v42 — 企業マスター連携・決算分析ノート自動追記

v41の「🎧 IR Webcast 文字起こし」画面を、次の1本の流れで動くようにしました。

```
企業を選ぶ（企業マスター）
  → 音声URL（IRページ自動探索 or ブックマークレット）
  → 文字起こし → 決算期・説明会日を自動判定 → 日本語訳
  → Quarterly_Analysis（AC列）に原文と日本語訳をGoogleドキュメントで保存
  → 日本語訳から4,000字以上の分析を作成 → 05_決算分析ノート（AD列）の先頭に追記
```

## 何が自動になったか

| 以前の手作業 | v42 |
|---|---|
| 毎回Googleログイン | Secretsの `GOOGLE_REFRESH_TOKEN` で自動接続（初回のみ設定） |
| 保存先フォルダの指定 | 企業を選ぶとAC列（Quarterly_Analysis）を自動設定 |
| 企業名・決算期・説明会日の入力 | 企業名は企業マスター、決算期・説明会日はスクリプト冒頭からAIで判定（空欄の場合） |
| IRページで音声URLを探す | 企業マスターM列（音声）のページからWebcastリンクを探索。JS型・登録制のプレイヤーはブックマークレット1クリック |
| ChatGPTに要約プロンプトを書く | 翻訳後に自動で要約し、05_決算分析ノートに最新四半期を先頭に追記 |

## 追加・変更ファイル

- `company_registry.py`（新規）：スプレッドシート `global_company_watch_300` の2行目の見出し（企業名／音声／フォルダ／Quarterly_Analysis／05_決算分析ノート 等）で列を特定。列の位置が変わっても動きます。
- `ir_discovery.py`（新規）：IRページのWebcastリンク探索と、ブックマークレットの生成。
- `analysis_note.py`（新規）：決算期・説明会日の判定、分析の生成、Googleドキュメントへの追記。
- `google_drive_integration.py`：`GOOGLE_REFRESH_TOKEN` による自動接続、汎用Google API呼び出し。
- `app.py`：Webcast画面に企業選択・自動判定・ノート追記を追加。`APP_PASSWORD`（任意）。不具合修正2件（下記）。
- `workflow_utils.py` はこのZIPに含まれていません。GitHubにある既存ファイルをそのまま残してください（削除しないこと）。

## 初回セットアップ（1回だけ）

1. **Google Cloud で API を有効化**：OAuthクライアントがあるプロジェクトで、*Google Docs API* と *Google Sheets API* を有効化（Drive APIは有効化済みのはず）。
2. **OAuth同意画面を「本番環境（In production）」にする**：「テスト」のままだとリフレッシュトークンが7日で失効します。個人利用なら未審査のままで構いません（ログイン時に警告が出るだけです）。
3. **アプリを非公開にする**：トークンを保存すると、アプリを開いた人は誰でもあなたのDriveに書き込め、OpenAIの料金も使えてしまいます。Streamlit Community Cloudの *Share* 設定でアプリを非公開にし、閲覧者を自分だけにしてください（Streamlitのログインはブラウザに保持されます）。追加の対策として `APP_PASSWORD` も設定できます。
4. デプロイ後、一度だけ「Googleアカウントでログイン」→ 表示される「次回からログイン不要にする」を開き、`GOOGLE_REFRESH_TOKEN = "..."` をコピー。
5. Streamlit Cloud の Secrets に追記：

```toml
OPENAI_API_KEY = "..."
GOOGLE_CLIENT_ID = "..."
GOOGLE_CLIENT_SECRET = "..."
GOOGLE_REDIRECT_URI = "https://irwebcasttranscriber-2ytgsid2pfpmajk8qrvqvv.streamlit.app"
GOOGLE_OAUTH_STATE_SECRET = "..."
GOOGLE_REFRESH_TOKEN = "..."          # 手順4で取得
APP_URL = "https://irwebcasttranscriber-2ytgsid2pfpmajk8qrvqvv.streamlit.app"   # ブックマークレット用
# APP_PASSWORD = "..."                # 任意。設定するとセッションごとにパスワードを確認
# COMPANY_SHEET_ID / COMPANY_SHEET_NAME  # 企業マスターを変える場合のみ
```

6. ブックマークレットを登録：画面の「🔖 ブックマークレット」を開き、表示されたコードをブラウザのブックマークのURL欄に貼り付けます。

## 毎回の使い方

**A. IRページから探せる企業（例：AMD）**
1. 企業を選択 →「IRページから説明会の音声・Webcastを探す」→ 最新の候補を選び「このURLを使う」
2. 「文字起こし開始」— あとは完了まで自動です。

**B. JS型・登録制のWebcast（例：Notified／Q4のプレイヤー、NVIDIA、Broadcom）**
1. ブラウザでWebcastページを開き、必要なら登録して**再生を開始**
2. ブックマークレットをクリック → 音声URLが入力された状態でアプリが開き、ページタイトルから企業も自動選択されます
3. 「文字起こし開始」

決算期・説明会日は空欄のままで構いません（判定結果と根拠が画面に表示されます）。判定できなかった場合や修正したい場合は、画面下部の「05_決算分析ノートへの追記（再試行）」で修正してから追記できます。再試行時に文字起こし・翻訳はやり直しません。

## 05_決算分析ノートへの書き込み仕様

- 対象：AD列のGoogleドキュメント。AD列が空・ゴミ箱・**.md等のGoogleドキュメント以外**の場合は、企業ルートフォルダ（AA列「フォルダ」＝Quarterly_Analysisの1つ上）に `05_決算分析ノート_企業名` をGoogleドキュメント形式で新規作成し、AD列を新しいURLに書き換えます（元の.mdは削除しません）。
- 位置：既存の最初の四半期見出し（見出し1）の直前＝最新四半期が一番上。同じ決算期の章が既にあれば追記しません（二重追記防止）。
- 構成：`FY2027 Q2｜2026年8月26日｜参照スクリプト：<Japanese_TranslationのURL>`（見出し1）→【総括】→【1．論点】〜（6〜12個、見出し2）。
- 書式：NVIDIAノートに合わせています（見出し1 Calibri 14pt 太字 #366091、見出し2 Calibri 13pt 太字 #4f81bd、本文 Noto Sans 10pt、段落後10pt、行間1.15、タイトル Calibri 26pt #17365d・下罫線）。
- 分量：要約＋全論点で4,000字以上。不足した場合は、スクリプトの具体的内容を追加する形で1回だけ書き直させます（水増しは禁止とプロンプトで指示）。それでも不足した場合は警告を出したうえで追記します。
- 要約モデルは既定で `gpt-4.1`（画面で変更可）。決算期の判定は `gpt-4.1-mini`。

## 不具合修正（v41から）

- 日本語訳のチャンク分割が改行ではなく文字列 `\n`（バックスラッシュ＋n）を探していたため、常に8,500文字で文の途中から分割されていました。改行で分割するように修正。
- MP4/M3U8の直接取得で、ffmpegに渡すRefererヘッダーの末尾が改行コードではなく文字列 `\r\n` になっていました。CDNによってはこれが取得失敗の原因になります。

## 検証状況

- 確認済み：全モジュールの構文チェック。企業マスターの列解析（実際の見出し行で確認）。Docs APIへの挿入リクエストの位置計算（UTF-16、絵文字を含む場合）と挿入位置の判定。AMDのIRページでのWebcast探索（最新四半期から順に4件検出）。ブックマークレット（ブラウザ上で字幕m3u8を避けて音声m3u8を選ぶことを確認）。ブックマークレットからの起動時の自動入力、パスワード画面（Streamlit AppTest）。
- 未確認：OpenAI API・Google Docs/Sheets APIへの実際の接続と、Streamlit Cloud上での一連の動作。初回は、ノートのコピーなど影響の小さい企業で1回試してください。
- NVIDIA／BroadcomのIRページはJavaScriptで描画されるため、自動探索ではWebcastが見つかりません（Bの手順を使用）。Notified（edge.media-server.com）は視聴者登録が必要で、サーバー側からは取得できません。
