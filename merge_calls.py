"""Merge earnings and follow-up transcripts into one native Google Doc."""
import hashlib
import os
import re
from pathlib import Path

import streamlit as st
from google_drive_integration import google_login_panel, parse_folder_id, existing_google_doc, upload_as_google_doc
from pdf_pipeline import metadata_filename
from translation_core import split_metadata, chunks, translate_chunk
from workflow_utils import persistent_text, auto_save_documents


def read_source(upload):
    """Return extracted text, original header, body; reject image-only PDFs."""
    name = upload.name
    data = upload.getvalue()
    if name.lower().endswith(".pdf"):
        import fitz
        with fitz.open(stream=data, filetype="pdf") as doc:
            if doc.needs_pass:
                raise ValueError(f"{name}: パスワード付きPDFは読み込めません")
            pages = [page.get_text("text", sort=True).strip() for page in doc]
        if not any(pages):
            raise ValueError(f"{name}: テキストを抽出できません（OCRが必要です）")
        body = "\n\n".join(
            f"===== 原本 {i} ページ =====\n{text}"
            for i, text in enumerate(pages, 1)
        )
        return "", body, len(pages)
    # UTF-8 BOM and common Japanese Windows encoding.
    for encoding in ("utf-8-sig", "cp932"):
        try:
            decoded = data.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    else:
        raise ValueError(f"{name}: TXTの文字コードを判別できません")
    if not decoded.strip():
        raise ValueError(f"{name}: 空ファイルです")
    header, body, _ = split_metadata(decoded)
    if not body.strip():
        body = decoded
        header = ""
    return header, body, None


def mostly_japanese(text):
    sample = (text or "")[:5000]
    jp = sum(1 for ch in sample if "\u3040" <= ch <= "\u30ff" or "\u4e00" <= ch <= "\u9fff")
    return bool(sample) and jp / len(sample) > 0.3


def detect_slides(upload):
    """True for a presentation-deck PDF (landscape, little text per page)."""
    if not upload.name.lower().endswith(".pdf"):
        return False
    import fitz
    from pdf_pipeline import looks_like_slides
    try:
        with fitz.open(stream=upload.getvalue(), filetype="pdf") as doc:
            return looks_like_slides(doc)
    except Exception:
        return False


def _secret_api_key():
    try:
        return str(st.secrets.get("OPENAI_API_KEY", os.getenv("OPENAI_API_KEY", "")) or "")
    except Exception:
        return os.getenv("OPENAI_API_KEY", "")


def detect_metadata(fingerprint, files_and_texts, api_key):
    """Company (master), fiscal period and call date from the two uploads, once per pair of files.

    Results go into the metadata inputs' session keys; values edited by hand afterwards are kept because
    detection does not run again for the same files. Returns a short report for the page, or None."""
    if st.session_state.get("merge_detected_for") == fingerprint:
        return st.session_state.get("merge_detect_report")
    import company_registry
    from automation import guess_company_from_document, guess_company_with_ai
    from analysis_note import extract_call_metadata
    names = [name for name, _ in files_and_texts]
    text = "\n\n".join(body[:8000] for _, body in files_and_texts)
    report, values = [], {}
    item = None
    try:
        companies = company_registry.load_companies()
        item = guess_company_from_document(companies, names, text)
        if not item and api_key:
            item = guess_company_with_ai(api_key, companies, names, text)
    except Exception as exc:
        report.append(f"企業の自動判定に失敗: {exc}")
    if item:
        values["merge_company"] = item["company"]
        if str(item.get("transcript_folder", "")).startswith("http"):
            values["merge_folder"] = item["transcript_folder"]
        st.session_state["merge_pending_pick"] = company_registry.label(item)
    if api_key:
        try:
            meta = extract_call_metadata(api_key, "gpt-4.1-mini", values.get("merge_company", ""), text,
                                         "Files: " + " / ".join(names))
            if meta.get("fiscal_period"):
                values["merge_period"] = meta["fiscal_period"]
            if meta.get("event_date"):
                values["merge_date"] = meta["event_date"]
            if meta.get("evidence"):
                report.append("根拠：" + meta["evidence"][:150])
        except Exception as exc:
            report.append(f"決算期・説明会日の自動判定に失敗: {exc}")
    st.session_state["merge_pending_values"] = values
    head = "自動判定：" + "／".join(f"{label} {values[key]}" for key, label in (
        ("merge_company", "企業"), ("merge_period", "決算期"), ("merge_date", "説明会日")) if values.get(key))
    report.insert(0, head if values else "自動判定できませんでした。手で入力してください。")
    st.session_state["merge_detected_for"] = fingerprint
    st.session_state["merge_detect_report"] = report
    return report


