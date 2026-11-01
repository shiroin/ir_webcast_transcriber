#!/bin/bash
# Mac版をログイン時に自動起動する（http://localhost:8501 が常に使える状態にする）。
# Cloud版の「Macで自動処理する」ボタンは、この常駐しているMac版を開いて処理する。
# このフォルダの内容を ~/Library/Application Support/IRWebcastTranscriber/app にコピーして動かすので、
# 新しい版をGitHubからダウンロードしたら、もう一度このファイルを実行すれば更新される。

cd "$(dirname "$0")" || exit 1

pause_exit() {
  echo
  read -r -p "Enterキーで閉じます" _
  exit "${1:-1}"
}

if [ ! -f workflow_utils.py ]; then
  echo "workflow_utils.py が見つかりません。GitHubからダウンロードしたフォルダの中で実行してください。"
  pause_exit
fi
if [ ! -f "$HOME/.streamlit/secrets.toml" ]; then
  echo "Secretsがありません。先に start_mac.command を一度実行して、Secretsを設定してください。"
  pause_exit
fi
if ! command -v ffmpeg >/dev/null 2>&1; then
  echo "ffmpeg が見つかりません。ターミナルで  brew install ffmpeg  を実行してください。"
  pause_exit
fi

APP_HOME="$HOME/Library/Application Support/IRWebcastTranscriber"
APP_DIR="$APP_HOME/app"
VENV="$APP_HOME/venv"
LOG_DIR="$APP_HOME/logs"
LABEL="com.irwebcast.transcriber"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
mkdir -p "$APP_DIR" "$LOG_DIR" "$HOME/Library/LaunchAgents"

echo "アプリをコピー中…"
rsync -a --delete --exclude ".git" --exclude ".devcontainer" ./ "$APP_DIR/" || pause_exit

if [ ! -x "$VENV/bin/python" ]; then
  echo "Python環境を作成中（数分かかります）…"
  python3 -m venv "$VENV" || pause_exit
fi
REQ_HASH="$(shasum -a 256 requirements.txt | cut -d' ' -f1)"
if [ "$(cat "$APP_HOME/requirements.sha256" 2>/dev/null)" != "$REQ_HASH" ]; then
  echo "必要なパッケージをインストール中…"
  "$VENV/bin/python" -m pip install --upgrade pip >/dev/null
  "$VENV/bin/python" -m pip install -r requirements.txt || pause_exit
  echo "$REQ_HASH" > "$APP_HOME/requirements.sha256"
fi

# start_mac.command で手動起動したものが残っていればポートが重なるので止める。
pkill -f "streamlit run app.py --server.port 8501" 2>/dev/null

cat > "$PLIST" <<PLISTEOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key>
  <array>
    <string>$VENV/bin/python</string>
    <string>-m</string><string>streamlit</string><string>run</string><string>app.py</string>
    <string>--server.port</string><string>8501</string>
    <string>--server.address</string><string>localhost</string>
    <string>--server.headless</string><string>true</string>
    <string>--browser.gatherUsageStats</string><string>false</string>
  </array>
  <key>WorkingDirectory</key><string>$APP_DIR</string>
  <key>EnvironmentVariables</key>
  <dict>
    <key>PATH</key><string>/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin</string>
    <key>LANG</key><string>ja_JP.UTF-8</string>
  </dict>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>StandardOutPath</key><string>$LOG_DIR/app.log</string>
  <key>StandardErrorPath</key><string>$LOG_DIR/app.log</string>
</dict>
</plist>
PLISTEOF

launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null
launchctl bootstrap "gui/$(id -u)" "$PLIST" || pause_exit

echo "起動を待っています…"
for _ in $(seq 1 60); do
  if curl -s -m 2 http://localhost:8501/_stcore/health | grep -q ok; then
    echo "完了しました。Mac版は今後ログイン時に自動で起動します: http://localhost:8501"
    open "http://localhost:8501"
    pause_exit 0
  fi
  sleep 2
done
echo "起動を確認できませんでした。ログ: $LOG_DIR/app.log"
tail -20 "$LOG_DIR/app.log"
pause_exit
