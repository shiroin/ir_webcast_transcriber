# IR Webcast Transcriber v39 — Gridow配信ページ対応

## 新機能
`https://wms.gridow.com/ir/nanya/nanya_2026Q3.html` のようなGridow IR配信ページを認識し、yt-dlpでページを解析、失敗した場合はiframe/埋め込みプレイヤーの探索とFFmpegによる音声取得を順に試みます。取得した音声は従来の文字起こし→日本語翻訳→Google Drive自動保存に引き渡します。

## 導入
ZIP内の全ファイルを既存のGitHubリポジトリ直下に上書きして、Streamlit Cloudを再起動してください。「🎧 IR Webcast 文字起こし」でGridowのページURLを入力します。

## 注意
配信サイトへの実接続はネットワーク制限により未検証です。認証・地域制限・DRM・ブラウザ専用JS処理が必要な場合は自動取得できません。その場合はStreamlitのエラー詳細またはブラウザ開発者ツールで確認したメディアURLを共有してください。Python構文とZIPの整合性は確認済みです。