def saved_doc_id(folder_url, job_id, title):
    """ID of a document auto_save_documents created for this job (it keeps them per job and folder)."""
    try:
        folder_id = parse_folder_id(folder_url)
    except ValueError:
        return None
    identity = hashlib.sha256((job_id + "\0" + folder_id).encode()).hexdigest()
    doc_id = (st.session_state.get("autosave_" + identity) or {}).get(title)
    if not doc_id:
        found = existing_google_doc(folder_id, title)
        doc_id = found and found["id"]
    return doc_id


def add_to_note(item, folder_url, job_id, translated_name, translated, period, event_date, model, api_key):
    """After saving: summarize the Japanese text into 05_決算分析ノート, once per job (retry keeps the summary)."""
    from automation import run_analysis_step
    if not item:
        st.info("05_決算分析ノートに追記するには、上の「企業（企業マスター）」で企業を選んでください。")
        return
    script_id = saved_doc_id(folder_url, job_id, translated_name)
    if not script_id:
        st.caption("日本語訳の保存が終わると、05_決算分析ノートに追記します。")
        return
    note = st.session_state.setdefault("merge_note_" + job_id, {
        "company": item["company"], "company_row": item, "translated": translated, "model": model})
    note.update(period=period.strip(), date=event_date.strip(),
                script_url=f"https://docs.google.com/document/d/{script_id}/edit")
    if not note.get("done") and not note.get("error"):
        try:
            run_analysis_step(note, api_key)
            if not note.get("done"):
                note["error"] = "05_決算分析ノートへの追記が完了しませんでした。"
        except Exception as exc:
            note["error"] = f"05_決算分析ノートへの追記に失敗しました: {exc}"
    if note.get("error"):
        st.error(note["error"])
        # Clearing the error lets the next run try again; a summary already written is reused.
        st.button("05_決算分析ノートへの追記を再試行", key="merge_note_retry", on_click=note.pop, args=("error", None))
    elif note.get("done") and note.get("doc_url"):
        st.success("05_決算分析ノートへの追記まで完了しました。")
        st.markdown(f"[05_決算分析ノートを開く]({note['doc_url']})")


def build_combined(earnings, followup, metadata):
    """Combine without rewriting either transcript, with explicit source boundaries."""
    def clean(value):
        return str(value or "").replace("\r", " ").replace("\n", " ").strip()
    header = "\n".join([
        "# IR Webcast Metadata",
        f'- Company: {clean(metadata["company"])}',
        f'- Fiscal Period: {clean(metadata["period"])}',
        f'- Event Date: {clean(metadata["event_date"])}',
        "- Document Title: Earnings Call + Follow-up Call Combined Transcript",
        "- Document Type: Combined Transcript",
        f'- Earnings Call Source: {clean(earnings["name"])}',
        f'- Follow-up Call Source: {clean(followup["name"])}',
        "- Merge Order: Earnings Call, Follow-up Call",
    ])
    sections = []
    for title, item in (("Earnings Call", earnings), ("Follow-up Call", followup)):
        source_header = item["header"].strip()
        sections.append(
            f"## {title}\n"
            f"Source File: {clean(item['name'])}\n"
            + (f"\n### Source Metadata (verbatim)\n{source_header}\n" if source_header else "")
            + f"\n### Transcript\n{item['body'].strip()}\n"
        )
    return header + "\n\n# Original Transcript\n\n" + "\n\n".join(sections) + "\n"


