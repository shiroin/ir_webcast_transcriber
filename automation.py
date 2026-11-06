"""Steps shared by the Webcast and PDF pages: company detection, Drive save, analysis-note update."""
import re

import streamlit as st
import company_registry


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
        ticker = (item.get("ticker") or "").strip().lower()
        if len(ticker) >= 2 and re.search(r"(?<![a-z0-9])" + re.escape(ticker) + r"(?![a-z0-9])", titles):
            score += 5
        if score > best_score:
            best, best_score = item, score
    return best if best_score >= 2 else None


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
