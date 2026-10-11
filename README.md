# IR Webcast Transcriber v26 — requirements.txt 修正

原因: v25のrequirements.txtの最終行にリテラル `\n` が含まれており、pip/uvでインストールに失敗していました。

修正: 各依存関係を正しい改行で1行ずつ記載。packaging.Requirementで全行を解析し、app.py / translation_page.py の構文チェックも実施済み。

既存の全文翻訳、APIコスト表示、Google Drive TXT変換機能は変更していません。

GitHubのリポジトリ直下にある **requirements.txtのみ** をZIP内のものに置き換えるだけで、このインストールエラーは解消します。

実際のStreamlit Cloud上での再デプロイ確認は未実施。
