# IR Webcast Transcriber v34 — Earnings Call + Follow-up Call 統合・日本語翻訳・Google Drive保存

## 新機能
「📑 Earnings + Follow-up 統合」画面で、Earnings CallとFollow-up CallのTXT/PDFを各1ファイルアップロードし、**Earnings Call → Follow-up Call**の順に原文全文を結合します。

企業名・決算期・Event Dateを入力すると、原文・日本語訳のGoogleドキュメント名が以下のように生成されます。

- `NVIDIA_FY2027_Q2_2026-08-26_Earnings_and_Followup_Combined`
- `Japanese_Translation_NVIDIA_FY2027_Q2_2026-08-26_Earnings_and_Followup_Combined`

## 操作
1. Earnings CallとFollow-up CallのTXT/PDFをアップロード
2. 企業名・決算期・Event Dateを入力
3. 「統合全文の日本語翻訳を開始／再開」を押す。チャンクごとに全文翻訳。元のメタデータヘッダーをそのまま保持し、本文を翻訳
4. OpenAI APIトークン数と推定API料金を確認
5. Googleアカウントでログインし、Google DriveフォルダURLを入力
6. 「原文と日本語訳の2つをGoogleドキュメントで保存」を押す
7. 同じフォルダにネイティブGoogleドキュメント2件を作成。同名のGoogleドキュメントはスキップ

翻訳に失敗しても、完了したチャンクはStreamlitセッション内に保持され、再開可能です。保存に失敗した場合も再翻訳は不要です。原文・日本語訳TXTのダウンロードも可能です。

## デプロイ
ZIP内の全ファイルをGitHubリポジトリ直下に上書き・追加し、Streamlit Cloudで再起動。既存のGoogle OAuth用SecretsとOpenAI APIキーをそのまま使用。

## 注意
- PDFは文字レイヤーを持つものに対応。画像PDFはOCRが必要
- 翻訳はOpenAI APIの従量課金。画面に表示する料金は推定値
- Googleドキュメントへのインポートで段落や書式が変わる場合があります
- 翻訳前に統合全文をプレビュー可能。元ファイルのメタデータを本文内に保持
- Python構文チェック済み。Google Drive APIへの実書き込み・翻訳APIの実接続は未検証
