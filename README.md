# IR Webcast Transcriber v21 — 翻訳画面ブラックアウト修正

## 原因
v20では `import translation_page` を使用していたため、Streamlitの再実行時にPythonのモジュールキャッシュが働き、翻訳画面の描画処理が再実行されないことがありました。

## 修正
`runpy.run_path()` で翻訳画面を毎回実行するよう変更しました。ラジオボタンの選択・Google Docs入力・ファイルアップロード後の再実行でも画面が維持されます。

## 更新
ZIP内の `app.py`、`translation_page.py`、`requirements.txt`、`packages.txt` を既存のStreamlitアプリのリポジトリ直下に上書きしてください。Streamlit Cloudで再起動してください。

## 検証
Python構文チェック済み。Streamlit Cloudでの実機動作確認は未実施。
