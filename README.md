# IR Webcast Transcriber v8

StreamlitでIR webcast / YouTube / 直接音声 / HLSを文字起こしするアプリです。

## v8の変更点
- `.ts` 断片URLを直接貼れるように対応
- `.ts` から親の `.m3u8` を推測して自動探索
- 親M3U8が見つからない場合、`*_part1.ts`, `*_part2.ts`... のような連番TSを直接取得・結合するフォールバック
- Whisperのimportを遅延化し、Streamlit Cloud起動時の負荷を軽減
- 既存の英語 / 中国語 / 韓国語 / 日本語 / 自動判定、企業名・決算期・説明会日タグ、文字起こし進捗表示を維持

## Streamlit Community Cloud
Repo rootに以下を置いてください。
- app.py
- requirements.txt
- packages.txt
- README.md

Deploy時の Advanced settings で **Python 3.11** を選択してください。
Main file path は `app.py` です。

## TS URLの例
`https://.../mwsgh7n5_en_enc1_audio_part1.ts`

この形式を貼ると、まず親M3U8を探索し、見つからなければ連番TSの結合を試します。

## 注意
- セッション・Cookie・認証・DRMが必要な配信は自動取得できない場合があります。
- Streamlit CloudではYouTube側のbot対策によりyt-dlpが失敗することがあります。
- `large-v3` はCloudのCPU/RAM負荷が大きいため、まず `small` 推奨です。
