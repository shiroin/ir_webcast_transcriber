# IR Webcast Transcriber v37 — Advantest MP4安定化

## 修正
- `https://www2.advantest.com/IR/video/FY2026/1Q/ja/O_QandA_260729.mp4` のような直接MP4 URLの音声取得を強化。
- FFmpegによる直接ストリーミング変換を試し、失敗した場合はHTTPで1MBずつ一時ファイルにダウンロードしてから変換。動画をPythonメモリに丸ごと保持しない。
- 通信タイムアウト・FFmpegの処理時間上限・一時ファイル削除・1GB上限を追加。
- 既存の翻訳・Google Drive自動保存などの機能を維持。

## 導入
ZIPの全ファイルを既存GitHubリポジトリ直下にアップロードしてStreamlit Cloudを再起動。文字起こし画面にMP4 URLをそのまま貼り付けて実行。

## 検証
Python構文検証済み。実際のAdvantest配信URLは実行環境から接続できなかったため、本番での動作は未検証。エラーが続く場合はStreamlit Cloudログの最後の例外と動画の容量・長さを確認してください。動画が1GB超の場合は別の取得方法が必要です。
