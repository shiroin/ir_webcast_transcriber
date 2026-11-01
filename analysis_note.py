"""Quarterly analysis: extract call metadata, write a 4,000+ character analysis, prepend it to 05_決算分析ノート.

Formatting follows 05_決算分析ノート_NVIDIA:
  title     Calibri 26pt #17365d, bottom border #4f81bd
  quarter   HEADING_1 Calibri 14pt bold #366091, 24pt above  「FY2027 Q2｜2026年8月26日｜参照スクリプト：URL」
  sections  HEADING_2 Calibri 13pt bold #4f81bd, 10pt above 「【総括】」「【1．…】」
  body      Noto Sans 10pt #000000, 10pt below, line spacing 115
"""
import json
import re
import time
from datetime import date

import requests
from google_drive_integration import google_api

CHAT_URL = "https://api.openai.com/v1/chat/completions"
DOCS = "https://docs.googleapis.com/v1/documents"
DRIVE = "https://www.googleapis.com/drive/v3/files"
DOC_MIME = "application/vnd.google-apps.document"
MIN_CHARS = 4000

STYLES = {
    "TITLE": {"font": "Calibri", "size": 26, "color": "#17365d", "bold": False, "above": 0, "below": 4},
    "HEADING_1": {"font": "Calibri", "size": 14, "color": "#366091", "bold": True, "above": 24, "below": 0},
    "HEADING_2": {"font": "Calibri", "size": 13, "color": "#4f81bd", "bold": True, "above": 10, "below": 0},
    "NORMAL_TEXT": {"font": "Noto Sans", "size": 10, "color": "#000000", "bold": False, "above": 0, "below": 10},
}
UPDATE_POLICY = ("更新方針：新四半期のJapanese_Translation原稿が追加されたら最新四半期を先頭に追加し、"
                 "過去四半期の分析と参照URLを保持する。数値・発言は各説明会時点の内容。")


# ---------------------------------------------------------------- OpenAI

def _chat_json(api_key, model, system, user, schema_name, schema, usage=None, timeout=600):
    payload = {"model": model, "temperature": 0.2,
               "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
               "response_format": {"type": "json_schema",
                                   "json_schema": {"name": schema_name, "strict": True, "schema": schema}}}
    last = None
    for attempt in range(4):
        try:
            r = requests.post(CHAT_URL, headers={"Authorization": f"Bearer {api_key}"},
                              json=payload, timeout=timeout)
            if r.status_code in (429, 500, 502, 503, 504):
                last = f"HTTP {r.status_code}: {r.text[:300]}"
                time.sleep(min(2 ** attempt * 3, 30))
                continue
            if not r.ok:
                raise RuntimeError(f"OpenAI API HTTP {r.status_code}: {r.text[:600]}")
            data = r.json()
            if usage is not None:
                u = data.get("usage") or {}
                usage["input_tokens"] = usage.get("input_tokens", 0) + int(u.get("prompt_tokens") or 0)
                usage["output_tokens"] = usage.get("output_tokens", 0) + int(u.get("completion_tokens") or 0)
            message = data["choices"][0]["message"]
            if message.get("refusal"):
                raise RuntimeError("モデルが応答を拒否しました: " + message["refusal"])
            return json.loads(message["content"])
        except requests.RequestException as exc:
            last = str(exc)
            time.sleep(min(2 ** attempt * 3, 30))
    raise RuntimeError(f"OpenAI APIの再試行回数を超えました: {last}")


META_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["fiscal_period", "event_date", "event_title", "evidence"],
    "properties": {
        "fiscal_period": {"type": "string"},
        "event_date": {"type": "string"},
        "event_title": {"type": "string"},
        "evidence": {"type": "string"},
    },
}


