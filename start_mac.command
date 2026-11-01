#!/bin/bash
# Mac版の起動ファイル（ダブルクリックで実行）。
# YouTubeはStreamlit Cloudの共有サーバーをブロックするが、自宅などの回線からは取得できるため、
# YouTubeの決算説明会はこのMac版で処理する。
# Python環境とSecretsはリポジトリの外（~/Library と ~/.streamlit）に置き、GitHubに上がらないようにする。

cd "$(dirname "$0")" || exit 1

pause_exit() {
  echo
  read -r -p "Enterキーで閉じます" _
  exit "${1:-1}"
}

if [ ! -f workflow_utils.py ]; then
  echo "workflow_utils.py が見つかりません。"
  echo "GitHubのリポジトリを「Code → Download ZIP」でダウンロードし、展開したフォルダの中のこのファイルを実行してください。"
  pause_exit
fi

if ! command -v python3 >/dev/null 2>&1; then
  echo "python3 が見つかりません。https://www.python.org/downloads/ からインストールしてください。"
  pause_exit
fi

if ! command -v ffmpeg >/dev/null 2>&1; then
  echo "ffmpeg が見つかりません。ターミナルで  brew install ffmpeg  を実行してから、もう一度起動してください。"
  pause_exit
fi

SECRETS="$HOME/.streamlit/secrets.toml"
if [ ! -f "$SECRETS" ]; then
  mkdir -p "$HOME/.streamlit"
  cp secrets.example.toml "$SECRETS"
  chmod 600 "$SECRETS"
  echo "Secretsのひな形を作成しました: $SECRETS"
  echo "Streamlit CloudのSecretsの内容を貼り付けて保存してから、もう一度このファイルを起動してください。"
  open -e "$SECRETS"
  pause_exit 0
fi

# Streamlitの初回起動時のメールアドレス入力で止まらないようにする。
if [ ! -f "$HOME/.streamlit/credentials.toml" ]; then
  printf '[general]\nemail = ""\n' > "$HOME/.streamlit/credentials.toml"
fi

APP_HOME="$HOME/Library/Application Support/IRWebcastTranscriber"
VENV="$APP_HOME/venv"
if [ ! -x "$VENV/bin/python" ]; then
  echo "初回セットアップ中（数分かかります）…"
  mkdir -p "$APP_HOME"
  python3 -m venv "$VENV" || pause_exit
fi

# requirements.txt が変わったときだけ再インストールする。
REQ_HASH="$(shasum -a 256 requirements.txt | cut -d' ' -f1)"
if [ "$(cat "$APP_HOME/requirements.sha256" 2>/dev/null)" != "$REQ_HASH" ]; then
  echo "必要なパッケージをインストール中…"
  "$VENV/bin/python" -m pip install --upgrade pip >/dev/null
  "$VENV/bin/python" -m pip install -r requirements.txt || pause_exit
  echo "$REQ_HASH" > "$APP_HOME/requirements.sha256"
fi

echo "アプリを起動します。ブラウザが自動で開きます（開かない場合は http://localhost:8501 ）。"
echo "終了するときは、このウィンドウを閉じてください。"
# リポジトリの .streamlit/config.toml は Cloud 用に headless=true なので、ここで上書きしてブラウザを開く。
exec "$VENV/bin/python" -m streamlit run app.py --server.port 8501 --server.address localhost --server.headless false --browser.gatherUsageStats false
