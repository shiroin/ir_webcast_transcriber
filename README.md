# IR Webcast Transcriber v40 — YouTube JavaScript / HTTP 403 改善

## 修正
- yt-dlp の Python パッケージを `yt-dlp[default]` に変更し、EJS チャレンジ解決コンポーネントを同時導入。
- YouTube取得時に Deno 2.3+ / Node.js 22+ を検出。どちらもなければ公式Deno 2.5.6のLinux実行ファイルを一時ディレクトリに自動導入（GitHubへの接続が必要）。導入に失敗したら理由を表示。
- `m.youtube.com/watch?v=...&pp=...` を通常の `www.youtube.com/watch?v=...` に正規化。
- 音声フォーマット優先、タイムアウト・再試行・403診断。403の場合は無意味な連続リトライをしない。
- PDF翻訳、Earnings/Follow-up統合翻訳、Google Docs自動保存など既存機能を維持。

## 導入
ZIPの全ファイルをGitHubリポジトリのルートに上書き/追加し、Streamlit CloudのRebootを実施。`youtube_runtime.py` の追加と `requirements.txt` の更新は必須。

## 注意
- 403はJavaScript実行環境の不足以外にも、YouTube側のネットワーク制限・地域制限・認証・動画固有の制限で発生します。自動導入後も403が出る場合はアプリ画面に表示された詳細を確認してください。
- GitHub Releasesへのアクセスが制限される環境ではDeno自動導入に失敗します。その場合はDeno 2.3+ またはNode.js 22+ をデプロイ環境にインストールしてください。
- Denoのダウンロード先は公式GitHub Releasesの特定バージョンです。実行ファイルのチェックサム検証は行っていません。運用環境では固定ハッシュの検証やビルド時の導入を推奨します。
- Python構文チェック済み。Streamlit Cloud / YouTubeへの実接続は未検証です。
