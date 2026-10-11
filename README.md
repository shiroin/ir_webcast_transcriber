# IR Webcast Transcriber v15

v14 + Q4 Events の字幕HLS (`captions/.../subtitles.m3u8`) 対応。

## v15 修正点
- `subtitles.m3u8` / `/captions/` を音声M3U8として ffmpeg に渡さない。
- HLS manifest を読み、WebVTT字幕セグメントを直接取得してTXT化。
- 既存字幕を使う場合はOpenAI Transcription APIを呼ばない（API費用なし）。
- 字幕URLではMP3ダウンロードを表示しない。
- 通常の音声M3U8は従来どおり ffmpeg → MP3 → OpenAI API。
- YouTube / Vimeo / SmartVision / IR Webcasting / TS等の既存対応は維持。

## 原因
Q4の `.../captions/.../subtitles.m3u8` は音声プレイリストではなく字幕(WebVTT)プレイリストです。v14は拡張子だけで通常M3U8と判定してffmpegでMP3化しようとしていたため、文字起こし経路が不適切でした。


## v16 原文＋日本語訳一括ダウンロード
「原文と日本語訳を両方作成する」をオンにして実行すると、原文TXTと `Japanese_Translation_` を付けた日本語訳TXTを1つのZIPとしてダウンロードできます。翻訳はOpenAI Chat Completions API（gpt-4o-mini）を利用し、文字起こしとは別にAPI料金がかかります。長文は分割翻訳されます。


## v17 API料金表示
文字起こしの音声分数による概算と、翻訳APIが返す実測トークン数からの概算料金を画面表示。セッション内の履歴をCSVでダウンロードできます。料金単価・USDJPYは画面で変更可能。実際の請求額はOpenAI Billingで確認してください。字幕直接取得時は文字起こし料金0。翻訳失敗時には履歴追加されません。

## v18 メタデータ移植
日本語訳には原文と同じ企業名、決算期、説明会日、種別、URL、取得元／文字起こしモデルを確実に含めます。メタデータは機械的にコピーし、本文のみAI翻訳します。
