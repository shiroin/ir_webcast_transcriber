# IR Webcast Transcriber v32 — メタデータ連動ファイル名

v31のPDF→メタデータ付き原文→全文日本語翻訳→Google DriveのGoogleドキュメント形式で2件保存する機能を維持し、ファイル名をメタデータから生成するように変更。

## ファイル名
企業名、決算期、Event Dateの入力値を使います。
- 原文: `NVIDIA_FY2027_Q2_2026-08-26_Original.txt`
- 日本語訳: `Japanese_Translation_NVIDIA_FY2027_Q2_2026-08-26_Original.txt`

Google Driveには上記から `.txt` を除いた名前の**ネイティブGoogleドキュメント**を作成します。「ファイル名に資料タイトルも含める」を選ぶと日付の後に資料タイトルを挿入します。ファイル名に使用できない記号は置換します。

同じ名前のGoogleドキュメントが既に存在する場合は上書きせずスキップします。PDF本文が更新された場合は古いファイルが残る可能性があるため、既存文書を削除・改名するかファイル名に資料タイトルを追加して区別してください。

## 導入
ZIP内の全ファイルをGitHubリポジトリ直下に上書きし、Streamlit Cloudを再起動します。OAuth・OpenAI Secrets設定はv31と同じです。

Python構文チェック済み。実Google Drive APIでのアップロードは未検証。