def extract_call_metadata(api_key, model, company, transcript, hints="", usage=None):
    """Read the fiscal period and call date from the opening of the call (and page/file hints)."""
    head = transcript[:12000]
    tail = transcript[-3000:] if len(transcript) > 15000 else ""
    system = (
        "You extract metadata from an earnings call transcript. Use only facts stated in the transcript or hints. "
        "fiscal_period: the company's own fiscal period being reported, formatted 'FY2027 Q2' "
        "(use the company's fiscal year naming, e.g. NVIDIA's fiscal 2027; full-year/Q4 calls are 'FY2026 Q4'). "
        "For Japanese-style periods you may use '2026年3月期 Q2'. "
        "event_date: the date the call took place, ISO 'YYYY-MM-DD'. Use an explicit date in the transcript or hints; "
        "if only the weekday/'today' is implied and no date is given, return ''. Never guess. "
        "event_title: short title such as 'Q2 FY2027 Earnings Call'. "
        "evidence: the quoted phrase(s) you relied on.")
    user = (f"Company: {company}\nToday's date (for sanity only, not the event date): {date.today().isoformat()}\n"
            f"Hints (page title, URL, file dates):\n{hints or '(none)'}\n\nTranscript opening:\n{head}\n\n"
            + (f"Transcript ending:\n{tail}" if tail else ""))
    result = _chat_json(api_key, model, system, user, "call_metadata", META_SCHEMA, usage)
    if result.get("event_date") and not re.fullmatch(r"20\d{2}-\d{2}-\d{2}", result["event_date"]):
        result["event_date"] = ""
    return result


ANALYSIS_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["summary", "sections"],
    "properties": {
        "summary": {"type": "string"},
        "sections": {"type": "array", "items": {
            "type": "object", "additionalProperties": False, "required": ["title", "body"],
            "properties": {"title": {"type": "string"}, "body": {"type": "string"}}}},
    },
}

ANALYSIS_SYSTEM = """あなたは株式アナリストです。決算説明会スクリプト（日本語訳）だけを根拠に、投資判断に使う四半期分析ノートを日本語で書きます。

要件：
- summary：その四半期の「まとめ」。最重要の数値・変化・経営陣の主張・論点を凝縮した400〜700字程度の段落。
- sections：主要論点を6〜12個の中から内容に応じて合理的な数に分ける。論点の種類は四半期ごとに変えてよい。
  - title：論点を端的に表す見出し（例「HyperscaleとACIEの新分類」）。番号や【】は付けない。
  - body：その論点の本文。具体的な数値（前年同期比・前四半期比・ガイダンス）、製品名、顧客・地域、経営陣の説明、アナリストの質問と回答の要点、その意味合い・検証すべき点を書く。複数段落にする場合は空行で区切る。
- summaryと全sectionsのbodyの合計で、最低4,000字以上。目安は5,000〜7,000字。
- 定型文、同じ内容の言い換え、一般論で文字数を水増しすることは絶対にしない。字数はスクリプトにある具体的な内容（数値、発言、質疑応答）だけで満たす。
- スクリプトにない数値や事実を作らない。会社側の見解は「経営陣は〜と説明した」のように、分析上の評価と区別する。
- 文体は常体（だ・である調）。Markdown記号（#、*、-、|）は使わない。"""


def _analysis_length(result):
    return len(result.get("summary", "")) + sum(len(s.get("body", "")) for s in result.get("sections", []))


def generate_quarter_analysis(api_key, model, company, period, event_date, translated, usage=None):
    user = (f"企業名：{company}\n決算期：{period}\n説明会日：{event_date or '不明'}\n\n"
            f"決算説明会スクリプト（日本語訳）：\n{translated}")
    result = _chat_json(api_key, model, ANALYSIS_SYSTEM, user, "quarter_analysis", ANALYSIS_SCHEMA, usage)
    length, count = _analysis_length(result), len(result.get("sections", []))
    if length < MIN_CHARS or not 6 <= count <= 12:
        # One revision pass: add substance from the transcript, never filler.
        fix = (f"前回の出力は本文{length:,}字・論点{count}個でした。要件（論点6〜12個、合計4,000字以上）を満たすよう、"
               "スクリプトにある具体的な数値・発言・質疑応答の内容を追加して全体を書き直してください。"
               "水増しや言い換えの繰り返しは禁止です。\n\n前回の出力：\n"
               + json.dumps(result, ensure_ascii=False))
        result = _chat_json(api_key, model, ANALYSIS_SYSTEM, user + "\n\n" + fix,
                            "quarter_analysis", ANALYSIS_SCHEMA, usage)
    result["sections"] = result.get("sections", [])[:12]
    result["char_count"] = _analysis_length(result)
    return result


