
import re
import sys
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests
import streamlit as st
from bs4 import BeautifulSoup

st.set_page_config(page_title="IR Webcast Transcriber v9", page_icon="🎧", layout="centered")
st.title("🎧 IR Webcast Transcriber v9")
st.caption("YouTube / MP3 / M3U8 / TS / IR webcastページ。英語・中国語・韓国語・日本語に対応。Streamlit Cloud向け省メモリ版。")

UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/151 Safari/537.36"


def run(cmd):
    p = subprocess.run(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
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


def is_youtube(url):
    host = urlparse(url).netloc.lower()
    return "youtube.com" in host or "youtu.be" in host


def yt_dlp_available():
    p = subprocess.run(
        [sys.executable, "-m", "yt_dlp", "--version"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    return p.returncode == 0


def ffmpeg_available():
    return shutil.which("ffmpeg") is not None


def ffmpeg_get(url, out_path):
    run([
        "ffmpeg",
        "-y",
        "-hide_banner",
        "-loglevel", "error",
        "-user_agent", UA,
        "-i", url,
        "-vn",
        "-c:a", "libmp3lame",
        "-q:a", "5",
        str(out_path),
    ])
    return out_path


def youtube_get(url, out_path):
    # IMPORTANT:
    # Do not call "yt-dlp" binary directly.
    # Always use the SAME Python interpreter that is running this Streamlit app.
    template = str(out_path.with_suffix(".%(ext)s"))
    run([
        sys.executable,
        "-m", "yt_dlp",
        "--no-playlist",
        "-x",
        "--audio-format", "mp3",
        "--audio-quality", "5",
        "-o", template,
        url,
    ])

    candidates = list(out_path.parent.glob(out_path.stem + ".mp3"))
    if not candidates:
        raise RuntimeError("YouTube音声ファイルを作成できませんでした。")
    return candidates[0]



def looks_like_m3u8_response(r):
    if r.status_code >= 400:
        return False
    ct = (r.headers.get("content-type") or "").lower()
    head = (r.text[:200] if hasattr(r, "text") else "")
    return "mpegurl" in ct or head.lstrip().startswith("#EXTM3U")


def infer_m3u8_candidates_from_ts(ts_url):
    """Generate likely sibling playlist URLs from a .ts segment URL."""
    parsed = urlparse(ts_url)
    path = parsed.path
    directory, filename = path.rsplit("/", 1) if "/" in path else ("", path)

    names = []
    # Exact extension swap.
    names.append(re.sub(r"\.ts$", ".m3u8", filename, flags=re.I))

    # Common HLS segment naming patterns, e.g. *_audio_part1.ts.
    stem = re.sub(r"\.ts$", "", filename, flags=re.I)
    patterns = [
        (r"_part\d+$", ""),
        (r"_seg(?:ment)?[_-]?\d+$", ""),
        (r"[_-]\d+$", ""),
    ]
    bases = [stem]
    for pat, repl in patterns:
        b = re.sub(pat, repl, stem, flags=re.I)
        if b != stem:
            bases.append(b)
    for b in bases:
        names.extend([f"{b}.m3u8", f"{b}_playlist.m3u8", f"{b}_index.m3u8"])

    # Sometimes the playlist omits an explicit "_audio" suffix.
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
    """Probe likely sibling playlist URLs and return the first valid M3U8."""
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
    """Fallback for *_part1.ts style streams: fetch sequential TS segments and merge."""
    m = re.search(r"^(.*?)(\d+)(\.ts(?:\?.*)?)$", first_ts_url, re.I)
    if not m:
        raise RuntimeError("TS断片URLから連番パターンを判定できませんでした。m3u8 URLを貼ってください。")

    prefix, start_num, suffix = m.group(1), int(m.group(2)), m.group(3)
    # Avoid query-string confusion when constructing numbered siblings.
    if "?" in suffix:
        ext, query = suffix.split("?", 1)
        tail = ext + "?" + query
    else:
        tail = suffix

    headers = {"User-Agent": UA, "Referer": first_ts_url}
    combined = out_path.with_suffix(".combined.ts")
    count = 0
    misses = 0
    max_segments = 3000

    with open(combined, "wb") as wf:
        for n in range(start_num, start_num + max_segments):
            seg_url = f"{prefix}{n}{tail}"
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

def discover_media(page_url):
    r = requests.get(page_url, headers={"User-Agent": UA}, timeout=25)
    r.raise_for_status()
    html = r.text.replace("\\/", "/")
    soup = BeautifulSoup(html, "html.parser")

    found = []

    for tag in soup.find_all(["audio", "video", "source", "a", "iframe", "script"]):
        for attr in ("src", "href", "data-src", "data-url", "data-file", "data-media", "data-audio"):
            value = tag.get(attr)
            if not value:
                continue
            full = urljoin(page_url, value)
            if kind(full):
                found.append(full)

    absolute_re = re.compile(
        r'https?://[^\s"\'<>\\]+?\.(?:m3u8|mp3|m4a|aac|mp4|webm)(?:\?[^\s"\'<>\\]*)?',
        re.I,
    )
    for x in absolute_re.findall(html):
        found.append(x.replace("&amp;", "&"))

    relative_re = re.compile(
        r'["\']([^"\']+\.(?:m3u8|mp3|m4a|aac|mp4|webm)(?:\?[^"\']*)?)["\']',
        re.I,
    )
    for x in relative_re.findall(html):
        found.append(urljoin(page_url, x))

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


def load_model(size):
    # Cloud-safe: do NOT cache the Whisper model in RAM.
    # Streamlit Community Cloud has limited memory, and cached small/medium models
    # can cause the worker process to be killed with the generic "Oh no" page.
    from faster_whisper import WhisperModel
    return WhisperModel(
        size,
        device="cpu",
        compute_type="int8",
        cpu_threads=2,
        num_workers=1,
    )


def fmt_time(seconds):
    seconds = int(max(0, seconds))
    return f"{seconds//3600:02d}:{(seconds%3600)//60:02d}:{seconds%60:02d}"


def transcribe(audio_path, size, language, timestamps, progress_bar=None, progress_text=None):
    model = load_model(size)
    segments, info = model.transcribe(
        str(audio_path),
        language=None if language == "auto" else language,
        vad_filter=True,
        beam_size=5,
    )

    duration = float(getattr(info, "duration", 0) or 0)
    lines = []
    started = time.time()
    last_pct = -1

    for s in segments:
        text = s.text.strip()
        if text:
            if timestamps:
                lines.append(f"[{fmt_time(s.start)} - {fmt_time(s.end)}] {text}")
            else:
                lines.append(text)

        if duration > 0:
            pct = min(99, max(0, int((float(s.end) / duration) * 100)))
            if pct != last_pct:
                if progress_bar is not None:
                    progress_bar.progress(pct)
                if progress_text is not None:
                    elapsed = time.time() - started
                    processed = max(float(s.end), 0.1)
                    speed = elapsed / processed
                    remain_audio = max(duration - float(s.end), 0)
                    eta = remain_audio * speed
                    progress_text.caption(
                        f"文字起こし {pct}%  |  "
                        f"{fmt_time(s.end)} / {fmt_time(duration)}  |  "
                        f"推定残り 約{max(1, int(eta/60 + 0.5))}分"
                    )
                last_pct = pct

    if progress_bar is not None:
        progress_bar.progress(100)
    if progress_text is not None:
        progress_text.caption(f"文字起こし 100%  |  {fmt_time(duration)} / {fmt_time(duration)}")

    # Release model memory as soon as transcription finishes.
    try:
        del segments
        del model
        import gc
        gc.collect()
    except Exception:
        pass

    return "\n".join(lines).strip() + "\n"


def safe_filename(text):
    text = (text or "").strip()
    text = re.sub(r'[\\/:*?"<>|]+', "_", text)
    text = re.sub(r"\s+", "_", text)
    return text[:80] or "untagged"


url = st.text_input(
    "URL",
    placeholder="YouTube / .mp3 / .m3u8 / .ts / Chorus Call / teletogether 等",
)

st.subheader("決算説明会タグ")
m1, m2 = st.columns(2)
with m1:
    company = st.text_input(
        "企業名",
        placeholder="例: NVIDIA / SK hynix / Palantir",
    )
with m2:
    earnings_period = st.text_input(
        "決算期",
        placeholder="例: FY2027 Q2 / 2026年2Q / 2026年12月期 Q3",
    )

m3, m4 = st.columns(2)
with m3:
    earnings_date = st.text_input(
        "説明会日",
        placeholder="例: 2026-08-26",
    )
with m4:
    event_type = st.selectbox(
        "種別",
        ["決算説明会", "決算発表", "Investor Day", "その他"],
        index=0,
    )

c1, c2 = st.columns(2)
with c1:
    model_size = st.selectbox(
        "Whisper",
        ["tiny", "base", "small"],
        index=1,
        help="Streamlit Cloudでは base 推奨。small はメモリ不足で落ちる場合があります。",
    )
with c2:
    language = st.selectbox(
        "言語",
        ["en", "zh", "ko", "ja", "auto"],
        index=0,
        format_func=lambda x: {
            "en": "英語",
            "zh": "中国語（標準中国語）",
            "ko": "韓国語",
            "ja": "日本語",
            "auto": "自動判定",
        }[x],
    )

timestamps = st.checkbox("タイムスタンプ", True)

with st.expander("環境チェック", expanded=False):
    st.write(f"Python: `{sys.executable}`")
    st.write("ffmpeg:", "✅" if ffmpeg_available() else "❌")
    st.write("yt-dlp:", "✅" if yt_dlp_available() else "❌")

if not ffmpeg_available():
    st.error("ffmpeg が見つかりません。setup.command を実行してください。")

if st.button("文字起こし開始", type="primary", use_container_width=True):
    if not url.strip():
        st.warning("URLを貼ってください。")
        st.stop()

    if is_youtube(url) and not yt_dlp_available():
        st.error("このアプリのPython環境に yt-dlp が入っていません。setup.command をもう一度実行してください。")
        st.stop()

    status = st.empty()
    t0 = time.time()

    try:
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            audio = td / "webcast.mp3"
            source_kind = kind(url)

            if is_youtube(url):
                status.info("YouTubeから音声取得中…")
                audio = youtube_get(url, audio)
                source = "YouTube"

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
                status.info("IRページ内の音声URLを探索中…")
                candidates = discover_media(url)

                if not candidates:
                    st.error("自動検出できませんでした。JavaScript / Cookie / 認証型の可能性があります。")
                    st.info("この場合だけDevToolsのNetworkから m3u8 / mp3 / mp4 を取得して貼ってください。")
                    st.stop()

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

            status.info(f"{source}: 音声取得完了。Whisper {model_size} で文字起こし中…")
            progress_text = st.empty()
            progress_bar = st.progress(0)
            progress_text.caption("文字起こし 0%  |  準備中…")

            text = transcribe(
                audio, model_size, language, timestamps,
                progress_bar=progress_bar,
                progress_text=progress_text,
            )

            tags = []
            if company.strip():
                tags.append(f"企業名: {company.strip()}")
            if earnings_period.strip():
                tags.append(f"決算期: {earnings_period.strip()}")
            if earnings_date.strip():
                tags.append(f"説明会日: {earnings_date.strip()}")
            if event_type:
                tags.append(f"種別: {event_type}")
            tags.append(f"URL: {url}")

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

            st.download_button(
                "TXTをダウンロード",
                transcript_with_tags.encode("utf-8"),
                f"{base_name}_transcript.txt",
                "text/plain",
                use_container_width=True,
            )

            st.download_button(
                "MP3もダウンロード",
                audio.read_bytes(),
                f"{base_name}.mp3",
                "audio/mpeg",
                use_container_width=True,
            )

    except Exception as e:
        st.error("処理に失敗しました。")
        st.code(str(e))


with st.expander("対応URL"):
    st.markdown("""
- **YouTube**: `youtube.com/watch...` / `youtu.be/...`
- **直接音声**: `.mp3`, `.m4a`, `.aac`, `.wav`
- **M3U8**
- **TS断片**: `.ts` を貼ると親M3U8を自動探索し、見つからなければ連番TSの直接結合を試行
- **動画**: `.mp4`, `.webm`
- **IR webcastページ**: HTML内のメディアURLを自動探索

YouTube取得時に `yt-dlp` コマンドを直接呼ばず、
**このアプリを動かしているPythonで `python -m yt_dlp` を実行**します。
そのため、PATH違いによる `No such file or directory: 'yt-dlp'` を避けられます。
""")
