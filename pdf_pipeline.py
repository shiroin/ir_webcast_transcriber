"""PDF -> metadata-preserving transcript -> full translation -> two Google Docs -> analysis note.

Upload a PDF (or paste its URL) and everything else runs automatically: company detection from the
company master, fiscal period / call date detection, translation, saving to Quarterly_Analysis and
prepending a 4,000+ character analysis to 05_決算分析ノート. Every step is resumable without paying twice."""
import hashlib
import os
import re
from pathlib import Path
import streamlit as st
import fitz
from google_drive_integration import google_login_panel, parse_folder_id, existing_google_doc, upload_as_google_doc
from translation_core import chunks, translate_chunk, split_metadata


def make_original(pdf_bytes, filename, metadata, page_breaks=True):
    with fitz.open(stream=pdf_bytes, filetype='pdf') as doc:
        if doc.needs_pass:
            raise ValueError('パスワード保護されたPDFです。解除してからアップロードしてください。')
        pages = [p.get_text('text', sort=True).strip() for p in doc]
    if not pages or not any(pages):
        raise ValueError('PDFから文字を抽出できません。スキャンPDFはOCR済みのファイルを使用してください。')
    def clean(value):
        return str(value or '').replace('\r',' ').replace('\n',' ').strip()
    header = '\n'.join([
        '# IR Webcast Metadata',
        f'- Company: {clean(metadata["company"])}',
        f'- Fiscal Period: {clean(metadata["period"])}',
        f'- Event Date: {clean(metadata["event_date"])}',
        f'- Document Title: {clean(metadata["title"])}',
        f'- Original Filename: {clean(filename)}',
        f'- Source URL: {clean(metadata["url"])}',
        '- Source Format: PDF',
        '- Extraction Method: PyMuPDF (no OCR)',
        f'- Source Pages: {len(pages)}',
        f'- Source SHA256: {hashlib.sha256(pdf_bytes).hexdigest()}',
    ])
    body = '\n\n'.join((f'===== 原本 {i} ページ =====\n{page}' if page_breaks else page)
                       for i,page in enumerate(pages,1) if page or page_breaks)
    original = header + '\n\n# Original Transcript\n\n' + body + '\n'
    parsed_header, parsed_body, _ = split_metadata(original)
    if parsed_header.strip() != header.strip() or not parsed_body.strip():
        raise RuntimeError('メタデータと本文の区切りに失敗しました。')
    return original, header, parsed_body, [i for i,p in enumerate(pages,1) if not p]


def metadata_filename(metadata, include_title=False):
    """Build a stable, human-readable document name from user metadata."""
    def safe(value):
        value = re.sub(r'[\\/:*?"<>|\x00-\x1f]+', '_', str(value or '').strip())
        value = re.sub(r'\s+', '_', value)
        value = re.sub(r'_+', '_', value).strip('._ ')
        return value[:90].rstrip('._ ')
    fields = [safe(metadata.get(k)) for k in ('company','period','event_date')]
    if include_title and safe(metadata.get('title')):
        fields.append(safe(metadata['title']))
    return '_'.join(part for part in fields if part) or 'Transcript'


UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/151 Safari/537.36"
MAX_PDF_BYTES = 60 * 1024 * 1024


def fetch_pdf(url):
    """Download a transcript PDF from an IR site (bounded, PDF signature checked)."""
    import requests
    from urllib.parse import urlparse, unquote
    with requests.get(url, headers={"User-Agent": UA, "Accept": "application/pdf,*/*"},
                      stream=True, timeout=(15, 60)) as response:
        if response.status_code in (401, 403):
            raise RuntimeError(f"PDFのサイトがサーバーからのアクセスを拒否しました（HTTP {response.status_code}）。"
                               "PDFをダウンロードしてアップロードしてください。")
        response.raise_for_status()
        data = b""
        for block in response.iter_content(1024 * 1024):
            data += block
            if len(data) > MAX_PDF_BYTES:
                raise RuntimeError("PDFが60MBを超えています。")
    if not data.startswith(b"%PDF"):
        raise RuntimeError("URLの中身がPDFではありませんでした（ログイン画面などの可能性があります）。")
    name = Path(unquote(urlparse(url).path)).name or "transcript.pdf"
    return data, name if name.lower().endswith(".pdf") else name + ".pdf"


def _secret(name, default=""):
    try:
        return str(st.secrets.get(name, os.getenv(name, default)) or default)
    except Exception:
        return os.getenv(name, default)


