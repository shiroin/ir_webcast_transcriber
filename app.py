import os
import re
import sys
import shutil
import subprocess
import tempfile
import time
import io
import zipfile
import csv
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests
import streamlit as st
from bs4 import BeautifulSoup

st.set_page_config(page_title="IR Webcast Transcriber", page_icon="🎧", layout="centered")
if "code" in st.query_params or "error" in st.query_params:
    from google_drive_integration import _google_oauth_callback
    _google_oauth_callback()
page = st.radio("機能を選択", ["📄 PDF → 全文翻訳 → Google Drive", "📑 Earnings + Follow-up 統合", "🎧 IR Webcast 文字起こし", "🌐 TXT・Google Docs 全文翻訳"], horizontal=True, key="ir_page")
if page == "📄 PDF → 全文翻訳 → Google Drive":
    from pdf_pipeline import main as pdf_pipeline_main
    pdf_pipeline_main()
    st.stop()
if page == "📑 Earnings + Follow-up 統合":
    from merge_calls import main as merge_calls_main
    merge_calls_main()
    st.stop()
if page == "🌐 TXT・Google Docs 全文翻訳":
    # Streamlit reruns app.py on every interaction, but Python imports are cached.
    # runpy executes the translation page afresh on every rerun, preventing a blank page.
    import runpy
    from pathlib import Path
    runpy.run_path(str(Path(__file__).resolve().parent / "translation_page.py"), run_name="__main__")
    st.stop()

st.title("🎧 IR Webcast Transcriber")
st.caption("音声取得はStreamlit、文字起こしは外部API。YouTube / Vimeo / SmartVision IR / Gridow / Q4録画リンク / Q4字幕M3U8 / MP3 / M3U8 / TS / IR webcastページ / IR Webcasting、英・中・韓・日に対応。")

UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/151 Safari/537.36"
OPENAI_TRANSCRIBE_URL = "https://api.openai.com/v1/audio/transcriptions"


def run(cmd):
    p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if p.returncode != 0:
        raise RuntimeError((p.stderr or p.stdout)[-6000:])
    return p


def kind(url):
    path = urlparse(url).path.lower()
    if path.endswith(".m3u8"):
        return "m3u8"
    if path.endswith(".ts"):
        return "ts"
    if path.endswith((".mp3", ".m4a", ".aac", ".wav", ".ogg", ".flac")):
        return "audio"
    if path.endswith((".mp4", ".webm", ".mov", ".mkv")):
        return "video"
    return None




def is_q4_recording_link(url):
    """Q4 static.events recording URLs often have no media file extension."""
    parsed = urlparse(url)
    return (parsed.scheme in ("http", "https")
            and parsed.hostname == "static.events.q4inc.com"
            and re.search(r"/videos/[^/]+/videoRecordingLink[^/]*$", parsed.path, re.I) is not None)


def _bounded_command(cmd, timeout=1200):
    """Never keep an unbounded video decoder or stdout buffer alive."""
    try:
        return subprocess.run(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                              stderr=subprocess.PIPE, timeout=timeout, check=True)
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"動画処理が{timeout}秒でタイムアウトしました") from exc
    except subprocess.CalledProcessError as exc:
        err=(exc.stderr or b"").decode("utf-8", errors="replace")
        raise RuntimeError(f"メディア処理エラー: {err[-1200:]}") from exc


def _q4_transcode_stream(url, out_path, referer=None, timeout=1200):
    """Decode only the audio stream; no MP4 download and no in-memory video."""
    from urllib.parse import urlsplit
    parsed=urlsplit(url)
    if parsed.scheme not in ("https", "http"):
        raise ValueError("メディアURLはHTTP(S)である必要があります")
    # Use a finite duration and retry budget, so broken CDN connections do not hang.
    referer=referer or f"{parsed.scheme}://{parsed.netloc}/"
    cmd=["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
         "-rw_timeout", "30000000", "-user_agent", UA,
         "-headers", f"Referer: {referer}\r\n",
         "-reconnect", "1", "-reconnect_streamed", "1",
         "-reconnect_delay_max", "5", "-i", url,
         "-map", "0:a:0", "-vn", "-ac", "1", "-ar", "16000",
         "-c:a", "libmp3lame", "-b:a", "32k", str(out_path)]
    try:
        _bounded_command(cmd,timeout=timeout)
        if not out_path.is_file() or out_path.stat().st_size < 1000:
            raise RuntimeError("抽出した音声が空です")
        return out_path
    except Exception:
        out_path.unlink(missing_ok=True)
        raise


def _q4_yt_dlp_media_urls(url):
    """Extract media URLs without downloading the full video."""
    cmd=[sys.executable,"-m","yt_dlp","--no-playlist","--no-warnings",
         "--skip-download","-f","bestaudio/best","--get-url",url]
    result=subprocess.run(cmd, stdin=subprocess.DEVNULL, capture_output=True,
                          text=True, timeout=90)
    if result.returncode:
        raise RuntimeError((result.stderr or "yt-dlpで解析できません")[-600:])
    return [line.strip() for line in result.stdout.splitlines()
            if line.strip().startswith(("https://","http://"))]


def q4_recording_audio_get(url, out_path):
    """Q4 extensionless links: avoid full video download, with bounded fallbacks."""
    errors=[]
    resolved=url
    content_type=""
    # HEAD does not download the video; some CDNs reject HEAD, so it is optional.
    try:
        with requests.head(url, headers={"User-Agent":UA,"Accept":"*/*"},
                           allow_redirects=True, timeout=(8,15)) as response:
            if response.ok:
                resolved=response.url
                content_type=response.headers.get("Content-Type","").lower()
    except requests.RequestException as exc:
        errors.append(f"HEAD: {str(exc)[-200:]}")

    candidates=[]
    if "text/html" not in content_type and "application/json" not in content_type:
        candidates.append(resolved)
    if yt_dlp_available():
        try:
            candidates.extend(_q4_yt_dlp_media_urls(url))
        except Exception as exc:
            errors.append(f"yt-dlp URL解析: {str(exc)[-400:]}")
    # A Q4 URL can resolve to an HTML player, not an actual media file.
    try:
        if not candidates or "text/html" in content_type or "application/json" in content_type:
            nested,_=discover_nested_player_media(resolved,max_depth=2)
            candidates.extend(nested[:10])
    except Exception as exc:
        errors.append(f"プレイヤー解析: {str(exc)[-400:]}")
    if resolved not in candidates:
        candidates.append(resolved)
    if url not in candidates:
        candidates.append(url)
    for candidate in dict.fromkeys(candidates):
        try:
            return _q4_transcode_stream(candidate,out_path,referer=url)
        except Exception as exc:
            errors.append(f"音声ストリーム: {str(exc)[-450:]}")
    # yt-dlp's audio-only downloader can handle segmented streams that FFmpeg cannot.
    # Unlike the old implementation, never fetch a 1GB+ source MP4 as fallback.
    if yt_dlp_available():
        try:
            temp_dir=out_path.parent / "q4_audio_only"
            temp_dir.mkdir(exist_ok=True)
            template=str(temp_dir / "audio.%(ext)s")
            _bounded_command([sys.executable,"-m","yt_dlp","--no-playlist",
                              "--no-progress","--retries","3","--fragment-retries","5",
                              "--socket-timeout","30","-f","bestaudio/best",
                              "--max-filesize","700M","-o",template,url],timeout=1200)
            sources=[x for x in temp_dir.iterdir() if x.is_file()
                     and not x.name.endswith((".part",".ytdl"))]
            if sources:
                _bounded_command(["ffmpeg","-nostdin","-y","-hide_banner",
                                  "-loglevel","error","-i",str(sources[0]),
                                  "-map","0:a:0","-vn","-ac","1","-ar","16000",
                                  "-c:a","libmp3lame","-b:a","32k",str(out_path)],timeout=1200)
                if out_path.exists() and out_path.stat().st_size>1000:
                    return out_path
        except Exception as exc:
            errors.append(f"音声のみ取得: {str(exc)[-500:]}")
        finally:
            shutil.rmtree(out_path.parent / "q4_audio_only",ignore_errors=True)
    raise RuntimeError("Q4録画リンクから音声を取得できませんでした。\n"
                       "動画全体をメモリに読み込まずに複数方式を試しました。\n"
                       + "\n".join(errors[-6:]))


