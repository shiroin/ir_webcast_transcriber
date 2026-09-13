# IR Webcast Transcriber v9 Cloud-safe

Streamlit Community Cloud向け省メモリ版。

主な変更:
- Python 3.11推奨
- Whisperモデルをキャッシュせず、文字起こし時だけロード
- モデル選択は tiny / base / small
- デフォルトは base
- cpu_threads=2 / num_workers=1 でメモリ消費を抑制
- 文字起こし後にモデルを解放
- TS / M3U8 / YouTube / 直接音声 / IRページ探索を維持
- 英語 / 中国語 / 韓国語 / 日本語 / 自動判定

Streamlit Community Cloudでは Advanced settings で Python 3.11 を選択してください。
