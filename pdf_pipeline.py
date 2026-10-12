"""PDF -> metadata-preserving transcript -> full translation -> two Google Docs."""
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


def main():
    st.title('📄 PDF決算スクリプト → 日本語訳 → Google Drive')
    st.caption('PDFからメタデータ付き原文を作成し、全文日本語訳を生成。原文と日本語訳をGoogleドキュメント形式で保存します。')
    pdf = st.file_uploader('① PDFファイルをアップロード',type=['pdf'],key='pdf_pipeline_file')
    if pdf is None:
        st.info('まずNVIDIAなどの決算スクリプトPDFをアップロードしてください。')
        return
    data = pdf.getvalue()
    fingerprint = hashlib.sha256(data).hexdigest()
    try:
        with fitz.open(stream=data,filetype='pdf') as doc:
            pdf_title = (doc.metadata or {}).get('title') or ''
            num_pages = len(doc)
    except Exception as exc:
        st.error(f'PDFを開けませんでした: {exc}')
        return
    st.caption(f'{pdf.name} / {num_pages}ページ')
    st.subheader('② メタデータを確認・入力')
    st.caption('Event Dateは必須です。日付は自動推測せず、入力した値を原文と日本語訳の両方に記録します。')
    a,b=st.columns(2)
    with a:
        company=st.text_input('企業名（必須）',key='p_company_'+fingerprint,placeholder='NVIDIA')
        period=st.text_input('決算期（必須）',key='p_period_'+fingerprint,placeholder='FY2027 Q2')
    with b:
        event_date=st.text_input('Event Date（必須）',key='p_date_'+fingerprint,placeholder='2026-08-26')
        title=st.text_input('資料タイトル',value=pdf_title,key='p_title_'+fingerprint)
    url=st.text_input('PDF原本URL（任意）',key='p_url_'+fingerprint)
    page_breaks=st.checkbox('ページ区切りを残す',value=True,key='p_page_'+fingerprint)
    metadata={'company':company,'period':period,'event_date':event_date,'title':title,'url':url}
    try:
        original,header,body,empty_pages=make_original(data,pdf.name,metadata,page_breaks)
    except Exception as exc:
        st.error(str(exc))
        return
    if empty_pages:
        st.warning(f'文字を抽出できなかったページがあります: {empty_pages[:20]}。スキャン部分はOCRが必要です。')
    with st.expander('原文・メタデータをプレビュー',expanded=False):
        st.code(original[:6000],language='text')
    base = re.sub(r'[^\w.-]+','_',Path(pdf.name).stem,flags=re.UNICODE).strip('_') or 'Transcript'
    original_filename = base + '_original.txt'
    translated_filename = 'Japanese_Translation_' + base + '_original.txt'
    st.download_button('原文TXTをダウンロード',original.encode('utf-8-sig'),original_filename,'text/plain')
    st.subheader('③ 全文日本語翻訳')
    def secret(name,default=''):
        try: return st.secrets.get(name,os.getenv(name,default))
        except Exception: return os.getenv(name,default)
    api_key=st.text_input('OpenAI APIキー（Secrets設定済みなら入力不要）',value=secret('OPENAI_API_KEY'),type='password',key='pdf_openai_key')
    model=st.text_input('翻訳モデル',value='gpt-4.1-mini',key='pdf_translation_model')
    input_rate=st.number_input('入力料金（USD / 100万トークン）',value=0.40,min_value=0.0,format='%.4f',key='pdf_input_rate')
    output_rate=st.number_input('出力料金（USD / 100万トークン）',value=1.60,min_value=0.0,format='%.4f',key='pdf_output_rate')
    cached_rate=st.number_input('キャッシュ入力料金（USD / 100万トークン）',value=0.10,min_value=0.0,format='%.4f',key='pdf_cached_rate')
    parts=chunks(body)
    job_id=hashlib.sha256((original+'\0'+model).encode()).hexdigest()
    key='pdf_translation_'+job_id
    done=st.session_state.setdefault(key,{})
    st.caption(f'本文 {len(body):,}文字 / {len(parts)}チャンク。中断後は同じPDF・メタデータ・モデルなら続きから再開できます。')
    if st.button('全文日本語翻訳を開始／再開',type='primary',disabled=not bool(api_key and company.strip() and period.strip() and event_date.strip()),key='pdf_translate_start'):
        progress=st.progress(len(done)/max(len(parts),1))
        for i,part in enumerate(parts):
            if i in done: continue
            try:
                done[i]=translate_chunk(api_key,model,part,i+1,len(parts),'')
            except Exception as exc:
                st.error(f'{i+1}/{len(parts)} チャンクで停止: {exc}。再開できます。')
                break
            progress.progress(len(done)/max(len(parts),1))
    prompt_tokens=sum(v.get('input_tokens',0) for v in done.values())
    output_tokens=sum(v.get('output_tokens',0) for v in done.values())
    cached_tokens=sum(v.get('cached_input_tokens',0) for v in done.values())
    cost=((prompt_tokens-cached_tokens)*input_rate+cached_tokens*cached_rate+output_tokens*output_rate)/1_000_000
    c1,c2,c3=st.columns(3)
    c1.metric('入力トークン',f'{prompt_tokens:,}')
    c2.metric('出力トークン',f'{output_tokens:,}')
    c3.metric('推定API料金',f'${cost:.4f}')
    complete=bool(parts) and len(done)==len(parts)
    translated=None
    if complete:
        translated=header+'\n\n# 本文（日本語訳）\n\n'+'\n\n'.join(done[i]['text'] for i in range(len(parts)))+'\n'
        st.success('全文翻訳が完了しました。')
        st.download_button('日本語訳TXTをダウンロード',translated.encode('utf-8-sig'),translated_filename,'text/plain')
    st.subheader('④ Google Driveに原文・日本語訳を保存')
    google_login_panel()
    folder_url=st.text_input('保存先Google DriveフォルダURL',key='pdf_drive_folder',placeholder='https://drive.google.com/drive/u/0/folders/...')
    st.caption('両方ともGoogleドキュメント形式で保存します。TXTファイルをDriveに置くのではありません。')
    if st.button('原文・日本語訳をGoogleドキュメントで保存',type='primary',disabled=not complete or not folder_url.strip() or not st.session_state.get('google_drive_tokens') or not all((company.strip(),period.strip(),event_date.strip())),key='pdf_save_docs'):
        try:
            folder_id=parse_folder_id(folder_url)
            # Include a short content fingerprint in the title to avoid silently reusing a
            # stale Google Doc from a previous run of a similarly named PDF.
            suffix=hashlib.sha256(original.encode()).hexdigest()[:8]
            for filename,text in ((original_filename,original),(translated_filename,translated)):
                doc_name=Path(filename).stem+'_'+suffix
                existing=existing_google_doc(folder_id,doc_name)
                doc=existing if existing else upload_as_google_doc(folder_id,doc_name+'.txt',text)
                st.success(f'{"既存" if existing else "作成"}: {doc_name}')
                st.markdown(f'[Googleドキュメントを開く](https://docs.google.com/document/d/{doc["id"]}/edit)')
        except Exception as exc:
            st.error(f'Google Driveへの保存に失敗しました: {exc}。翻訳し直さずに再試行できます。')

if __name__=='__main__':
    main()