def is_gridow_page(url):
    host = (urlparse(url).hostname or "").lower()
    return host == "wms.gridow.com" or host.endswith(".gridow.com")


def gridow_audio_get(page_url, out_path):
    """Resolve embedded Gridow webcast media with bounded fallbacks.

    Never download entire videos into Python memory. All media is converted to
    compact MP3, preserving the existing transcription pipeline.
    """
    errors = []
    if yt_dlp_available():
        # yt-dlp can recognize dynamically configured iframe players that do
        # not expose an .m3u8 literal in the parent page HTML.
        try:
            template = str(out_path.with_suffix(".%(ext)s"))
            cmd = [sys.executable, "-m", "yt_dlp", "--no-playlist",
                   "--no-warnings", "--socket-timeout", "25", "--retries", "2",
                   "--fragment-retries", "2", "--referer", page_url,
                   "--user-agent", UA, "-x", "--audio-format", "mp3",
                   "--audio-quality", "5", "-o", template, page_url]
            subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                           text=True, timeout=900, check=True)
            if out_path.exists() and out_path.stat().st_size:
                return out_path
            errors.append("yt-dlpは音声ファイルを生成しませんでした")
        except subprocess.TimeoutExpired:
            errors.append("yt-dlp: 15分の制限時間を超過")
        except subprocess.CalledProcessError as exc:
            errors.append("yt-dlp: " + (exc.stderr or "unknown")[-500:])
        except Exception as exc:
            errors.append("yt-dlp: " + str(exc)[-400:])
        out_path.unlink(missing_ok=True)

    try:
        candidates, player_pages = discover_nested_player_media(page_url, max_depth=3)
        if not candidates:
            candidates = discover_media(page_url)
        for player in player_pages[:6]:
            if yt_dlp_available():
                try:
                    template = str(out_path.with_suffix(".%(ext)s"))
                    cmd = [sys.executable, "-m", "yt_dlp", "--no-playlist",
                           "--socket-timeout", "20", "--retries", "1",
                           "--referer", page_url, "-x", "--audio-format", "mp3",
                           "-o", template, player]
                    subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                                   text=True, timeout=480, check=True)
                    if out_path.exists() and out_path.stat().st_size:
                        return out_path
                except Exception as exc:
                    errors.append("iframe yt-dlp: " + str(exc)[-250:])
                out_path.unlink(missing_ok=True)
        for candidate in candidates[:12]:
            try:
                return ffmpeg_get(candidate, out_path)
            except Exception as exc:
                errors.append("media: " + str(exc)[-350:])
                out_path.unlink(missing_ok=True)
    except Exception as exc:
        errors.append("HTML/iframe探索: " + str(exc)[-450:])
    raise RuntimeError(
        "Gridowの配信音声を自動取得できませんでした。"
        "ページがJavaScript実行・ログイン・地域制限・DRMを必要とする可能性があります。"
        "ブラウザの開発者ツール > Network > Mediaで実際のm3u8/mp4/mp3を確認してください。\n"
        + "\n".join(errors[-5:])
    )


def is_caption_m3u8(url):
    """Q4等の字幕HLS manifestをURLから早期判定。"""
    u = url.lower()
    return u.endswith(".m3u8") and any(x in u for x in ("/captions/", "subtitle", "subtitles", "caption"))


def caption_m3u8_to_text(url):
    """HLS WebVTT字幕manifestを直接テキスト化する。

    subtitles.m3u8 は音声ではないためffmpeg→MP3に渡してはいけない。
    manifestから .vtt / subtitle segmentを取得し、時刻・WEBVTT制御行・HTMLタグを除去する。
    """
    sess = requests.Session()
    headers = {"User-Agent": UA, "Accept": "*/*"}
    r = sess.get(url, headers=headers, timeout=30)
    r.raise_for_status()
    body = r.text
    if "#EXTM3U" not in body:
        raise RuntimeError("字幕URLはM3U8 manifestとして取得できませんでした。")

    # master playlistなら子playlistへ降りる。字幕manifestでは通常1階層だけ。
    lines = [x.strip() for x in body.splitlines() if x.strip()]
    child_m3u8 = [urljoin(url, x) for x in lines if not x.startswith("#") and ".m3u8" in x.lower()]
    if child_m3u8:
        return caption_m3u8_to_text(child_m3u8[0])

    segs = [urljoin(url, x) for x in lines if not x.startswith("#")]
    if not segs:
        raise RuntimeError("字幕M3U8内に字幕セグメントがありません。")

    cues = []
    seen = set()
    for seg in segs:
        rr = sess.get(seg, headers=headers, timeout=30)
        rr.raise_for_status()
        txt = rr.content.decode("utf-8", errors="replace").lstrip("\ufeff")
        # WebVTT cueを行ベースでクリーンアップ。
        for line in txt.splitlines():
            x = line.strip()
            if not x or x == "WEBVTT" or x.startswith(("NOTE", "STYLE", "REGION", "X-TIMESTAMP-MAP")):
                continue
            if "-->" in x or re.fullmatch(r"\d+", x):
                continue
            x = re.sub(r"<[^>]+>", "", x).strip()
            if not x:
                continue
            # HLS字幕は境界で同じcueが再掲されることがあるので連続重複を除く。
            if cues and cues[-1] == x:
                continue
            key = (seg, x)
            if key in seen:
                continue
            seen.add(key)
            cues.append(x)
    if not cues:
        raise RuntimeError("字幕セグメントは取得できましたが、本文を抽出できませんでした。")
    return "\n".join(cues)

def is_youtube(url):
    host = urlparse(url).netloc.lower()
    return "youtube.com" in host or "youtu.be" in host


def is_vimeo(url):
    host = urlparse(url).netloc.lower()
    return host == "vimeo.com" or host.endswith(".vimeo.com") or host == "player.vimeo.com"


