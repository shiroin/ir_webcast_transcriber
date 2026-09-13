# IR Webcast Transcriber v7 - Deploy Ready

## Streamlit Community Cloud

このフォルダの中身をGitHubリポジトリへアップロードし、
Streamlit Community Cloudで `app.py` を指定してデプロイします。

必要ファイル:
- app.py
- requirements.txt
- packages.txt
- runtime.txt

`packages.txt` で ffmpeg をインストールします。

## 注意
- YouTubeはクラウドIP側で制限・bot判定される場合があります。
- 一部のIR webcastサイトはCookie / JavaScript / 地域制限により自動取得できないことがあります。
- 文字起こしはCPU実行のため、長時間音声はローカルMacより遅いことがあります。
- Streamlit Community Cloudの一時ストレージは永続保存ではありません。
