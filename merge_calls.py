"""Merge earnings and follow-up transcripts into one native Google Doc."""
import hashlib
import os
import re
from pathlib import Path

import streamlit as st
from google_drive_integration import google_login_panel, parse_folder_id, existing_google_doc, upload_as_google_doc
from pdf_pipeline import metadata_filename
from translation_core import split_metadata, chunks, translate_chunk


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
    st.subheader("③ 統合メタデータ")
    a, b = st.columns(2)
    with a:
        company = st.text_input("企業名（必須）", key="merge_company_"+fingerprint, placeholder="NVIDIA")
        period = st.text_input("決算期（必須）", key="merge_period_"+fingerprint, placeholder="FY2027 Q2")
    with b:
        event_date = st.text_input("Event Date（必須）", key="merge_date_"+fingerprint, placeholder="2026-08-26")
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

    original_header, original_body, _=split_metadata(combined)
    parts=chunks(original_body)
    # Changes to the uploaded files, metadata or model start a separate translation job.
    job_id=hashlib.sha256((combined+"\\0"+model).encode("utf-8")).hexdigest()
    done=st.session_state.setdefault("merge_translation_"+job_id,{})
    st.caption(f"統合本文 {len(original_body):,}文字 / {len(parts)}チャンク。"
               "途中で停止しても同じ入力・設定で再開できます。")
    can_translate=bool(api_key and company.strip() and period.strip() and event_date.strip() and parts)
    if st.button("統合全文の日本語翻訳を開始／再開",type="primary",
                 disabled=not can_translate,key="merge_translate_start"):
        progress=st.progress(len(done)/max(len(parts),1))
        for i,part in enumerate(parts):
            if i in done:
                continue
            try:
                done[i]=translate_chunk(api_key,model,part,i+1,len(parts),
                    "Preserve the Earnings Call and Follow-up Call section boundaries and their order. "
                    "Do not omit any Q&A or other source material. Preserve original source metadata exactly.")
            except Exception as exc:
                st.error(f"{i+1}/{len(parts)}チャンクで停止: {exc}。再開できます。")
                break
            progress.progress(len(done)/max(len(parts),1))
    prompt_tokens=sum(v.get("input_tokens",0) for v in done.values())
    output_tokens=sum(v.get("output_tokens",0) for v in done.values())
    cached_tokens=sum(v.get("cached_input_tokens",0) for v in done.values())
    cost=(max(0,prompt_tokens-cached_tokens)*input_rate
          +cached_tokens*cached_rate+output_tokens*output_rate)/1_000_000
    c1,c2,c3=st.columns(3)
    c1.metric("入力トークン",f"{prompt_tokens:,}")
    c2.metric("出力トークン",f"{output_tokens:,}")
    c3.metric("推定API料金",f"${cost:.4f}")
    st.caption("表示額はAPIが返したトークン使用量と設定単価による推定額です。"
               "失敗・再試行リクエストの費用が含まれない場合があります。")
    complete=bool(parts) and len(done)==len(parts) and all(
        isinstance(done.get(i),dict) and done[i].get("text") for i in range(len(parts))
    )
    translated=None
    translated_name="Japanese_Translation_"+name
    st.caption(f"原文: {name} ／ 日本語訳: {translated_name}")
    if complete:
        # Preserve combined metadata byte-for-byte, translating the body only.
        translated=original_header+"\\n\\n# 本文（日本語訳）\\n\\n"+(
            "\\n\\n".join(done[i]["text"] for i in range(len(parts)))
        )+"\\n"
        st.success("統合全文の日本語翻訳が完了しました。")
        st.download_button("統合日本語訳TXTをダウンロード",
                           translated.encode("utf-8-sig"),
                           file_name=translated_name+".txt",mime="text/plain")
    st.subheader("⑤ Google Driveに原文・日本語訳を保存")
    google_login_panel()
    folder_url=st.text_input("保存先Google DriveフォルダURL",key="merge_folder",
                              placeholder="https://drive.google.com/drive/u/0/folders/...")
    st.caption("2つともネイティブGoogleドキュメント形式で保存します。"
               "同名ドキュメントは上書きせずスキップします。")
    if st.button("原文と日本語訳の2つをGoogleドキュメントで保存",type="primary",
                 disabled=not (complete and company.strip() and period.strip()
                               and event_date.strip() and folder_url.strip()
                               and st.session_state.get("google_drive_tokens")),
                 key="merge_save"):
        try:
            folder_id=parse_folder_id(folder_url)
            for doc_name,content in ((name,combined),(translated_name,translated)):
                existing=existing_google_doc(folder_id,doc_name)
                doc=existing or upload_as_google_doc(folder_id,doc_name+".txt",content)
                st.success(f'{"既存（スキップ）" if existing else "作成"}: {doc_name}')
                st.markdown(f"[Googleドキュメントを開く](https://docs.google.com/document/d/{doc['id']}/edit)")
        except Exception as exc:
            st.error(f"Google Driveへの保存に失敗しました: {exc}。翻訳し直さず再試行できます。")


if __name__ == "__main__":
    main()