def main():
    st.title("📑 Earnings Call + Follow-up Call 統合")
    st.caption("Earnings Call → Follow-up Callの順に全文を統合し、日本語に全文翻訳。"
               "原文とJapanese_Translation_付き日本語訳の2つをGoogleドキュメント形式で保存します。")
    st.subheader("Google Drive接続・保存先設定")
    google_login_panel()
    from automation import master_company_picker
    if st.session_state.get("merge_pending_pick"):
        st.session_state["merge_company_pick"] = st.session_state.pop("merge_pending_pick")
    for key, value in (st.session_state.pop("merge_pending_values", None) or {}).items():
        # Also the "pref_" shadow kept by workflow_utils.persistent_text.
        st.session_state[key] = st.session_state["pref_" + key] = value
    picked = master_company_picker("merge_company_pick", {"merge_folder": "transcript_folder", "merge_company": "company"},
                          label="企業（企業マスターから選ぶと保存先フォルダと企業名を自動入力）")
    folder_url=persistent_text("保存先Google DriveフォルダURL", "merge_folder",
        placeholder="https://drive.google.com/drive/u/0/folders/...")
    st.caption("保存先URLは次のファイルでも引き継がれます。翻訳完了後、自動保存します。")
    left, right = st.columns(2)
    with left:
        earnings_file = st.file_uploader("① Earnings Call（TXT / PDF）", type=["txt", "pdf"], key="merge_earnings")
    with right:
        followup_file = st.file_uploader("② Follow-up Call（TXT / PDF）", type=["txt", "pdf"], key="merge_followup")
    if not earnings_file or not followup_file:
        st.info("2つのファイルをアップロードしてください。TXTとPDFの組み合わせも可能です。")
        return
    if earnings_file.getvalue() == followup_file.getvalue():
        st.error("2つのファイルの内容が同一です。別々のトランスクリプトを指定してください。")
        return
    fingerprint = hashlib.sha256(
        earnings_file.getvalue() + b"\0" + followup_file.getvalue()
    ).hexdigest()[:16]
    try:
        e_header, e_body, e_pages = read_source(earnings_file)
        f_header, f_body, f_pages = read_source(followup_file)
    except Exception as exc:
        st.error(str(exc))
        return
    st.caption("説明会資料（スライドのPDF）は、AIが1枚ずつ表・グラフを読み取って文章化します。日本語の部分は翻訳しません。")
    s1, s2 = st.columns(2)
    e_slides = s1.checkbox("①をスライド資料として文章化", value=detect_slides(earnings_file),
                           key="merge_e_slides_" + fingerprint, disabled=not earnings_file.name.lower().endswith(".pdf"))
    f_slides = s2.checkbox("②をスライド資料として文章化", value=detect_slides(followup_file),
                           key="merge_f_slides_" + fingerprint, disabled=not followup_file.name.lower().endswith(".pdf"))
    st.subheader("③ 統合メタデータ")
    first_detection = st.session_state.get("merge_detected_for") != fingerprint
    report = detect_metadata(fingerprint, [(earnings_file.name, e_body), (followup_file.name, f_body)],
                             _secret_api_key())
    if first_detection and st.session_state.get("merge_pending_values"):
        st.rerun()  # apply the detected values from the top, where the picker and folder inputs are drawn
    for line in report or []:
        st.caption(line)
    a, b = st.columns(2)
    with a:
        company = persistent_text("企業名（必須）", key="merge_company", placeholder="NVIDIA")
        period = persistent_text("決算期（必須）", key="merge_period", placeholder="FY2027 Q2")
    with b:
        event_date = persistent_text("Event Date（必須）", key="merge_date", placeholder="2026-08-26")
    metadata = {"company": company, "period": period, "event_date": event_date}
    combined = build_combined(
        {"name": earnings_file.name, "header": e_header, "body": e_body},
        {"name": followup_file.name, "header": f_header, "body": f_body},
        metadata,
    )
    name = metadata_filename(metadata) + "_Earnings_and_Followup_Combined"
    st.caption(f"Googleドキュメント名：{name}")
    st.caption(f"Earnings Call: {len(e_body):,}文字"
               + (f" / {e_pages}ページ" if e_pages else "")
               + f"｜Follow-up Call: {len(f_body):,}文字"
               + (f" / {f_pages}ページ" if f_pages else ""))
    with st.expander("統合内容を確認", expanded=False):
        st.code(combined[:12000], language="text")
    st.download_button("統合原文TXTをダウンロード", combined.encode("utf-8-sig"),
                       file_name=name+".txt", mime="text/plain")
    st.subheader("④ 統合全文を日本語翻訳")
    def api_secret():
        try:
            return st.secrets.get("OPENAI_API_KEY", os.getenv("OPENAI_API_KEY", ""))
        except Exception:
            return os.getenv("OPENAI_API_KEY", "")
    api_key=st.text_input("OpenAI APIキー（Secrets設定済みなら入力不要）",
                          value=api_secret(),type="password",key="merge_openai_key")
    model=st.text_input("翻訳モデル",value="gpt-4.1-mini",key="merge_translation_model")
    input_rate=st.number_input("入力料金（USD / 100万トークン）",value=0.40,
                               min_value=0.0,format="%.4f",key="merge_input_rate")
    output_rate=st.number_input("出力料金（USD / 100万トークン）",value=1.60,
                                min_value=0.0,format="%.4f",key="merge_output_rate")
    cached_rate=st.number_input("キャッシュ入力料金（USD / 100万トークン）",value=0.10,
                                min_value=0.0,format="%.4f",key="merge_cached_rate")

    slide_model = st.text_input("スライド文章化モデル（画像を読めるモデル）", value="gpt-4.1", key="merge_slide_model")
    note_enabled = st.checkbox("保存後に、4,000字以上の要約を作って05_決算分析ノートの先頭に追記する", value=True,
                               key="merge_note_enabled")
    analysis_model = st.text_input("要約モデル", value="gpt-4.1", key="merge_analysis_model")
    original_header, original_body, _ = split_metadata(combined)
    sources = [
        {"key": "e", "title": "Earnings Call", "file": earnings_file, "body": e_body, "slides": e_slides},
        {"key": "f", "title": "Follow-up Call", "file": followup_file, "body": f_body, "slides": f_slides},
    ]
    for src in sources:
        src["mode"] = "slides" if src["slides"] else ("keep" if mostly_japanese(src["body"]) else "translate")
        src["parts"] = chunks(src["body"]) if src["mode"] == "translate" else []
    # Changes to the uploaded files, metadata, models or slide switches start a separate job.
    job_id = hashlib.sha256((combined + "\0" + model + "\0" + slide_model + "\0" +
                             "".join(src["mode"] for src in sources)).encode("utf-8")).hexdigest()
    job = st.session_state.setdefault("merge_job_" + job_id, {"e": {}, "f": {}})
    label = {"slides": "スライドを文章化", "keep": "日本語のためそのまま", "translate": "翻訳"}
    st.caption("　".join(f"{src['title']}：{label[src['mode']]}" +
                        (f"（{len(src['parts'])}チャンク）" if src["mode"] == "translate" else "") for src in sources) +
               "。途中で停止しても同じ入力・設定で再開できます。")

    def part_done(src):
        state = job[src["key"]]
        if src["mode"] == "keep":
            return True
        if src["mode"] == "slides":
            import fitz
            with fitz.open(stream=src["file"].getvalue(), filetype="pdf") as doc:
                return len(state.get("slides", {})) == len(doc)
        return len(state.get("chunks", {})) == len(src["parts"])

    can_translate = bool(api_key and company.strip() and period.strip() and event_date.strip())
    if st.button("日本語化 → 保存 → 05_決算分析ノート追記を開始／再開", type="primary", disabled=not can_translate,
                 key="merge_translate_start"):
        from pdf_pipeline import narrate_slides
        with st.status("日本語化中…", expanded=True) as status:
            try:
                for src in sources:
                    state = job[src["key"]]
                    if src["mode"] == "slides":
                        narrate_slides(state, src["file"].getvalue(), api_key, slide_model, status)
                    elif src["mode"] == "translate":
                        done = state.setdefault("chunks", {})
                        status.update(label=f"{src['title']}を翻訳中…（{len(src['parts'])}チャンク）")
                        for i, part in enumerate(src["parts"]):
                            if i not in done:
                                done[i] = translate_chunk(api_key, model, part, i + 1, len(src["parts"]),
                                    "Do not omit any Q&A or other source material. Preserve speaker labels and order.")
                status.update(label="日本語化が完了しました", state="complete")
            except Exception as exc:
                status.update(label="途中で止まりました", state="error")
                st.error(f"{exc}。もう一度押すと、終わった部分は使い回して続きから再開します。")

    usage = [v for src in sources for key in ("chunks", "slides") for v in job[src["key"]].get(key, {}).values()]
    prompt_tokens = sum(v.get("input_tokens", 0) for v in usage)
    output_tokens = sum(v.get("output_tokens", 0) for v in usage)
    cached_tokens = sum(v.get("cached_input_tokens", 0) for v in usage)
    cost = (max(0, prompt_tokens - cached_tokens) * input_rate
            + cached_tokens * cached_rate + output_tokens * output_rate) / 1_000_000
    c1, c2, c3 = st.columns(3)
    c1.metric("入力トークン", f"{prompt_tokens:,}")
    c2.metric("出力トークン", f"{output_tokens:,}")
    c3.metric("推定API料金", f"${cost:.4f}")
    st.caption("表示額はAPIが返したトークン使用量と設定単価による推定額です（スライドの文章化も同じ単価で概算）。"
               "失敗・再試行リクエストの費用が含まれない場合があります。")
    complete = all(part_done(src) for src in sources)
    translated = None
    translated_name = "Japanese_Translation_" + name
    st.caption(f"原文: {name} ／ 日本語訳: {translated_name}")
    if complete:
        sections = []
        for src in sources:
            state = job[src["key"]]
            if src["mode"] == "slides":
                text = "\n\n".join(f"===== スライド {i + 1} =====\n{state['slides'][i]['text']}"
                                     for i in range(len(state["slides"])))
                heading = f"## {src['title']}（説明会資料の文章化）"
            elif src["mode"] == "keep":
                text, heading = src["body"].strip(), f"## {src['title']}"
            else:
                text = "\n\n".join(state["chunks"][i]["text"] for i in range(len(src["parts"])))
                heading = f"## {src['title']}（日本語訳）"
            sections.append(heading + "\n\n" + text)
        # Preserve combined metadata byte-for-byte; only the body is in Japanese.
        translated = original_header + "\n\n# 本文（日本語訳）\n\n" + "\n\n".join(sections) + "\n"
        st.success("統合全文の日本語化が完了しました。")
        st.download_button("統合日本語訳TXTをダウンロード",
                           translated.encode("utf-8-sig"),
                           file_name=translated_name + ".txt", mime="text/plain")
    st.subheader("⑤ Google Driveへ自動保存 → 05_決算分析ノート")
    if complete:
        docs = ((name, combined), (translated_name, translated))
        auto_save_documents(folder_url, docs, job_id)
        if st.button("保存のみ再試行", key="merge_autosave_retry"):
            auto_save_documents(folder_url, docs, job_id, retry=True)
        if note_enabled:
            add_to_note(picked, folder_url, job_id, translated_name, translated, period, event_date,
                        analysis_model, api_key)
    else:
        st.caption("日本語化が終わると、指定したフォルダに原文と日本語訳を自動アップロードし、"
                   "続けて05_決算分析ノートに追記します。")


if __name__ == "__main__":
    main()
