#!/bin/bash
# ダブルクリックで、YouTubeの決算説明会の音声をMP3で「ダウンロード/YouTube音声」に保存する。
# 保存したファイルをCloud版の「YouTube取得診断・ローカルファイル代替」にアップロードして使う。
# Python環境は start_mac.command と共用（リポジトリの外 ~/Library に置く）。

cd "$(dirname "$0")" || exit 1

pause_exit() {
  echo
  read -r -p "Enterキーで閉じます" _
  exit "${1:-1}"
}

if ! command -v ffmpeg >/dev/null 2>&1; then
  echo "ffmpeg が見つかりません。ターミナルで  brew install ffmpeg  を実行してから、もう一度起動してください。"
  pause_exit
fi

APP_HOME="$HOME/Library/Application Support/IRWebcastTranscriber"
VENV="$APP_HOME/venv"
if [ ! -x "$VENV/bin/python" ]; then
  echo "初回セットアップ中（数分かかります）…"
  mkdir -p "$APP_HOME"
  python3 -m venv "$VENV" || pause_exit
fi
REQ_HASH="$(shasum -a 256 requirements.txt | cut -d' ' -f1)"
if [ "$(cat "$APP_HOME/requirements.sha256" 2>/dev/null)" != "$REQ_HASH" ]; then
  echo "必要なパッケージをインストール中…"
  "$VENV/bin/python" -m pip install --upgrade pip >/dev/null
  "$VENV/bin/python" -m pip install -r requirements.txt || pause_exit
  echo "$REQ_HASH" > "$APP_HOME/requirements.sha256"
fi

"$VENV/bin/python" youtube_download.py "$@"
pause_exit $?
