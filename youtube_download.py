"""Save a YouTube earnings call's audio as a small MP3 on this Mac, for upload to the Cloud app.

YouTube blocks Streamlit Cloud's shared servers (HTTP 403) but not home networks, so the
download runs here and the Cloud app continues from the uploaded file.

Usage: python youtube_download.py [URL]   (without URL, a dialog asks for it)
"""
import subprocess
import sys
from pathlib import Path

from youtube_runtime import youtube_js_args

OUT_DIR = Path.home() / "Downloads" / "YouTube音声"
UPLOAD_LIMIT_MB = 1024  # .streamlit/config.toml の maxUploadSize


def ask_url():
    script = ('text returned of (display dialog "YouTubeのURLを貼り付けてください" '
              'default answer "" with title "YouTube音声ダウンロード" buttons {"キャンセル", "OK"} '
              'default button "OK")')
    result = subprocess.run(["osascript", "-e", script], capture_output=True, text=True)
    return result.stdout.strip() if result.returncode == 0 else ""


def notify(message):
    subprocess.run(["osascript", "-e", f'display dialog "{message}" with title "YouTube音声ダウンロード" '
                                       'buttons {"OK"} default button "OK"'], capture_output=True)


def main():
    url = (sys.argv[1] if len(sys.argv) > 1 else ask_url()).strip()
    if not url:
        return 1
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    js_args, js_description = youtube_js_args()
    print(f"JS環境: {js_description}")
    # Upload date in the name helps the app determine the call date; 64 kbps mono keeps 1 hour near 30 MB.
    template = str(OUT_DIR / "%(upload_date>%Y-%m-%d)s_%(title).80B.%(ext)s")
    cmd = [sys.executable, "-m", "yt_dlp", "--no-playlist", "--no-config", "--socket-timeout", "25",
           "--retries", "3", "--fragment-retries", "3", *js_args, "-f", "bestaudio/best",
           "-x", "--audio-format", "mp3", "--audio-quality", "64K",
           "--postprocessor-args", "ExtractAudio:-ac 1", "--restrict-filenames",
           "--print", "after_move:filepath", "-o", template, url]
    result = subprocess.run(cmd, stdout=subprocess.PIPE, text=True)
    paths = [line for line in (result.stdout or "").splitlines() if line.strip().endswith(".mp3")]
    if result.returncode or not paths:
        notify("ダウンロードに失敗しました。ターミナルのメッセージを確認してください。")
        return 1
    path = Path(paths[-1].strip())
    size_mb = path.stat().st_size / 1024 / 1024
    print(f"保存しました: {path}（{size_mb:.1f}MB）")
    subprocess.run(["open", "-R", str(path)])
    if size_mb > UPLOAD_LIMIT_MB:
        notify(f"保存しましたが{size_mb:.0f}MBあり、アプリのアップロード上限（{UPLOAD_LIMIT_MB}MB）を超えています。")
    else:
        notify(f"保存しました（{size_mb:.1f}MB）。Finderで表示したファイルをアプリにアップロードしてください。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
