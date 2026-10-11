# IR Webcast Transcriber 統合版 v19

同じStreamlitアプリの上部切替で、**IR Webcast文字起こし**と**TXT / Google Docs全文翻訳**を利用できます。別アプリの起動は不要です。

## 起動

```bash
pip install -r requirements.txt
streamlit run app.py
```

音声処理には ffmpeg が必要です（Streamlit Cloud は packages.txt に指定済み）。翻訳・文字起こしには OpenAI API キーが必要です。既存のStreamlit Secretsの `OPENAI_API_KEY` を引き続き利用できます。

## 追加画面

上部の「🌐 TXT・Google Docs 全文翻訳」を選びます。TXTをアップロード、またはGoogle DocsのURLを入力して全文を日本語訳できます。メタデータは保持し、翻訳済みの本文言語・翻訳状況だけ更新します。出力名は `Japanese_Translation_...txt`。チャンクごとに処理し、同じセッション内で再開可能です。

Google Docs URLからの取得は、公開/共有状態に応じて成功しない場合があります。非公開ドキュメントはOAuthトークン、またはTXT書き出し・アップロードを使用してください。

既存の音声文字起こし機能は元のv18コードを維持しています。