# ---------------------------------------------------------------- Google Docs

def japanese_date(iso):
    m = re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})", iso or "")
    return f"{int(m.group(1))}年{int(m.group(2))}月{int(m.group(3))}日" if m else (iso or "日付不明")


def _utf16_len(text):
    # Docs API indexes are UTF-16 code units.
    return len(text.encode("utf-16-le")) // 2


def _rgb(hex_color):
    h = hex_color.lstrip("#")
    return {"red": int(h[0:2], 16) / 255, "green": int(h[2:4], 16) / 255, "blue": int(h[4:6], 16) / 255}


def _style_requests(start, end, style_name, link=None):
    style = STYLES[style_name]
    requests_ = [
        {"updateParagraphStyle": {"range": {"startIndex": start, "endIndex": end},
                                  "paragraphStyle": {"namedStyleType": style_name},
                                  "fields": "namedStyleType"}},
        {"updateParagraphStyle": {"range": {"startIndex": start, "endIndex": end},
                                  "paragraphStyle": {
                                      "spaceAbove": {"magnitude": style["above"], "unit": "PT"},
                                      "spaceBelow": {"magnitude": style["below"], "unit": "PT"},
                                      "lineSpacing": 115 if style_name != "TITLE" else 100},
                                  "fields": "spaceAbove,spaceBelow,lineSpacing"}},
        {"updateTextStyle": {"range": {"startIndex": start, "endIndex": end},
                             "textStyle": {"weightedFontFamily": {"fontFamily": style["font"]},
                                           "fontSize": {"magnitude": style["size"], "unit": "PT"},
                                           "foregroundColor": {"color": {"rgbColor": _rgb(style["color"])}},
                                           "bold": style["bold"], "italic": False, "underline": False},
                             "fields": "weightedFontFamily,fontSize,foregroundColor,bold,italic,underline"}},
    ]
    if style_name == "TITLE":
        requests_.append({"updateParagraphStyle": {
            "range": {"startIndex": start, "endIndex": end},
            "paragraphStyle": {"borderBottom": {"color": {"color": {"rgbColor": _rgb("#4f81bd")}},
                                                "width": {"magnitude": 1, "unit": "PT"},
                                                "padding": {"magnitude": 4, "unit": "PT"},
                                                "dashStyle": "SOLID"}},
            "fields": "borderBottom"}})
    if link:
        url, offset = link
        url_start = start + offset
        requests_.append({"updateTextStyle": {
            "range": {"startIndex": url_start, "endIndex": url_start + _utf16_len(url)},
            "textStyle": {"link": {"url": url}}, "fields": "link"}})
    return requests_


def build_insert_requests(index, paragraphs):
    """paragraphs: [(style_name, text, link_or_None)] -> one insertText + styling at `index`."""
    text = "".join(p[1] + "\n" for p in paragraphs)
    reqs = [{"insertText": {"location": {"index": index}, "text": text}}]
    pos = index
    for style_name, para, link in paragraphs:
        end = pos + _utf16_len(para) + 1
        reqs += _style_requests(pos, end, style_name, link)
        pos = end
    return reqs


def quarter_paragraphs(period, event_date, script_url, analysis):
    prefix = f"{period}｜{japanese_date(event_date)}｜参照スクリプト："
    paras = [("HEADING_1", prefix + script_url, (script_url, _utf16_len(prefix)) if script_url else None),
             ("HEADING_2", "【総括】", None)]
    paras += [("NORMAL_TEXT", p, None) for p in _split_paragraphs(analysis["summary"])]
    for i, section in enumerate(analysis["sections"], 1):
        title = re.sub(r"^[【\[]?\s*\d+\s*[．.、]\s*|[】\]]$", "", section["title"].strip())
        paras.append(("HEADING_2", f"【{i}．{title}】", None))
        paras += [("NORMAL_TEXT", p, None) for p in _split_paragraphs(section["body"])]
    paras.append(("NORMAL_TEXT", "", None))
    return paras


def _split_paragraphs(text):
    parts = [re.sub(r"[ \t]*\n[ \t]*", "", p).strip() for p in re.split(r"\n\s*\n", text or "")]
    return [p for p in parts if p] or [""]


