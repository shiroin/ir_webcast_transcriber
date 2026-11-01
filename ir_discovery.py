"""Find the latest earnings-call webcast from a company's IR page, and a bookmarklet fallback.

Many IR sites (Q4, Notified, etc.) render players with JavaScript or block server-side
requests (HTTP 403). The crawler is best-effort; the bookmarklet runs in the user's own
browser, where the player has already loaded the real m3u8/mp3/mp4 URL.
"""
import re
from urllib.parse import urljoin, urlparse, quote

import requests
from bs4 import BeautifulSoup

UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/151 Safari/537.36"
MEDIA_RE = re.compile(r"\.(m3u8|mp3|m4a|aac|mp4|webm)(\?|$)", re.I)
STRONG = ("webcast", "earnings call", "conference call", "earnings conference", "replay",
          "決算説明会", "説明会動画", "説明会音声", "音声配信", "動画配信", "q4inc.com", "media-server.com",
          "webcasts.com", "event.on24", "gridow", "irwebcasting", "ivision", "vimeo", "youtube")
WEAK = ("earnings", "quarter", "results", "audio", "listen", "四半期", "決算")
NEGATIVE = ("annual meeting", "stockholders", "shareholder meeting", "株主総会", "privacy", "cookie",
            "login", "subscribe", "email alert", "rss", "linkedin", "twitter", "facebook", "powered by",
            "youtube.com/user/", "youtube.com/channel/", "youtube.com/c/", "youtube.com/@")


def _period_rank(text):
    """Larger means more recent: (year, quarter) parsed from link text/URL."""
    t = text.lower()
    years = [int(y) for y in re.findall(r"(?<!\d)(20\d{2})(?!\d)", t)]
    years += [2000 + int(y) for y in re.findall(r"fy\s?'?(\d{2})(?!\d)", t)]
    quarter = 0
    m = (re.search(r"\bq([1-4])\b", t) or re.search(r"\b([1-4])q\b", t)
         or re.search(r"第\s*([1-4１-４])\s*四半期", t))
    if m:
        quarter = int(m.group(1).translate(str.maketrans("１２３４", "1234")))
    else:
        words = {"first": 1, "second": 2, "third": 3, "fourth": 4}
        m = re.search(r"\b(first|second|third|fourth)[ -]quarter", t)
        if m:
            quarter = words[m.group(1)]
    return (max(years) if years else 0, quarter)


def score_link(text, url):
    blob = f"{text} {url}".lower()
    if any(n in blob for n in NEGATIVE):
        return -1
    if url.lower().endswith(".pdf"):
        # Published transcripts are useful but are not audio.
        return 2 if "transcript" in blob or "call" in blob else -1
    score = 0
    if MEDIA_RE.search(urlparse(url).path):
        score += 8
    score += sum(4 for k in STRONG if k in blob)
    score += sum(1 for k in WEAK if k in blob)
    return score


def find_webcast_candidates(page_url, limit=15):
    """Return [{'url','text','score','kind'}] sorted by recency, then relevance."""
    response = requests.get(page_url, headers={"User-Agent": UA, "Accept-Language": "en,ja;q=0.8"},
                            timeout=25)
    if response.status_code in (401, 403):
        raise PermissionError(
            f"IRページがサーバーからのアクセスを拒否しました（HTTP {response.status_code}）。"
            "ブックマークレットを使ってください。")
    response.raise_for_status()
    soup = BeautifulSoup(response.text, "html.parser")
    seen, found = set(), []
    for tag in soup.find_all(["a", "iframe", "source", "video", "audio"]):
        href = tag.get("href") or tag.get("src") or tag.get("data-src")
        if not href or href.startswith(("#", "javascript:", "mailto:", "tel:")):
            continue
        url = urljoin(page_url, href.strip())
        if not url.startswith("http") or url in seen:
            continue
        seen.add(url)
        # Link text plus the nearest row/card text gives the quarter for "Webcast" buttons.
        own = tag.get_text(" ", strip=True) or tag.get("title") or tag.get("aria-label") or ""
        # Climb until the enclosing card/row names the event (e.g. "Q2 2026 ... Webcast").
        context, parent = own, tag
        for _ in range(5):
            parent = parent.find_parent(["tr", "li", "article", "section", "div"])
            if parent is None:
                break
            context = parent.get_text(" ", strip=True)[:300]
            if _period_rank(context)[0] and len(context) > len(own) + 10:
                break
        context = (own + " | " + context).strip(" |") if context != own else own
        # Relevance from the link itself; the card text is used only for its date.
        score = score_link(own, url)
        if score < 4:
            continue
        found.append({"url": url, "text": context[:160], "own": own, "score": score,
                      "kind": "media" if MEDIA_RE.search(urlparse(url).path) else
                              "pdf" if url.lower().endswith(".pdf") else "page",
                      "rank": _period_rank(context + " " + url)})
    def category(item):
        if item["kind"] == "media":
            return 3
        if any(h in item["url"].lower() for h in STRONG if "." in h) or "webcast" in item["own"].lower():
            return 2
        return 1 if item["kind"] == "page" else 0
    # Stable sort keeps page order (usually newest first) among equally dated links.
    found.sort(key=lambda x: (category(x), x["rank"]), reverse=True)
    return found[:limit]


def bookmarklet(app_url):
    """Bookmarklet: collect media URLs the open player loaded, then open this app pre-filled."""
    app = app_url.rstrip("/")
    js = (
        "(()=>{const A=" + repr(app + "/") + ";"
        "const isM=u=>/\\.(m3u8|mp3|m4a|aac|mp4)(\\?|$)/i.test(u);"
        "const isCap=u=>/caption|subtitle/i.test(u);"
        "let u=performance.getEntriesByType('resource').map(e=>e.name)"
        ".concat([...document.querySelectorAll('video,audio,source')].map(e=>e.currentSrc||e.src||''))"
        ".filter(isM);u=[...new Set(u)];"
        "if(!u.length){alert('音声・動画URLが見つかりません。プレイヤーで再生を開始してから再度クリックしてください。"
        "プレイヤーが別ドメインの枠内にある場合は、その枠を新しいタブで開いてから実行してください。');return}"
        "const p=u.find(x=>/\\.m3u8/i.test(x)&&!isCap(x)&&!/chunk|segment|\\/seg/i.test(x))"
        "||u.find(x=>/\\.(mp3|m4a|aac)/i.test(x))||u.find(x=>!isCap(x))||u[0];"
        "window.open(A+'?media='+encodeURIComponent(p)+'&src='+encodeURIComponent(location.href)"
        "+'&title='+encodeURIComponent(document.title.slice(0,200)),'_blank')})()"
    )
    return "javascript:" + quote(js, safe="()=>{}[];,.'+|!&*/:?-_$@")
