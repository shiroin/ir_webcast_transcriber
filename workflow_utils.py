"""Persistent workflow preferences and idempotent Google Docs auto-save."""
import hashlib
import streamlit as st
from google_drive_integration import parse_folder_id, existing_google_doc, upload_as_google_doc

PREFS = ('merge_company','merge_period','merge_date','merge_folder',
         'p_company','p_period','p_date','pdf_drive_folder')


def persistent_text(label, key, placeholder='', default=''):
    """Keep settings in non-widget session keys when Streamlit removes widgets."""
    shadow = 'pref_' + key
    if shadow not in st.session_state:
        st.session_state[shadow] = default
    if key not in st.session_state:
        st.session_state[key] = st.session_state[shadow]
    def sync():
        st.session_state[shadow] = st.session_state[key]
    return st.text_input(label, key=key, placeholder=placeholder, on_change=sync)


def oauth_preferences():
    return {k: str(st.session_state.get('pref_'+k, st.session_state.get(k,'')))[:500]
            for k in PREFS if st.session_state.get('pref_'+k, st.session_state.get(k,''))}


def restore_oauth_preferences(values):
    if not isinstance(values, dict):
        return
    for k in PREFS:
        if isinstance(values.get(k), str):
            st.session_state['pref_'+k] = values[k][:500]
            # The callback executes before any widget is rendered.
            st.session_state[k] = values[k][:500]


def auto_save_documents(folder_url, documents, job_id, retry=False):
    """Upload each document once per job/folder, retry only incomplete documents."""
    if not folder_url.strip():
        st.info('自動保存待機中：Google DriveフォルダURLを入力してください。')
        return
    if not st.session_state.get('google_drive_tokens'):
        st.info('自動保存待機中：Googleアカウントでログインしてください。')
        return
    try:
        folder_id = parse_folder_id(folder_url)
    except ValueError as exc:
        st.warning(str(exc))
        return
    identity = hashlib.sha256((job_id+'\0'+folder_id).encode()).hexdigest()
    record = st.session_state.setdefault('autosave_'+identity, {})
    if record.get('error') and not retry:
        st.error('前回の自動保存でエラーが発生しました：'+record['error'])
        st.caption('翻訳は保持されています。下の「保存のみ再試行」を押してください。')
        return
    record.pop('error', None)
    for title, content in documents:
        if title in record:
            continue
        try:
            existing = existing_google_doc(folder_id, title)
            result = existing or upload_as_google_doc(folder_id, title+'.txt', content)
            record[title] = result['id']
        except Exception as exc:
            record['error'] = str(exc)
            st.error(f'{title} の自動保存に失敗しました：{exc}')
            st.caption('「保存のみ再試行」で未保存ファイルから再開できます。')
            break
    for title, _ in documents:
        if title in record:
            st.success('保存済み：'+title)
            st.markdown(f'[Googleドキュメントを開く](https://docs.google.com/document/d/{record[title]}/edit)')
