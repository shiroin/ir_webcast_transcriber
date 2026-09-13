# IR Webcast Transcriber v10 — External API Edition

Streamlit Cloud上ではローカルWhisperモデルを動かさず、音声取得のみを行い、文字起こしはOpenAIの音声文字起こしAPIへ送る省メモリ版です。

## 対応
- YouTube
- MP3 / M4A / AAC / WAV
- M3U8
- TS断片（親M3U8を自動探索。見つからなければ連番TS結合を試行）
- MP4 / WebM
- HTML内にメディアURLが見えるIR webcastページ
- 英語 / 中国語 / 韓国語 / 日本語 / 自動判定
- 企業名 / 決算期 / 説明会日 / 種別タグ
- TXT / MP3ダウンロード

## Streamlit Community Cloudへのデプロイ
1. GitHubリポジトリ直下に `app.py`, `requirements.txt`, `packages.txt`, `README.md` を置く。
2. Streamlit Community Cloudで `app.py` を指定してデプロイ。
3. Python 3.11推奨。
4. App settings → Secrets に次を設定：

```toml
OPENAI_API_KEY = "sk-..."
```

APIキーをGitHubへ直接書かないでください。

## 文字起こし方式
音声は12分ごとに、mono / 16kHz / 32kbps のMP3へ分割してAPIへ逐次送信します。Streamlitプロセス内にWhisperモデルを保持しないため、RAM使用量を大幅に抑えます。

## requirements
`faster-whisper` は不要です。