def run_pipeline(job, data, filename, source_url, item, api_key, settings):
    """All steps for one PDF. Each finished step is stored in `job`, so a retry continues where it stopped."""
    import analysis_note
    from automation import save_docs_once, run_analysis_step
    with st.status("自動処理中…", expanded=True) as status:
        if not job.get("meta"):
            status.update(label="決算期・説明会日をスクリプトから判定中…")
            hints = "\n".join(x for x in [f"PDF filename: {filename}", f"PDF title: {job.get('pdf_title', '')}",
                                           f"Source URL: {source_url}"] if not x.endswith(": "))
            detected = analysis_note.extract_call_metadata(api_key, settings["meta_model"], item["company"],
                                                          job["text"], hints)
            job["meta"] = {"company": item["company"], "period": detected["fiscal_period"],
                           "event_date": detected["event_date"], "url": source_url,
                           "title": job.get("pdf_title") or detected["event_title"],
                           "evidence": detected["evidence"]}
        meta = job["meta"]
        st.write(f"決算期：{meta['period'] or '不明'} ／ 説明会日：{meta['event_date'] or '不明'}"
                 f"（根拠：{meta.get('evidence', '')[:150]}）")
        if not meta["period"]:
            raise RuntimeError("決算期を判定できませんでした。下の欄に決算期を入力して「再開」を押してください。")

        original, header, body, _ = make_original(data, filename, meta, settings.get("page_breaks", True))
        parts = chunks(body)
        done = job.setdefault("chunks", {})
        if len(done) < len(parts):
            status.update(label=f"全文を日本語に翻訳中…（{len(parts)}チャンク）")
            progress = st.progress(len(done) / len(parts))
            for i, part in enumerate(parts):
                if i not in done:
                    done[i] = translate_chunk(api_key, settings["model"], part, i + 1, len(parts), "")
                    progress.progress(len(done) / len(parts))
        translated = header + "\n\n# 本文（日本語訳）\n\n" + "\n\n".join(done[i]["text"] for i in range(len(parts))) + "\n"

        base = metadata_filename(meta)
        original_title, translated_title = base + "_Original", "Japanese_Translation_" + base + "_Original"
        if not job.get("docs"):
            status.update(label="Quarterly_Analysisに原文と日本語訳を保存中…")
            job["docs"] = save_docs_once(parse_folder_id(item["transcript_folder"]),
                                         [(original_title, original), (translated_title, translated)])
        script_url = f"https://docs.google.com/document/d/{job['docs'][translated_title]}/edit"

        if settings["note"]:
            status.update(label="要約を作成し、05_決算分析ノートに追記中…（数分かかります）")
            note = job.setdefault("note", {"company": item["company"], "company_row": item,
                                           "translated": translated, "model": settings["analysis_model"]})
            note.update(period=meta["period"], date=meta["event_date"], script_url=script_url)
            if not note.get("done"):
                run_analysis_step(note, api_key)
                if not note.get("done"):
                    raise RuntimeError("05_決算分析ノートへの追記が完了しませんでした。")
        job["done"] = True
        status.update(label="すべて完了しました", state="complete")


def show_results(job):
    if (job.get("note") or {}).get("doc_url"):
        st.markdown(f"- [05_決算分析ノート]({job['note']['doc_url']})")
    for title, doc_id in (job.get("docs") or {}).items():
        st.markdown(f"- [{title}](https://docs.google.com/document/d/{doc_id}/edit)")
    usage = [v for v in (job.get("chunks") or {}).values()]
    if usage:
        st.caption(f"翻訳トークン：入力{sum(v.get('input_tokens', 0) for v in usage):,}／"
                   f"出力{sum(v.get('output_tokens', 0) for v in usage):,}")


