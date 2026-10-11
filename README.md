# v28 PDF→メタデータ付きTXT

新しい「PDFをメタデータ付きTXTに変換」タブでPDFをアップロードし、企業名・決算期・イベント日・タイトル・URLを入力。PDFテキストをページ順に抽出し、IR Webcast Metadataヘッダーと # Original Transcript 区切りを付けたUTF-8 TXTをダウンロードできます。続いて「全文日本語翻訳」にアップロードしてください。

PyMuPDFを使用するためOpenAI API料金は発生しません。画像だけのスキャンPDFにはOCRが必要です（本版ではOCRなし）。複雑な段組や表の読み順は確認してください。既存の翻訳・料金表示・Google Drive変換・Googleログインは維持。

GitHubにZIP内のapp.py、translation_page.py、requirements.txt、packages.txtを上書きしてください。Googleログイン設定はv27と同じです。
