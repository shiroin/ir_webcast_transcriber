"""Steps shared by the Webcast and PDF pages: company detection, Drive save, analysis-note update."""
import re

import streamlit as st
import company_registry


def _ticker_patterns(ticker):
    """Regexes for a ticker as it is written in titles and transcripts.

    Letter tickers (AMD, NVDA) match as words. Numeric codes (8309, TPE:2317) match only in code form —
    "8309.T", "2317.TW", "(8309)", "TPE:2317", "証券コード8309" — never as a bare number, because filenames
    carry dates and document IDs (Hon_Hai_..._20261112_8309.pdf is not 三井住友トラスト 8309).
    Codes also have to come with their own exchange's marker."""
    ticker = (ticker or "").strip()
    code = ticker.split(":")[-1].strip()
    if not code:
        return []
    if not code.isdigit():
        return [r"(?<![a-z0-9])" + re.escape(code.lower()) + r"(?![a-z0-9])"] if len(code) >= 2 else []
    c = re.escape(code)
    # The same number exists on several exchanges (2371 = Tatung in Taipei, Kakaku.com in Tokyo), so a code
    # only counts with its own exchange's suffix or prefix. No prefix in the master means Tokyo.
    exchange = ticker.split(":")[0].strip().upper() if ":" in ticker else "TSE"
    suffixes, prefixes = {
        "TSE": ("t", "tse|tyo|証券コード"), "TPE": ("tw|two", "tpe|twse|tpex"), "HKG": ("hk", "hkg|hkex"),
        "SHA": ("ss", "sha|sse"), "SHE": ("sz", "she|szse"), "KRX": ("ks|kq", "krx|kosdaq"),
    }.get(exchange, ("t", "tse"))
    patterns = [r"(?<![0-9])" + c + r"\.(?:" + suffixes + r")(?![a-z])",
                r"(?:" + prefixes + r")\s*[:：]?\s*" + c + r"(?![0-9])"]
    if exchange == "TSE":
        patterns.append(r"[（(]\s*" + c + r"\s*[)）]")  # 「三井住友トラスト（8309）」 is a Japanese convention
    return patterns


def guess_company_from_document(companies, title_texts, body_text):
    """Company for a transcript: title/filename matches count most, then mentions in the opening.

    Counting (rather than first match) avoids picking an analyst's firm or a customer named in passing."""
    head = (body_text or "")[:6000].lower()
    titles = " ".join(t for t in title_texts if t).lower()
    best, best_score = None, 0
    for item in companies:
        score = 0
        for name in {item.get("company", ""), item.get("japanese_name", "")}:
            name = name.strip().lower()
            if len(name) < 3:
                continue
            pattern = r"(?<![a-z0-9])" + re.escape(name) + r"(?![a-z0-9])"
            score += 5 * len(re.findall(pattern, titles)) + len(re.findall(pattern, head))
        for pattern in _ticker_patterns(item.get("ticker")):
            if re.search(pattern, titles):
                score += 5
            elif re.search(pattern, head[:1500]):
                score += 3
        if score > best_score:
            best, best_score = item, score
    return best if best_score >= 2 else None


def guess_company_with_ai(api_key, companies, title_texts, body_text, model="gpt-4.1-mini"):
    """Fallback when names do not match literally (English "Hon Hai" vs master "鴻海精密工業").

    Shows the model the master list and the transcript opening; accepts only a row that exists."""
    import json
    import requests
    listing = "\n".join(f"{c['row']}|{c.get('company', '')}|{c.get('japanese_name', '')}|{c.get('ticker', '')}"
                         for c in companies)
    schema = {"type": "object", "additionalProperties": False, "required": ["row", "reason"],
              "properties": {"row": {"type": "integer"}, "reason": {"type": "string"}}}
    payload = {"model": model, "temperature": 0,
               "response_format": {"type": "json_schema", "json_schema": {"name": "company", "strict": True, "schema": schema}},
               "messages": [
                   {"role": "system", "content": "Identify which company held this earnings call. Pick the row number from the "
                    "company list (row|company|Japanese name|ticker). Names may differ in language or form (e.g. Hon Hai / "
                    "Foxconn = 鴻海精密工業). The reporting company is the one presenting results, not analysts, banks or "
                    "customers mentioned. Return row 0 if it is not in the list."},
                   {"role": "user", "content": "Company list:\n" + listing + "\n\nFile/title: " +
                    " / ".join(t for t in title_texts if t) + "\n\nTranscript opening:\n" + (body_text or "")[:3000]}]}
    r = requests.post("https://api.openai.com/v1/chat/completions", headers={"Authorization": f"Bearer {api_key}"},
                      json=payload, timeout=120)
    r.raise_for_status()
    row = json.loads(r.json()["choices"][0]["message"]["content"]).get("row")
    return next((c for c in companies if c["row"] == row), None)