def _paragraphs(document):
    for element in document.get("body", {}).get("content", []):
        para = element.get("paragraph")
        if not para:
            continue
        text = "".join(r.get("textRun", {}).get("content", "") for r in para.get("elements", []))
        yield element, para.get("paragraphStyle", {}).get("namedStyleType", ""), text.strip()


def find_insert_index(document, period):
    """Index of the newest quarter heading (first non-empty HEADING_1), or end of document.

    Returns (index, already_present, needs_paragraph_break)."""
    first_h1, last = None, None
    for element, style, text in _paragraphs(document):
        last = (element, text)
        if style == "HEADING_1" and text:
            if text.startswith(period + "｜"):
                return element["startIndex"], True, False
            if first_h1 is None:
                first_h1 = element["startIndex"]
    if first_h1 is not None:
        return first_h1, False, False
    if last is None:
        return 1, False, False
    element, text = last
    # Append after the last paragraph; if it has text, end it first so it is not merged.
    return (element["startIndex"], False, False) if not text else (element["endIndex"] - 1, False, True)


def note_title(company):
    return f"05_決算分析ノート_{company}"


def create_note_doc(folder_id, company):
    doc = google_api("POST", DRIVE, params={"supportsAllDrives": "true", "fields": "id,name,webViewLink"},
                     json={"name": note_title(company), "mimeType": DOC_MIME, "parents": [folder_id]})
    reqs = build_insert_requests(1, [("TITLE", note_title(company), None),
                                     ("NORMAL_TEXT", UPDATE_POLICY, None),
                                     ("NORMAL_TEXT", "", None)])
    google_api("POST", f"{DOCS}/{doc['id']}:batchUpdate", json={"requests": reqs})
    return doc["id"]


def drive_metadata(file_id):
    return google_api("GET", f"{DRIVE}/{file_id}", params={
        "fields": "id,name,mimeType,parents,trashed", "supportsAllDrives": "true"})


def find_doc_in_folder(folder_id, title):
    safe = title.replace("\\", "\\\\").replace("'", "\\'")
    data = google_api("GET", DRIVE, params={
        "q": f"'{folder_id}' in parents and name = '{safe}' and mimeType = '{DOC_MIME}' and trashed = false",
        "fields": "files(id)", "supportsAllDrives": "true", "includeItemsFromAllDrives": "true"})
    files = data.get("files") or []
    return files[0]["id"] if files else None


def resolve_note_doc(company, note_url, root_folder_id, transcript_folder_id=None):
    """Return (doc_id, created). A missing, trashed, or non-Google-Docs (.md etc.) note becomes a new Google Doc
    named 05_決算分析ノート_<company> in the company root folder (one level above Quarterly_Analysis)."""
    from google_drive_integration import parse_doc_id
    if note_url:
        try:
            meta = drive_metadata(parse_doc_id(note_url))
            if meta.get("mimeType") == DOC_MIME and not meta.get("trashed"):
                return meta["id"], False
        except (ValueError, RuntimeError):
            pass
    if not root_folder_id and transcript_folder_id:
        parents = drive_metadata(transcript_folder_id).get("parents") or []
        root_folder_id = parents[0] if parents else None
    if not root_folder_id:
        raise RuntimeError("企業ルートフォルダが分からないため、05_決算分析ノートを作成できません。")
    existing = find_doc_in_folder(root_folder_id, note_title(company))
    if existing:
        return existing, True
    return create_note_doc(root_folder_id, company), True


def prepend_quarter(doc_id, period, event_date, script_url, analysis):
    """Insert the quarter above older quarters. Returns 'inserted' or 'exists'."""
    document = google_api("GET", f"{DOCS}/{doc_id}")
    index, present, needs_break = find_insert_index(document, period)
    if present:
        return "exists"
    reqs = []
    if needs_break:
        reqs.append({"insertText": {"location": {"index": index}, "text": "\n"}})
        index += 1
    reqs += build_insert_requests(index, quarter_paragraphs(period, event_date, script_url, analysis))
    google_api("POST", f"{DOCS}/{doc_id}:batchUpdate", json={"requests": reqs}, timeout=120)
    return "inserted"