def yt_dlp_available():
    p = subprocess.run([sys.executable, "-m", "yt_dlp", "--version"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    return p.returncode == 0


def ffmpeg_available():
    return shutil.which("ffmpeg") is not None


def ffmpeg_get(url, out_path):
    """Stream audio from remote media; never load a large MP4 into Python RAM.

    Some corporate CDNs reject ffmpeg's direct requests. Fall back to a bounded,
    streamed download to disk, then transcode the local file.
    """
    from urllib.parse import urlparse
    parsed = urlparse(url)
    if parsed.scheme not in ("https", "http"):
        raise ValueError("http(s)形式のメディアURLを指定してください。")
    ffmpeg_base = [
        "ffmpeg", "-nostdin", "-y", "-hide_banner", "-loglevel", "error",
    ]
    encode_args = [
        "-map", "0:a:0", "-vn", "-ac", "1", "-ar", "16000",
        "-c:a", "libmp3lame", "-b:a", "32k", str(out_path),
    ]
    direct = ffmpeg_base + [
        "-rw_timeout", "30000000", "-user_agent", UA,
        "-headers", f"Referer: {parsed.scheme}://{parsed.netloc}/\\r\\n",
        "-i", url,
    ] + encode_args
    try:
        subprocess.run(direct, check=True, stdout=subprocess.DEVNULL,
                       stderr=subprocess.PIPE, timeout=1800)
        if out_path.exists() and out_path.stat().st_size > 0:
            return out_path
        raise RuntimeError("変換後の音声が空です")
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, RuntimeError) as exc:
        direct_error = str(exc)[-600:]
        out_path.unlink(missing_ok=True)

    # Fallback: stream to disk in small blocks, not requests.get(...).content.
    # Keep bounded to protect Streamlit Cloud's ephemeral disk.
    local_video = out_path.with_suffix(".source.mp4")
    limit = 1_000_000_000  # 1 GB max temporary download
    try:
        with requests.get(
            url, stream=True, timeout=(20, 90),
            headers={"User-Agent": UA, "Referer": f"{parsed.scheme}://{parsed.netloc}/",
                     "Accept": "*/*"},
        ) as response:
            response.raise_for_status()
            size_header = int(response.headers.get("Content-Length") or 0)
            if size_header > limit:
                raise RuntimeError("動画が1GBを超えます。Streamlit Cloudの容量制限を避けるため中断しました。")
            received = 0
            with local_video.open("wb") as f:
                for block in response.iter_content(chunk_size=1024 * 1024):
                    if not block:
                        continue
                    received += len(block)
                    if received > limit:
                        raise RuntimeError("動画が1GBを超えたため中断しました。")
                    f.write(block)
        subprocess.run(ffmpeg_base + ["-i", str(local_video)] + encode_args,
                       check=True, stdout=subprocess.DEVNULL,
                       stderr=subprocess.PIPE, timeout=1800)
        if not out_path.exists() or out_path.stat().st_size == 0:
            raise RuntimeError("音声の抽出結果が空です")
        return out_path
    except Exception as exc:
        raise RuntimeError(
            "MP4の音声抽出に失敗しました。"
            f"直接変換: {direct_error}; ストリーミング取得: {str(exc)[-600:]}"
        ) from exc
    finally:
        local_video.unlink(missing_ok=True)


def yt_dlp_audio_get(url, out_path):
    """YouTube: use supported JS runtime, EJS, bounded retries and diagnostics."""
    from youtube_runtime import youtube_js_args
    js_args, js_description = youtube_js_args()
    template = str(out_path.with_suffix('.%(ext)s'))
    # Normalize mobile YouTube URLs, remove tracking parameters.
    from urllib.parse import parse_qs
    parsed = urlparse(url)
    if 'youtu.be' in parsed.netloc.lower():
        video_id = parsed.path.strip('/').split('/')[0]
    else:
        video_id = parse_qs(parsed.query).get('v', [''])[0]
    if not re.fullmatch(r'[A-Za-z0-9_-]{11}', video_id):
        raise RuntimeError('YouTubeの動画IDをURLから取得できませんでした。')
    normalized_url = 'https://www.youtube.com/watch?v=' + video_id
    errors = []
    # Do not use format IDs or bypasses that depend on undocumented clients.
    for attempt in range(2):
        cmd = [sys.executable, '-m', 'yt_dlp', '--no-playlist',
               '--no-config', '--no-progress', '--socket-timeout', '25',
               '--retries', '2', '--fragment-retries', '2',
               '--extractor-retries', '2', '--no-part',
               *js_args, '-f', 'bestaudio/best', '-x',
               '--audio-format', 'mp3', '--audio-quality', '5',
               '-o', template, normalized_url]
        try:
            p = subprocess.run(cmd, capture_output=True, text=True, timeout=900)
            if p.returncode == 0 and out_path.is_file() and out_path.stat().st_size > 0:
                return out_path
            message = (p.stderr or p.stdout or '音声が生成されませんでした')[-2000:]
            errors.append(message)
            if '403' in message or 'Sign in' in message or 'bot' in message.lower():
                # Retrying the same request will not fix IP/access restrictions.
                break
        except subprocess.TimeoutExpired:
            errors.append('YouTube音声取得が15分でタイムアウトしました')
        out_path.unlink(missing_ok=True)
    raise RuntimeError('YouTubeの音声取得に失敗しました。JS環境: '+js_description+'。\n'
                       +'\n'.join(errors[-2:])+'\n'
                       +'403が続く場合、Streamlit Cloudの共有IPに対するYouTube側の制限の可能性があります。'
                       +'JS環境だけでは解除できません。')


def vimeo_audio_get(url, out_path):
    """Vimeo専用。

    VimeoのDASH/range配信では、CDNのrange断片URLをffmpegへ直接渡すと
    `trun track id unknown / no tfhd` になることがある。
    そこで、まずyt-dlpのnative downloaderで断片をローカルへ完全取得・結合し、
    その完成済みローカルファイルだけをffmpegへ渡してMP3化する。
    """
    work = out_path.parent / "vimeo_download"
    work.mkdir(exist_ok=True)
    template = str(work / "source.%(ext)s")

    cmd = [
        sys.executable, "-m", "yt_dlp",
        "--no-playlist",
        "--downloader", "http:native",
        "--downloader", "dash,m3u8:native",
        "--retries", "5",
        "--fragment-retries", "10",
        "--abort-on-unavailable-fragments",
        "-f", "bestaudio/best",
        "-o", template,
        url,
    ]
    run(cmd)

    # yt-dlpが作成した完成済みファイルのみを対象にする。
    files = [x for x in work.iterdir() if x.is_file() and not x.name.endswith((".part", ".ytdl"))]
    if not files:
        raise RuntimeError("Vimeoのメディアをローカルへ取得できませんでした。")
    src = max(files, key=lambda x: x.stat().st_size)

    # CDN URLではなくローカルファイルをffmpegに入力するのが重要。
    run([
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-i", str(src), "-vn", "-c:a", "libmp3lame", "-b:a", "64k", str(out_path),
    ])
    if not out_path.exists() or out_path.stat().st_size == 0:
        raise RuntimeError("Vimeo音声のMP3変換に失敗しました。")
    return out_path


def looks_like_m3u8_response(r):
    if r.status_code >= 400:
        return False
    ct = (r.headers.get("content-type") or "").lower()
    head = r.text[:200]
    return "mpegurl" in ct or head.lstrip().startswith("#EXTM3U")


def infer_m3u8_candidates_from_ts(ts_url):
    parsed = urlparse(ts_url)
    path = parsed.path
    directory, filename = path.rsplit("/", 1) if "/" in path else ("", path)
    names = [re.sub(r"\.ts$", ".m3u8", filename, flags=re.I)]
    stem = re.sub(r"\.ts$", "", filename, flags=re.I)
    bases = [stem]
    for pat in (r"_part\d+$", r"_seg(?:ment)?[_-]?\d+$", r"[_-]\d+$"):
        b = re.sub(pat, "", stem, flags=re.I)
        if b != stem:
            bases.append(b)
    for b in bases:
        names.extend([f"{b}.m3u8", f"{b}_playlist.m3u8", f"{b}_index.m3u8"])
    for b in list(bases):
        b2 = re.sub(r"_audio$", "", b, flags=re.I)
        if b2 != b:
            names.extend([f"{b2}.m3u8", f"{b2}_audio.m3u8"])
    out = []
    for name in names:
        new_path = (directory + "/" + name) if directory else name
        candidate = parsed._replace(path=new_path).geturl()
        if candidate not in out:
            out.append(candidate)
    return out


def find_parent_m3u8_from_ts(ts_url):
    headers = {"User-Agent": UA, "Referer": ts_url}
    for candidate in infer_m3u8_candidates_from_ts(ts_url):
        try:
            r = requests.get(candidate, headers=headers, timeout=8)
            if looks_like_m3u8_response(r):
                return candidate
        except requests.RequestException:
            pass
    return None


def ts_sequence_get(first_ts_url, out_path, progress_text=None):
    m = re.search(r"^(.*?)(\d+)(\.ts(?:\?.*)?)$", first_ts_url, re.I)
    if not m:
        raise RuntimeError("TS断片URLから連番パターンを判定できませんでした。m3u8 URLを貼ってください。")
    prefix, start_num, suffix = m.group(1), int(m.group(2)), m.group(3)
    headers = {"User-Agent": UA, "Referer": first_ts_url}
    combined = out_path.with_suffix(".combined.ts")
    count = 0
    misses = 0
    max_segments = 3000
    with open(combined, "wb") as wf:
        for n in range(start_num, start_num + max_segments):
            seg_url = f"{prefix}{n}{suffix}"
            try:
                r = requests.get(seg_url, headers=headers, timeout=15)
            except requests.RequestException:
                r = None
            if r is None or r.status_code >= 400 or not r.content:
                misses += 1
                if count > 0 and misses >= 2:
                    break
                continue
            misses = 0
            wf.write(r.content)
            count += 1
            if progress_text is not None:
                progress_text.caption(f"TS断片を取得中… {count} ファイル")
    if count == 0:
        raise RuntimeError("TS断片を1つも取得できませんでした。")
    ffmpeg_get(str(combined), out_path)
    try:
        combined.unlink()
    except OSError:
        pass
    return out_path, count


def is_irwebcasting(url):
    return "irwebcasting.com" in urlparse(url).netloc.lower()


def extract_irwebcasting_metadata(page_url):
    """Best-effort extraction of company / period / event date from IR Webcasting HTML."""
    r = requests.get(page_url, headers={"User-Agent": UA}, timeout=25)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")
    text = "\n".join(x.strip() for x in soup.stripped_strings if x.strip())
    title = soup.title.get_text(" ", strip=True) if soup.title else ""
    company = ""
    period = ""
    event_date = ""
    # Company often appears first in the page title/body.
    if title:
        company = re.split(r"\s{2,}|\s+-\s+", title)[0].strip()
        company = re.sub(r"\s+(?:20\d{2}年|FY20\d{2}).*$", "", company).strip()
    if not company:
        for line in text.splitlines()[:10]:
            if any(k in line for k in ("株式会社", "Inc.", "Corporation", "Limited", "Ltd.")):
                company = line.strip(); break
    # Typical Japanese title: 2026年9月期 第3四半期 決算説明会
    m = re.search(r"(20\d{2}年\d{1,2}月期\s*(?:第?\s*[1-4１-４]四半期|通期|上期|下期)?)", text)
    if m:
        period = re.sub(r"\s+", " ", m.group(1)).strip()
    else:
        m = re.search(r"(FY\s*20\d{2}\s*(?:Q[1-4]|[1-4]Q)?)", text, re.I)
        if m: period = re.sub(r"\s+", " ", m.group(1)).strip()
    m = re.search(r"(20\d{2})年\s*(\d{1,2})月\s*(\d{1,2})日", text)
    if m:
        event_date = f"{int(m.group(1)):04d}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
    return {"company": company, "period": period, "date": event_date}


def _media_urls_from_text(text, base_url):
    text = (text or "").replace("\\/", "/")
    found = []
    # absolute and protocol-relative media URLs
    abs_re = re.compile(r'(?:(?:https?:)?//)[^\s"\'<>\\]+?\.(?:m3u8|mp3|m4a|aac|mp4|webm|mov)(?:\?[^\s"\'<>\\]*)?', re.I)
    for x in abs_re.findall(text):
        if x.startswith("//"):
            x = urlparse(base_url).scheme + ":" + x
        found.append(x.replace("&amp;", "&"))
    rel_re = re.compile(r'["\']([^"\']+\.(?:m3u8|mp3|m4a|aac|mp4|webm|mov)(?:\?[^"\']*)?)["\']', re.I)
    found += [urljoin(base_url, x.replace("&amp;", "&")) for x in rel_re.findall(text)]
    return found


def discover_irwebcasting_media(page_url):
    """IR Webcasting-specific discovery: HTML + referenced same-site JS/config files."""
    headers = {"User-Agent": UA, "Referer": page_url}
    r = requests.get(page_url, headers=headers, timeout=25)
    r.raise_for_status()
    html = r.text
    soup = BeautifulSoup(html, "html.parser")
    found = _media_urls_from_text(html, page_url)

    # Inspect inline scripts plus external JS/config files. Many IR Webcasting pages
    # keep the movie path in player configuration rather than a visible <video> tag.
    assets = []
    for tag in soup.find_all("script"):
        src = tag.get("src")
        if src:
            assets.append(urljoin(page_url, src))
        else:
            found += _media_urls_from_text(tag.get_text(" ", strip=False), page_url)
    for tag in soup.find_all(["iframe", "source", "video", "audio", "a"]):
        for attr in ("src", "href", "data-src", "data-url", "data-file", "data-movie"):
            v = tag.get(attr)
            if v:
                full = urljoin(page_url, v)
                if kind(full): found.append(full)
                elif "irwebcasting" in urlparse(full).netloc.lower(): assets.append(full)

    # Keep crawling deliberately shallow to avoid scraping the whole site.
    seen_assets = set()
    for asset in assets[:30]:
        if asset in seen_assets: continue
        seen_assets.add(asset)
        try:
            rr = requests.get(asset, headers=headers, timeout=12)
            if rr.status_code < 400 and len(rr.content) < 5_000_000:
                found += _media_urls_from_text(rr.text, asset)
        except requests.RequestException:
            pass

    unique=[]
    for x in found:
        if x not in unique: unique.append(x)
    def score(x):
        lx=x.lower()
        if ".m3u8" in lx: return 0
        if re.search(r"\.(mp3|m4a|aac)", lx): return 1
        return 2
    return sorted(unique, key=score)


def is_smartvision_url(url):
    """SmartVision IR / iVision系のプレイヤー・配信URLをbest-effort判定。"""
    host = urlparse(url).netloc.lower()
    low = url.lower()
    return (
        "ivision.ne.jp" in host
        or "smartvision" in low
        or "smart-vision" in low
    )


def extract_generic_ir_metadata(page_url):
    """一般的な日本企業IR動画ページから企業名・決算期をbest-effort抽出。"""
    try:
        r = requests.get(page_url, headers={"User-Agent": UA}, timeout=20)
        r.raise_for_status()
    except Exception:
        return {"company": "", "period": "", "date": ""}
    soup = BeautifulSoup(r.text, "html.parser")
    title = soup.title.get_text(" ", strip=True) if soup.title else ""
    text = "\n".join(soup.stripped_strings)
    company = ""
    # title末尾のサイト名を落とす。完全自動化より誤入力回避を優先するbest-effort。
    if "サンリオ" in title or "サンリオ" in text[:3000]:
        company = "サンリオ"
    else:
        m = re.search(r"(?:株式会社\s*)?([^|｜\-]{2,40}(?:株式会社|Inc\.|Corporation|Ltd\.))", title)
        if m: company = m.group(1).strip()
    period = ""
    m = re.search(r"(20\d{2}年\s*\d{1,2}月期\s*(?:第?\s*[1-4１-４]四半期|通期|上期|下期)?)", text)
    if m: period = re.sub(r"\s+", " ", m.group(1)).strip()
    event_date = ""
    m = re.search(r"(20\d{2})年\s*(\d{1,2})月\s*(\d{1,2})日", text)
    if m: event_date = f"{int(m.group(1)):04d}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
    return {"company": company, "period": period, "date": event_date}


def discover_nested_player_media(page_url, max_depth=2):
    """SmartVisionのようなiframe/JS埋め込みプレイヤーを浅く再帰探索する。

    親IRページ -> iframe -> player HTML/config/JS -> m3u8/mp4/mp3 の順で探索。
    Cookie/session/DRMが必要な配信は対象外。
    """
    session = requests.Session()
    session.headers.update({"User-Agent": UA})
    queue = [(page_url, 0, page_url)]
    seen = set()
    media = []
    player_pages = []
    while queue and len(seen) < 40:
        current, depth, referer = queue.pop(0)
        if current in seen: continue
        seen.add(current)
        try:
            rr = session.get(current, headers={"Referer": referer}, timeout=15)
            if rr.status_code >= 400 or len(rr.content) > 6_000_000:
                continue
        except requests.RequestException:
            continue
        ctype=(rr.headers.get("content-type") or "").lower()
        if "mpegurl" in ctype or rr.text.lstrip().startswith("#EXTM3U"):
            media.append(current); continue
        text=rr.text.replace("\\/", "/")
        media += _media_urls_from_text(text, current)
        if depth >= max_depth: continue
        soup=BeautifulSoup(text, "html.parser")
        links=[]
        for tag in soup.find_all(["iframe","script","source","video","audio"]):
            for attr in ("src","data-src","data-url","data-file","data-movie","data-player-url"):
                v=tag.get(attr)
                if not v: continue
                full=urljoin(current,v)
                if kind(full): media.append(full)
                elif full.startswith("http"):
                    links.append(full)
                    if tag.name == "iframe": player_pages.append(full)
        # JSON/config URLも拾う。SmartVision実装差分への保険。
        for v in re.findall(r'["\']([^"\']+\.(?:json|js)(?:\?[^"\']*)?)["\']', text, re.I):
            links.append(urljoin(current,v))
        for full in links[:25]:
            if full not in seen:
                queue.append((full, depth+1, current))
    unique=[]
    for x in media:
        if x not in unique: unique.append(x)
    def score(x):
        lx=x.lower()
        if ".m3u8" in lx: return 0
        if re.search(r"\.(mp3|m4a|aac)",lx): return 1
        return 2
    return sorted(unique,key=score), player_pages


def discover_media(page_url):
    if is_irwebcasting(page_url):
        special = discover_irwebcasting_media(page_url)
        if special:
            return special
    # SmartVision等のiframe埋め込みを先に再帰探索。
    nested, _player_pages = discover_nested_player_media(page_url, max_depth=2)
    if nested:
        return nested
    r = requests.get(page_url, headers={"User-Agent": UA}, timeout=25)
    r.raise_for_status()
    html = r.text.replace("\\/", "/")
    soup = BeautifulSoup(html, "html.parser")
    found = []
    for tag in soup.find_all(["audio", "video", "source", "a", "iframe", "script"]):
        for attr in ("src", "href", "data-src", "data-url", "data-file", "data-media", "data-audio"):
            value = tag.get(attr)
            if value:
                full = urljoin(page_url, value)
                if kind(full):
                    found.append(full)
    absolute_re = re.compile(r'https?://[^\s"\'<>\\]+?\.(?:m3u8|mp3|m4a|aac|mp4|webm)(?:\?[^\s"\'<>\\]*)?', re.I)
    found += [x.replace("&amp;", "&") for x in absolute_re.findall(html)]
    relative_re = re.compile(r'["\']([^"\']+\.(?:m3u8|mp3|m4a|aac|mp4|webm)(?:\?[^"\']*)?)["\']', re.I)
    found += [urljoin(page_url, x) for x in relative_re.findall(html)]
    unique = []
    for x in found:
        if x not in unique:
            unique.append(x)
    def score(x):
        lx = x.lower()
        if ".m3u8" in lx:
            return 0
        if re.search(r"\.(mp3|m4a|aac)", lx):
            return 1
        return 2
    return sorted(unique, key=score)


def get_duration_seconds(path):
    p = run([
        "ffprobe", "-v", "error", "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1", str(path),
    ])
    try:
        return float(p.stdout.strip())
    except Exception:
        return 0.0


def make_api_chunks(audio_path, out_dir, chunk_minutes=12):
    """Create small mono 16kHz MP3 chunks to keep API uploads and RAM usage modest."""
    pattern = str(out_dir / "chunk_%03d.mp3")
    run([
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", str(audio_path),
        "-vn", "-ac", "1", "-ar", "16000", "-c:a", "libmp3lame", "-b:a", "32k",
        "-f", "segment", "-segment_time", str(int(chunk_minutes * 60)),
        "-reset_timestamps", "1", pattern,
    ])
    chunks = sorted(out_dir.glob("chunk_*.mp3"))
    if not chunks:
        raise RuntimeError("API送信用の音声分割に失敗しました。")
    return chunks



def translate_to_japanese(transcript, api_key, model="gpt-4o-mini", usage_stats=None):
    """Translate the original transcript in bounded chunks, retaining order and formatting."""
    chunks = []
    remaining = transcript
    while remaining:
        if len(remaining) <= 8500:
            chunks.append(remaining)
            break
        split_at = remaining.rfind("\\n", 0, 8500)
        if split_at < 3500:
            split_at = 8500
        chunks.append(remaining[:split_at])
        remaining = remaining[split_at:]
    translated = []
    for i, chunk in enumerate(chunks):
        last_error = None
        for attempt in range(3):
            try:
                response = requests.post(
                    "https://api.openai.com/v1/chat/completions",
                    headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                    json={
                        "model": model,
                        "temperature": 0,
                        "messages": [
                            {"role": "system", "content": (
                                "Translate the provided earnings-call transcript into accurate, natural Japanese. "
                                "Do not summarize, omit, add or explain. Preserve all figures, company names, "
                                "speaker names, timestamps, paragraph breaks and metadata. "
                                "Translate each part faithfully. Output only the translated text."
                            )},
                            {"role": "user", "content": chunk},
                        ],
                    },
                    timeout=180,
                )
                if response.ok:
                    data = response.json()
                    translated.append(data["choices"][0]["message"]["content"])
                    if usage_stats is not None:
                        usage = data.get("usage", {})
                        usage_stats["input_tokens"] += int(usage.get("prompt_tokens", 0))
                        usage_stats["output_tokens"] += int(usage.get("completion_tokens", 0))
                    break
                last_error = f"翻訳API HTTP {response.status_code}: {response.text[:500]}"
            except Exception as e:
                last_error = str(e)
            if attempt < 2:
                time.sleep(2 * (attempt + 1))
        else:
            raise RuntimeError(f"日本語訳の第{i+1}/{len(chunks)}部分で失敗: {last_error}")
    return "".join(translated)


def bundle_original_and_translation(original, translated, base_name):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(f"{base_name}_transcript.txt", original.encode("utf-8"))
        archive.writestr(f"Japanese_Translation_{base_name}_transcript.txt", translated.encode("utf-8"))
    return buf.getvalue()

def secret_api_key():
    try:
        return str(st.secrets.get("OPENAI_API_KEY", "") or "").strip()
    except Exception:
        return ""


def transcribe_api_chunk(path, api_key, model, language, prompt="", retries=3, usage_stats=None):
    headers = {"Authorization": f"Bearer {api_key}"}
    data = {"model": model, "response_format": "json"}
    if language != "auto":
        data["language"] = language
    if prompt.strip():
        data["prompt"] = prompt.strip()[:1000]

    last_error = None
    for attempt in range(retries):
        try:
            with open(path, "rb") as f:
                files = {"file": (path.name, f, "audio/mpeg")}
                r = requests.post(OPENAI_TRANSCRIBE_URL, headers=headers, data=data, files=files, timeout=900)
            if r.status_code == 200:
                payload = r.json()
                if usage_stats is not None:
                    usage = payload.get("usage", {})
                    if isinstance(usage, dict):
                        usage_stats["input_tokens"] += int(usage.get("input_tokens", 0))
                        usage_stats["output_tokens"] += int(usage.get("output_tokens", 0))
                        usage_stats["usage_reported"] = True
                return (payload.get("text") or "").strip()
            msg = r.text[-3000:]
            last_error = RuntimeError(f"OpenAI API {r.status_code}: {msg}")
            if r.status_code not in (408, 409, 429, 500, 502, 503, 504):
                break
        except requests.RequestException as e:
            last_error = e
        if attempt < retries - 1:
            time.sleep(2 ** attempt * 2)
    raise RuntimeError(str(last_error or "文字起こしAPIへの送信に失敗しました。"))


def fmt_time(seconds):
    seconds = int(max(0, seconds))
    return f"{seconds//3600:02d}:{(seconds%3600)//60:02d}:{seconds%60:02d}"


def transcribe_via_api(audio_path, api_key, model, language, timestamps, prompt, progress_bar, progress_text, usage_stats=None):
    duration = get_duration_seconds(audio_path)
    chunk_minutes = 12
    chunk_seconds = chunk_minutes * 60
    with tempfile.TemporaryDirectory() as cd:
        chunks = make_api_chunks(audio_path, Path(cd), chunk_minutes=chunk_minutes)
        lines = []
        for i, chunk in enumerate(chunks):
            start = i * chunk_seconds
            end = min(duration if duration > 0 else (i + 1) * chunk_seconds, (i + 1) * chunk_seconds)
            progress_text.caption(f"文字起こしAPIへ送信中… {i+1}/{len(chunks)}  |  {fmt_time(start)}〜{fmt_time(end)}")
            text = transcribe_api_chunk(chunk, api_key, model, language, prompt=prompt, usage_stats=usage_stats)
            if text:
                if timestamps:
                    lines.append(f"[{fmt_time(start)} - {fmt_time(end)}]\n{text}")
                else:
                    lines.append(text)
            progress_bar.progress(int(((i + 1) / len(chunks)) * 100))
        progress_text.caption(f"文字起こし 100%  |  API処理完了（{len(chunks)}分割）")
        return "\n\n".join(lines).strip() + "\n"


def safe_filename(text):
    text = (text or "").strip()
    text = re.sub(r'[\\/:*?"<>|]+', "_", text)
    text = re.sub(r"\s+", "_", text)
    return text[:80] or "untagged"


url = st.text_input("URL", placeholder="YouTube / Vimeo / Q4 videoRecordingLink / .mp3 / .m3u8 / .ts / IR Webcasting 等")

auto_meta = {"company":"", "period":"", "date":""}
if url.strip() and is_irwebcasting(url.strip()):
    try:
        auto_meta = extract_irwebcasting_metadata(url.strip())
        st.caption("IR Webcastingを検出：企業名・決算期・説明会日を自動取得します。")
    except Exception as e:
        st.caption(f"IR Webcastingのタグ自動取得に失敗しました（手入力は可能です）: {e}")
elif url.strip():
    # SmartVisionを含む一般IR動画ページでもタグをbest-effort取得。
    try:
        auto_meta = extract_generic_ir_metadata(url.strip())
        if any(auto_meta.values()):
            st.caption("IR動画ページから企業名・決算期などを自動取得しました（必要なら修正できます）。")
    except Exception:
        pass

st.subheader("決算説明会タグ")
m1, m2 = st.columns(2)
with m1:
    company = st.text_input("企業名", value=auto_meta.get("company", ""), placeholder="例: NVIDIA / SK hynix / Palantir")
with m2:
    earnings_period = st.text_input("決算期", value=auto_meta.get("period", ""), placeholder="例: FY2027 Q2 / 2026年2Q")
m3, m4 = st.columns(2)
with m3:
    earnings_date = st.text_input("説明会日", value=auto_meta.get("date", ""), placeholder="例: 2026-08-26")
with m4:
    event_type = st.selectbox("種別", ["決算説明会", "決算発表", "Investor Day", "その他"], index=0)

st.subheader("文字起こしAPI")
st.caption("APIキーはGitHubに書かず、Streamlit CloudのSecretsに OPENAI_API_KEY として保存するのがおすすめです。")
configured_key = secret_api_key()
api_key_input = st.text_input(
    "OpenAI API Key",
    type="password",
    placeholder="Secrets設定済みなら空欄でOK",
    help="この入力値はセッション内でのみ使います。GitHubへ保存しません。",
)
api_key = api_key_input.strip() or configured_key
if configured_key and not api_key_input.strip():
    st.success("Streamlit Secrets の OPENAI_API_KEY を使用します。")

c1, c2 = st.columns(2)
with c1:
    api_model = st.selectbox(
        "文字起こしモデル",
        ["gpt-4o-transcribe", "gpt-4o-mini-transcribe"],
        index=0,
        help="高精度なら gpt-4o-transcribe、軽量・低コスト寄りなら mini。",
    )
with c2:
    language = st.selectbox(
        "言語", ["en", "zh", "ko", "ja", "auto"], index=0,
        format_func=lambda x: {"en":"英語","zh":"中国語（標準中国語）","ko":"韓国語","ja":"日本語","auto":"自動判定"}[x],
    )

prompt = st.text_input(
    "固有名詞ヒント（任意）",
    placeholder="例: NVIDIA, Blackwell, CUDA, Jensen Huang",
    help="企業名・製品名などを入れると固有名詞認識の補助になります。",
)
timestamps = st.checkbox("タイムスタンプ（12分ごとのチャンク単位）", True)

with st.expander("環境チェック", expanded=False):
    st.write(f"Python: `{sys.version.split()[0]}`")
    st.write("ffmpeg:", "✅" if ffmpeg_available() else "❌")
    st.write("yt-dlp:", "✅" if yt_dlp_available() else "❌")
    st.write("API key:", "✅" if api_key else "❌")
    st.write("ローカルWhisper:", "使用しません")

if not ffmpeg_available():
    st.error("ffmpeg が見つかりません。Streamlit Cloudでは packages.txt に ffmpeg が必要です。")

translate_enabled = st.checkbox("原文と日本語訳を両方作成する（翻訳API料金が別途発生）", value=True)
st.subheader("Google DriveへGoogleドキュメントとして保存")
save_drive_enabled = st.checkbox(
    "文字起こし完了後、原文と日本語訳を指定フォルダに保存する",
    value=False, key="transcription_save_drive"
)
if save_drive_enabled:
    from google_drive_integration import google_login_panel
    google_login_panel()
    drive_folder_url = st.text_input(
        "保存先Google DriveフォルダのURL",key="transcription_drive_folder",
        placeholder="https://drive.google.com/drive/u/0/folders/..."
    )
    st.caption("原文と日本語訳を別々のGoogleドキュメントとして作成します。"
               "日本語訳はJapanese_Translation_を名前の先頭に付けます。")
else:
    drive_folder_url = ""


with st.expander("API料金設定（概算・USD）"):
    usd_jpy = st.number_input("USD/JPY", min_value=1.0, value=150.0, step=1.0)
    st.caption("下記は初期参考値です。利用中モデルの最新公式料金に合わせて変更してください。")
    transcribe_usd_min = st.number_input("文字起こし 1分あたり USD（概算）", min_value=0.0, value=0.006 if api_model == "gpt-4o-transcribe" else 0.003, step=0.001, format="%.4f")
    translation_in_per_m = st.number_input("翻訳 入力100万トークンあたり USD", min_value=0.0, value=0.15, step=0.05)
    translation_out_per_m = st.number_input("翻訳 出力100万トークンあたり USD", min_value=0.0, value=0.60, step=0.10)

if "cost_history" not in st.session_state:
    st.session_state.cost_history = []

def display_cost_history():
    if not st.session_state.cost_history:
        return
    st.subheader("今回のセッションのAPI費用履歴")
    st.dataframe(st.session_state.cost_history, use_container_width=True)
    csv_buf = io.StringIO()
    writer = csv.DictWriter(csv_buf, fieldnames=list(st.session_state.cost_history[0].keys()))
    writer.writeheader()
    writer.writerows(st.session_state.cost_history)
    st.download_button("費用履歴をCSVでダウンロード", csv_buf.getvalue().encode("utf-8-sig"), "ir_transcriber_api_cost_history.csv", "text/csv")
    st.caption("履歴は現在のStreamlitセッション内のみ保存。サーバー再起動やセッション終了で消える場合があります。")


if st.button("文字起こし開始", type="primary", use_container_width=True):
    if not url.strip():
        st.warning("URLを貼ってください。")
        st.stop()
    if not api_key:
        st.error("OpenAI API Key が必要です。画面に入力するか、Streamlit Secrets に OPENAI_API_KEY を設定してください。")
        st.stop()
    if (is_youtube(url) or is_vimeo(url)) and not yt_dlp_available():
        st.error("yt-dlp が見つかりません。requirements.txt を確認してください。")
        st.stop()

    transcribe_usage = {"input_tokens": 0, "output_tokens": 0, "usage_reported": False}
    translation_usage = {"input_tokens": 0, "output_tokens": 0}
    billed_audio_minutes = 0.0
    status = st.empty()
    t0 = time.time()
    try:
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            audio = td / "webcast.mp3"
            direct_caption_text = None
            source_kind = kind(url)

            if is_gridow_page(url):
                status.info("Gridowの埋め込みプレイヤーを解析中…（yt-dlp → iframe → HLS/MP4）")
                audio = gridow_audio_get(url, audio)
                source = "Gridow webcast"
            elif is_q4_recording_link(url):
                status.info("Q4録画リンクを解析中…（リダイレクト・動画形式を自動判定）")
                audio = q4_recording_audio_get(url, audio)
                source = "Q4 recording link"
            elif is_caption_m3u8(url):
                status.info("字幕M3U8を検出。音声ではなく既存WebVTT字幕を直接取得中…")
                direct_caption_text = caption_m3u8_to_text(url)
                source = "Q4/HLS subtitles"
            elif is_youtube(url):
                status.info("YouTubeから音声取得中…")
                audio = yt_dlp_audio_get(url, audio)
                source = "YouTube"
            elif is_vimeo(url):
                status.info("Vimeoから音声取得中…（断片をローカル結合してからMP3化）")
                try:
                    audio = vimeo_audio_get(url, audio)
                except Exception as e:
                    raise RuntimeError(
                        "Vimeoから音声を取得できませんでした。公開動画なら通常は取得できますが、"
                        "ログイン必須・パスワード付き・埋め込み先限定の動画ではCookie等が必要な場合があります。\n"
                        + str(e)
                    )
                source = "Vimeo"
            elif source_kind == "ts":
                status.info("TS断片URLを検出。親のM3U8を探索中…")
                parent = find_parent_m3u8_from_ts(url)
                if parent:
                    st.success("親のM3U8を自動検出しました。")
                    st.code(parent)
                    status.info("M3U8から音声取得中…")
                    audio = ffmpeg_get(parent, audio)
                    source = "TS → M3U8"
                else:
                    status.info("親M3U8が見つからないため、TS連番を直接取得して結合します…")
                    ts_progress = st.empty()
                    audio, seg_count = ts_sequence_get(url, audio, progress_text=ts_progress)
                    ts_progress.caption(f"TS断片の取得完了: {seg_count} ファイル")
                    source = f"TS連番 ({seg_count} segments)"
            elif source_kind:
                status.info("音声取得中…")
                audio = ffmpeg_get(url, audio)
                source = source_kind.upper()
            else:
                status.info("IR Webcasting / SmartVision / IRページ内の音声URLを探索中…")
                nested_candidates, player_pages = discover_nested_player_media(url, max_depth=2)
                candidates = nested_candidates or discover_media(url)
                if not candidates and player_pages and yt_dlp_available():
                    # プレイヤーHTML自体をyt-dlpが解決できる実装へのフォールバック。
                    last_player_error = None
                    for player_url in player_pages[:5]:
                        try:
                            status.info("SmartVision系プレイヤーをyt-dlpで解析中…")
                            audio = yt_dlp_audio_get(player_url, audio)
                            source = "SmartVision/iframe → yt-dlp"
                            break
                        except Exception as e:
                            last_player_error = e
                    else:
                        st.error("SmartVision/IRページからメディアを自動検出できませんでした。")
                        if last_player_error:
                            st.code(str(last_player_error))
                        st.info("Cookie・署名URL・DRM型の場合は、DevToolsのNetworkから m3u8 / mp3 / mp4 を取得して貼ってください。")
                        st.stop()
                    candidates = []
                elif not candidates:
                    st.error("自動検出できませんでした。JavaScript / Cookie / 認証型の可能性があります。")
                    st.info("この場合だけDevToolsのNetworkから m3u8 / mp3 / mp4 / ts を取得して貼ってください。")
                    st.stop()
                if candidates:
                    with st.expander("検出したメディアURL"):
                        for x in candidates[:20]:
                            st.code(x)
                    last_error = None
                    for x in candidates[:10]:
                        try:
                            audio = ffmpeg_get(x, audio)
                            source = "IRページ → " + (kind(x) or "media").upper()
                            break
                        except Exception as e:
                            last_error = e
                    else:
                        raise RuntimeError("候補は見つかりましたが音声取得に失敗しました。\n" + str(last_error))

            if direct_caption_text is not None:
                text = direct_caption_text
                status.info(f"{source}: 既存字幕の取得完了。音声文字起こしAPIは使用していません。")
            else:
                duration = get_duration_seconds(audio)
                billed_audio_minutes = max(0.0, duration / 60)
                status.info(f"{source}: 音声取得完了（約{duration/60:.1f}分）。外部APIで文字起こし中…")
                progress_text = st.empty()
                progress_bar = st.progress(0)
                text = transcribe_via_api(
                    audio, api_key, api_model, language, timestamps, prompt,
                    progress_bar=progress_bar, progress_text=progress_text, usage_stats=transcribe_usage,
                )

            tags = []
            if company.strip(): tags.append(f"企業名: {company.strip()}")
            if earnings_period.strip(): tags.append(f"決算期: {earnings_period.strip()}")
            if earnings_date.strip(): tags.append(f"説明会日: {earnings_date.strip()}")
            if event_type: tags.append(f"種別: {event_type}")
            tags.append(f"URL: {url}")
            tags.append(f"Transcription model: {api_model}" if direct_caption_text is None else "Transcript source: existing HLS/WebVTT captions")
            metadata = "# IR Webcast Metadata\n" + "\n".join(f"- {x}" for x in tags)
            transcript_with_tags = metadata + "\n\n# Transcript\n" + text

            company_part = safe_filename(company)
            period_part = safe_filename(earnings_period)
            base_name = f"{company_part}_{period_part}"
            if earnings_date.strip():
                base_name += f"_{safe_filename(earnings_date)}"

            elapsed = (time.time() - t0) / 60
            status.success(f"完了（{elapsed:.1f}分）")
            st.subheader("タグ")
            tag_cols = st.columns(2)
            with tag_cols[0]:
                st.write(f"**企業名:** {company.strip() or '未設定'}")
                st.write(f"**決算期:** {earnings_period.strip() or '未設定'}")
            with tag_cols[1]:
                st.write(f"**説明会日:** {earnings_date.strip() or '未設定'}")
                st.write(f"**種別:** {event_type}")

            st.text_area("Transcript", transcript_with_tags, height=450)
            if translate_enabled:
                status.info("日本語訳を作成中…")
                # Keep metadata deterministic: copy field values from the source verbatim.
                # Translate only the transcript body so the model cannot omit or alter tags.
                translated_body = translate_to_japanese(text, api_key, usage_stats=translation_usage)
                japanese_metadata = "# IR Webcast メタデータ\n" + "\n".join(
                    "- " + line.replace("Transcription model:", "文字起こしモデル:")
                               .replace("Transcript source:", "原文取得元:")
                    for line in tags
                )
                japanese_translation = japanese_metadata + "\n\n# 日本語訳\n" + translated_body
                st.text_area("日本語訳", japanese_translation, height=450)
                zip_bytes = bundle_original_and_translation(transcript_with_tags, japanese_translation, base_name)
                st.download_button(
                    "原文＋日本語訳をまとめてダウンロード（ZIP）",
                    zip_bytes,
                    f"{base_name}_Original_and_Japanese_Translation.zip",
                    "application/zip",
                    use_container_width=True,
                    type="primary",
                )
            if save_drive_enabled:
                st.session_state["pending_drive_transcript"] = {
                    "original": transcript_with_tags,
                    "translated": japanese_translation if translate_enabled else None,
                    "original_name": f"{base_name}_transcript.txt",
                    "translated_name": f"Japanese_Translation_{base_name}_transcript.txt",
                    "source_url": url,
                    "created_at": time.time(),
                }
                st.info("文字起こし・翻訳が完了しました。Google Driveへの自動保存を実行します。")
                try:
                    from google_drive_integration import parse_folder_id, upload_as_google_doc, existing_google_doc
                    folder_id=parse_folder_id(drive_folder_url)
                    if not st.session_state.get("google_drive_tokens"):
                        raise RuntimeError("Googleアカウントでログインしてください。")
                    docs_to_save=[(f"{base_name}_transcript.txt",transcript_with_tags)]
                    if translate_enabled:
                        docs_to_save.append((f"Japanese_Translation_{base_name}_transcript.txt",japanese_translation))
                    for doc_filename,doc_text in docs_to_save:
                        doc_title=doc_filename[:-4]
                        doc=existing_google_doc(folder_id,doc_title)
                        if not doc:
                            doc=upload_as_google_doc(folder_id,doc_filename,doc_text)
                        st.success(f"Google Drive保存完了: {doc_title}")
                        st.markdown(f"[Googleドキュメントを開く](https://docs.google.com/document/d/{doc['id']}/edit)")
                except Exception as drive_exc:
                    st.warning(f"自動保存できませんでした: {drive_exc}。下部から再試行できます。")
            transcription_cost = billed_audio_minutes * transcribe_usd_min
            translation_cost = (
                translation_usage["input_tokens"] * translation_in_per_m
                + translation_usage["output_tokens"] * translation_out_per_m
            ) / 1_000_000
            total_cost = transcription_cost + translation_cost
            st.subheader("API費用（推定）")
            st.metric("今回の推定合計", f"¥{total_cost * usd_jpy:,.2f}", f"${total_cost:.4f}", delta_color="off")
            st.write(f"文字起こし（音声{billed_audio_minutes:.1f}分）: ${transcription_cost:.4f} / 約¥{transcription_cost*usd_jpy:.2f}")
            st.write(f"日本語翻訳（入力{translation_usage['input_tokens']:,} / 出力{translation_usage['output_tokens']:,} tokens）: ${translation_cost:.4f} / 約¥{translation_cost*usd_jpy:.2f}")
            st.caption("翻訳トークン数はAPI応答のusage実測値。文字起こし料金は音声分数×設定単価の概算で、実際の請求額ではありません。")
            st.session_state.cost_history.append({
                "timestamp_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "company": company.strip(), "period": earnings_period.strip(),
                "source_url": url, "transcription_model": api_model,
                "audio_minutes": round(billed_audio_minutes, 3),
                "transcription_input_tokens_reported": transcribe_usage["input_tokens"],
                "transcription_output_tokens_reported": transcribe_usage["output_tokens"],
                "translation_input_tokens": translation_usage["input_tokens"],
                "translation_output_tokens": translation_usage["output_tokens"],
                "transcription_usd_estimate": round(transcription_cost, 6),
                "translation_usd_estimate": round(translation_cost, 6),
                "total_usd_estimate": round(total_cost, 6),
                "usd_jpy": usd_jpy,
                "total_jpy_estimate": round(total_cost * usd_jpy, 2),
            })
            display_cost_history()
            st.download_button(
                "TXTをダウンロード", transcript_with_tags.encode("utf-8"),
                f"{base_name}_transcript.txt", "text/plain", use_container_width=True,
            )
            if direct_caption_text is None and audio.exists():
                if audio.stat().st_size <= 25 * 1024 * 1024:
                    with open(audio, "rb") as af:
                        st.download_button("MP3もダウンロード", af.read(),
                                           f"{base_name}.mp3", "audio/mpeg", use_container_width=True)
                else:
                    st.caption("MP3が25MBを超えるため、Streamlitのメモリ保護のためダウンロード表示を省略しました。")
            elif direct_caption_text is not None:
                st.caption("このURLは字幕ストリームのためMP3はありません。音声が必要な場合はaudio/video側のm3u8を貼ってください。")
    except Exception as e:
        st.error("処理に失敗しました。")
        st.code(str(e))

# Render outside the transcription button so a Streamlit rerun never starts
# another paid transcription just to upload previously generated results.
if st.session_state.get("pending_drive_transcript"):
    result=st.session_state["pending_drive_transcript"]
    st.divider()
    st.subheader("Google Driveにアップロード")
    st.write("原文:",result["original_name"])
    if result["translated"] is not None:
        st.write("日本語訳:",result["translated_name"])
    else:
        st.warning("日本語訳は未生成です。原文のみ保存します。")
    from google_drive_integration import parse_folder_id, upload_as_google_doc, existing_google_doc
    folder_for_upload=st.text_input("保存先フォルダURL",value=st.session_state.get("transcription_drive_folder",""),
                                    key="transcription_upload_folder")
    if st.button("Google Driveにアップロード",type="primary",key="upload_transcripts_drive"):
        try:
            folder_id=parse_folder_id(folder_for_upload)
            if not st.session_state.get("google_drive_tokens"):
                raise RuntimeError("先にGoogleアカウントでログインしてください。")
            items=[(result["original_name"],result["original"])]
            if result["translated"] is not None:
                items.append((result["translated_name"],result["translated"]))
            for filename,content in items:
                title=filename[:-4] if filename.endswith(".txt") else filename
                existing=existing_google_doc(folder_id,title)
                if existing:
                    doc=existing
                    st.info(f"同名のGoogleドキュメントがあるためスキップ: {title}")
                else:
                    doc=upload_as_google_doc(folder_id,filename,content)
                doc_id=doc["id"]
                st.success(f"保存済み: {title}")
                st.markdown(f"[Googleドキュメントを開く](https://docs.google.com/document/d/{doc_id}/edit)")
        except Exception as exc:
            st.error(f"アップロードに失敗しました: {exc}")
    st.caption("アップロードに失敗しても翻訳を再実行する必要はありません。"
               "この画面の結果から再試行できます。同名のGoogleドキュメントは再作成しません。")

with st.expander("対応機能"):
    st.markdown("""
- **SmartVision IR / iframe埋め込み**を追加。親IRページ→iframe→プレイヤーHTML/JS/設定→m3u8/mp4/mp3を浅く再帰探索します。
- **IR Webcasting (`irwebcasting.com`) 専用解析**も継続。
- IR Webcastingでは **企業名・決算期・説明会日を自動入力**します。
- Streamlit Cloud上では **Whisperモデルを一切ロードしません**。
- 取得した音声を12分ごとの軽量MP3に分割し、1本ずつ文字起こしAPIへ送信します。
- そのため、v8/v9で問題になったローカルWhisperのRAM使用量を大幅に減らせます。
- `.ts` は親M3U8の自動探索 → 見つからなければ連番TS結合を試します。
- タイムスタンプはAPI版では **12分チャンク単位** です。
- APIキーはGitHubへコミットしないでください。Streamlit CloudのSecrets利用を推奨します。
""")