def main():
    import company_registry
    from automation import guess_company_from_document
    from google_drive_integration import ensure_google_auth
    st.title('📄 PDF決算スクリプト → 日本語訳 → Google Drive')
    st.caption('PDFをアップロードするかURLを貼るだけで、企業判定 → 決算期・説明会日の判定 → 全文日本語訳 → '
               'Quarterly_Analysisに保存 → 05_決算分析ノート追記まで自動で行います。')
    google_login_panel()
    companies = []
    if ensure_google_auth():
        try:
            companies = company_registry.load_companies()
        except Exception as exc:
            st.warning(f'企業マスターを読み込めません: {exc}')
    api_key = _secret('OPENAI_API_KEY')
    with st.expander('設定（通常は変更不要）'):
        if not api_key:
            api_key = st.text_input('OpenAI APIキー', type='password', key='pdf_openai_key')
        settings = {
            'model': st.text_input('翻訳モデル', value='gpt-4.1-mini', key='pdf_translation_model'),
            'analysis_model': st.text_input('要約モデル', value='gpt-4.1', key='pdf_analysis_model'),
            'meta_model': 'gpt-4.1-mini',
            'note': st.checkbox('05_決算分析ノートに追記する', value=True, key='pdf_note_enabled'),
        }
        settings['page_breaks'] = st.checkbox('ページ区切りを残す', value=True, key='pdf_page_breaks')

    source = st.radio('入力方法', ['PDFファイル', 'PDFのURL'], horizontal=True, key='pdf_source_kind')
    data, filename, source_url = None, '', ''
    if source == 'PDFファイル':
        pdf = st.file_uploader('決算説明会スクリプトのPDF', type=['pdf'], key='pdf_pipeline_file')
        if pdf is not None:
            data, filename = pdf.getvalue(), pdf.name
    else:
        source_url = st.text_input('PDFのURL', key='pdf_source_url',
                                   placeholder='https://.../earnings-call-transcript.pdf').strip()
        if source_url:
            cache = st.session_state.setdefault('pdf_url_cache', {})
            if source_url not in cache:
                try:
                    with st.spinner('PDFを取得中…'):
                        cache[source_url] = fetch_pdf(source_url)
                except Exception as exc:
                    st.error(f'PDFを取得できませんでした: {exc}')
            if source_url in cache:
                data, filename = cache[source_url]
    if data is None:
        st.info('PDFをアップロードするか、URLを貼ってください。あとは自動で進みます。')
        return

    fingerprint = hashlib.sha256(data).hexdigest()
    job = st.session_state.setdefault('pdf_auto_jobs', {}).setdefault(fingerprint, {})
    if 'text' not in job:
        try:
            with fitz.open(stream=data, filetype='pdf') as doc:
                if doc.needs_pass:
                    raise ValueError('パスワード保護されたPDFです。解除してからアップロードしてください。')
                job['pdf_title'] = (doc.metadata or {}).get('title') or ''
                job['pages'] = len(doc)
                job['text'] = '\n'.join(p.get_text('text', sort=True) for p in doc)
        except Exception as exc:
            st.error(f'PDFを開けませんでした: {exc}')
            return
        if not job['text'].strip():
            st.error('PDFから文字を抽出できません。スキャンPDFはOCR済みのファイルを使用してください。')
            return
    st.caption(f"{filename} ／ {job['pages']}ページ ／ {len(job['text']):,}文字")

    labels = [company_registry.label(c) for c in companies]
    if 'company_label' not in job:
        guessed = guess_company_from_document(companies, [filename, job.get('pdf_title', ''), source_url],
                                              job['text'])
        job['company_label'] = company_registry.label(guessed) if guessed else None
    options = ['（企業を選択）'] + labels
    default = options.index(job['company_label']) if job['company_label'] in options else 0
    choice = st.selectbox('企業（PDFから自動判定）', options, index=default, key='pdf_company_' + fingerprint[:12])
    item = companies[labels.index(choice)] if choice in labels else None
    if item and not item.get('transcript_folder', '').startswith('http'):
        st.error('企業マスターのQuarterly_Analysis列（保存先フォルダ）が空です。')
        item = None

    ready = bool(item and api_key and companies)
    if not ready:
        st.info('企業を選ぶと自動で処理を始めます。' if companies and api_key else
                'Google Drive接続とOpenAI APIキー（Secrets）が必要です。')
    if ready and not job.get('done') and not job.get('error'):
        # Settings that change the output are fixed for this PDF once processing starts.
        settings = job.setdefault('settings', settings)
        try:
            run_pipeline(job, data, filename, source_url, item, api_key, settings)
        except Exception as exc:
            job['error'] = f'処理が途中で止まりました: {exc}'
    if job.get('error'):
        st.error(job['error'])
        if job.get('meta') is not None:
            c1, c2 = st.columns(2)
            job['meta']['period'] = c1.text_input('決算期', value=job['meta'].get('period', ''),
                                                  key='pdf_fix_period_' + fingerprint[:12])
            job['meta']['event_date'] = c2.text_input('説明会日（YYYY-MM-DD）', value=job['meta'].get('event_date', ''),
                                                      key='pdf_fix_date_' + fingerprint[:12])
        if ready:
            # Clearing the error in the callback lets the next run continue automatically.
            st.button('再開（翻訳済み・保存済みの部分は使い回します）', type='primary',
                      key='pdf_resume_' + fingerprint[:12], on_click=job.pop, args=('error', None))
    if job.get('done'):
        st.success('完了しました：Quarterly_Analysisへの保存と05_決算分析ノートへの追記が終わっています。')
    show_results(job)


if __name__=='__main__':
    main()