def master_company_picker(key, fill, label="企業（企業マスターから選ぶと保存先フォルダを自動入力）"):
    """Selectbox over the company master. Choosing a company copies its fields into other widgets' session keys,
    e.g. fill={"merge_folder": "transcript_folder"}; those inputs stay editable. Returns the chosen row or None."""
    from google_drive_integration import ensure_google_auth
    if not ensure_google_auth():
        return None
    try:
        companies = company_registry.load_companies()
    except Exception as exc:
        st.warning(f"企業マスター（スプレッドシート）を読み込めません: {exc}")
        return None
    labels = [company_registry.label(c) for c in companies]

    def apply():
        choice = st.session_state.get(key)
        if choice in labels:
            item = companies[labels.index(choice)]
            for state_key, field in fill.items():
                # Also the "pref_" shadow kept by workflow_utils.persistent_text, so the value survives reruns.
                st.session_state[state_key] = st.session_state["pref_" + state_key] = item.get(field, "")

    choice = st.selectbox(label, ["（選択しない）"] + labels, key=key, on_change=apply)
    return companies[labels.index(choice)] if choice in labels else None


def save_docs_once(folder_id, documents):
    """Create each Google Doc unless one with the same title already exists. Returns {title: id}."""
    from google_drive_integration import existing_google_doc, upload_as_google_doc
    saved = {}
    for title, content in documents:
        doc = existing_google_doc(folder_id, title) or upload_as_google_doc(folder_id, title + ".txt", content)
        saved[title] = doc["id"]
    return saved


def run_analysis_step(job, api_key):
    """Summarize the translation and prepend it to the company's analysis note. Safe to retry."""
    import analysis_note
    from google_drive_integration import parse_folder_id
    if not job.get("period"):
        st.error("決算期を判定できなかったため、ノートに追記していません。下の欄で決算期を入力して再試行してください。")
        return
    if not job.get("analysis"):
        st.info(f"{job['model']}で決算分析ノートの要約を作成中…（数分かかることがあります）")
        usage = {}
        job["analysis"] = analysis_note.generate_quarter_analysis(
            api_key, job["model"], job["company"], job["period"], job["date"], job["translated"], usage)
        job["usage"] = usage
    analysis = job["analysis"]
    if analysis["char_count"] < analysis_note.MIN_CHARS:
        st.warning(f"要約が{analysis['char_count']:,}字で4,000字に届きませんでした。そのまま追記します。")
    item = job["company_row"]
    def folder_id(value):
        try:
            return parse_folder_id(value) if value else None
        except ValueError:
            return None
    doc_id, created = analysis_note.resolve_note_doc(
        item["company"], item.get("note_url", ""), folder_id(item.get("root_folder")),
        folder_id(item.get("transcript_folder")))
    doc_url = f"https://docs.google.com/document/d/{doc_id}/edit"
    if created:
        st.info(f"Googleドキュメント形式のノートを新規作成しました: {analysis_note.note_title(item['company'])}")
        try:
            company_registry.write_note_url(item, doc_url)
            st.caption("企業マスターの05_決算分析ノート列を新しいURLに更新しました。")
        except Exception as exc:
            st.warning(f"企業マスターへのURL書き戻しに失敗しました（手動で更新してください）: {exc}")
    outcome = analysis_note.prepend_quarter(doc_id, job["period"], job["date"], job["script_url"], analysis)
    job["done"] = True
    job["doc_url"] = doc_url
    if outcome == "exists":
        st.info(f"{job['period']} の章は既にノートにあるため追記しませんでした。")
    else:
        usage = job.get("usage") or {}
        st.success(f"05_決算分析ノートに {job['period']} を追記しました（{analysis['char_count']:,}字・"
                   f"論点{len(analysis['sections'])}個、入力{usage.get('input_tokens', 0):,}／"
                   f"出力{usage.get('output_tokens', 0):,}トークン）。")
        try:
            day = company_registry.write_note_updated(item)
            st.caption(f"企業マスターの「05更新」列に {day} を記録しました。")
        except Exception as exc:
            st.warning(f"企業マスターの「05更新」列への日付の記録に失敗しました（手動で記入してください）: {exc}")
    st.markdown(f"[05_決算分析ノートを開く]({doc_url})")
