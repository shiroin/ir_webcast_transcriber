# IR Webcast Transcriber v11 — IR Webcasting対応

Streamlit Cloud向けのIR決算説明会文字起こしアプリです。音声取得はffmpeg、文字起こしはOpenAI Audio Transcriptions APIを利用するため、Cloud上でWhisperモデルをロードしません。

## v11追加
- `irwebcasting.com` のページURLをそのまま入力可能
- IR WebcastingページのHTML・inline script・参照JS/プレイヤー設定から m3u8 / mp4 / mp3 等を探索
- IR Webcastingページから企業名・決算期・説明会日を自動入力（取得できる場合）
- 従来の YouTube / m3u8 / TS / MP3 / MP4 / 一般IRページも継続対応

例:
`https://www.irwebcasting.com/20260814/1/440f7327b9/mov/main/index.html`

このページではページ情報から「GMOペイメントゲートウェイ株式会社」「2026年9月期 第3四半期」「2026-08-14」の取得を試みます。

## Streamlit Community Cloud
1. ZIP内の `app.py`, `requirements.txt`, `packages.txt`, `README.md` をGitHubリポジトリ直下へ上書き
2. Streamlit CloudはPython 3.11を選択
3. App settings → Secrets に以下を設定

```toml
OPENAI_API_KEY = "sk-..."
```

4. Reboot app

## 注意
IR配信サイトの実装は変更される場合があります。Cookie/認証/DRMやJavaScriptで実行時にのみ生成される署名URLの場合、自動取得できないことがあります。その場合はDevTools Networkからm3u8/mp4等を直接入力してください。
