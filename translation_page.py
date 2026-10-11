import io, os, re, json, time, zipfile, hashlib
from pathlib import Path
import requests
import streamlit as st
from urllib.parse import urlencode
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired

st.title('🌐 TXT / Google Docs・Drive 全文日本語翻訳')
st.caption('元のメタデータを一字一句保持・全文翻訳・途中再開・TXT出力')

MODEL_DEFAULT='gpt-4.1-mini'
API='https://api.openai.com/v1/chat/completions'


def split_metadata(s):
    # Preserve header verbatim except language/status values that must be updated after translation.
    m=re.search(r'(?m)^# (?:Original Transcript|本文|Transcript|原文本文)\s*$',s)
    if m: return s[:m.start()],s[m.end():],s[m.start():m.end()].strip()
    m=re.search(r'(?m)^===== 原本\s*\d+\s*ページ =====',s)
    if m: return s[:m.start()],s[m.start():],'# 本文'
    return '',s,'# 本文'


def chunks(text, max_chars=6500):
    # Keep page markers and paragraphs intact whenever possible.
    parts=re.split(r'(\n\s*\n)',text)
    out=[]; buf=''
    for part in parts:
        if len(buf)+len(part)>max_chars and buf.strip():
            out.append(buf);buf=''
        if len(part)>max_chars:
            while len(part)>max_chars:
                pivot=part.rfind('\n',0,max_chars)
                if pivot<max_chars//3: pivot=max_chars
                out.append(part[:pivot]);part=part[pivot:]
        buf+=part
    if buf.strip():out.append(buf)
    return out


def fetch_google_doc(url, token=''):
    """Read Google Docs and plain-text Google Drive files.

    Supported:
      docs.google.com/document/d/<id>/...
      drive.google.com/file/d/<id>/view
      drive.google.com/open?id=<id>
    OAuth token must have access to the source file.
    """
    url = url.strip()
    from urllib.parse import urlparse, parse_qs
    parsed = urlparse(url)
    host = (parsed.hostname or '').lower()
    segments = [segment for segment in parsed.path.split('/') if segment]
    if host == 'docs.google.com' and len(segments) >= 3 and segments[:2] == ['document', 'd']:
        file_id, kind = segments[2], 'doc'
    elif host in ('drive.google.com', 'www.drive.google.com') and len(segments) >= 3 and segments[:2] == ['file', 'd']:
        file_id, kind = segments[2], 'file'
    elif host in ('drive.google.com', 'www.drive.google.com') and parsed.path == '/open':
        file_id, kind = parse_qs(parsed.query).get('id', [''])[0], 'file'
    else:
        raise ValueError('Google Docs（/document/d/）またはGoogle DriveのTXT（/file/d/）のURLを入力してください。')
    if not re.fullmatch(r'[A-Za-z0-9_-]+', file_id):
        raise ValueError('URL内のファイルIDを認識できませんでした。')

    headers = {'Authorization': f'Bearer {token.strip()}'} if token.strip() else {}
    session = requests.Session()

    if token.strip():
        if kind == 'doc':
            endpoint = f'https://www.googleapis.com/drive/v3/files/{file_id}/export'
            params = {'mimeType': 'text/plain'}
        else:
            endpoint = f'https://www.googleapis.com/drive/v3/files/{file_id}'
            params = {'alt': 'media'}
    else:
        if kind == 'doc':
            endpoint = f'https://docs.google.com/document/d/{file_id}/export'
            params = {'format': 'txt'}
        else:
            endpoint = 'https://drive.google.com/uc'
            params = {'export': 'download', 'id': file_id}

    response = session.get(endpoint, params=params, headers=headers, timeout=60, allow_redirects=True)
    if response.status_code != 200:
        raise RuntimeError(
            f'Google Driveから取得できませんでした（HTTP {response.status_code}）。'
            '共有設定またはOAuthアクセストークンのアクセス権限を確認してください。'
        )
    ctype = response.headers.get('Content-Type', '').lower()
    sample = response.content[:2048].lstrip().lower()
    if ('text/html' in ctype or sample.startswith(b'<!doctype html') or sample.startswith(b'<html')):
        raise RuntimeError(
            'Googleからログイン画面またはダウンロード確認画面が返されました。'
            'ファイルを「リンクを知っている全員が閲覧可」にするか、'
            'アクセス権のあるOAuthトークンを使うか、TXTを直接アップロードしてください。'
        )
    if b'\x00' in response.content[:4096]:
        raise RuntimeError('テキスト以外のファイルが返されました。TXTファイルのURLを指定してください。')
    try:
        content = response.content.decode('utf-8-sig')
    except UnicodeDecodeError:
        try:
            content = response.content.decode('cp932')
        except UnicodeDecodeError:
            raise RuntimeError('テキストをUTF-8またはShift_JISとして読み込めませんでした。TXTを直接アップロードしてください。')
    if not content.strip():
        raise RuntimeError('ファイルが空でした。')
    # Prefer original filename declared in transcript metadata; preserve metadata as-is.
    declared = re.search(r'(?m)^(?:原本ファイル|原文ファイル名|元ファイル名|Original Filename)\s*[:：]\s*(.+?)\s*$', content)
    if declared:
        name = Path(declared.group(1).strip()).name
        name = re.sub(r'\.(pdf|docx?|md)$', '.txt', name, flags=re.I)
        if not name.lower().endswith('.txt'):
            name += '.txt'
    else:
        name = f'GoogleDoc_{file_id}.txt' if kind == 'doc' else f'GoogleDrive_{file_id}.txt'
    return content, name

def translate_chunk(key,model,part,idx,total,extra):
    system=('You are a professional Japanese translator of corporate financial earnings call transcripts. '
            'Translate ALL input text into fluent accurate Japanese, with absolutely no omissions, summaries, or editorial additions. '
            'Preserve all numeric figures, currency units, speaker names, Q/A labels, product names, section and page markers, and ordering. '
            'Do not translate metadata in this request; only the body is provided. '
            'If text is already Japanese, preserve it. Output only the translation. '+extra)
    payload={'model':model,'temperature':0,'messages':[{'role':'system','content':system},
             {'role':'user','content':f'Part {idx}/{total}. Translate the entire following transcript verbatim in meaning:\n\n{part}'}]}
    for attempt in range(5):
        try:
            r=requests.post(API,headers={'Authorization':f'Bearer {key}','Content-Type':'application/json'},json=payload,timeout=180)
            if r.status_code in (429,500,502,503,504):
                time.sleep(min(2**attempt*2,30));continue
            r.raise_for_status()
            translated=r.json()['choices'][0]['message']['content'].strip()
            if not translated: raise RuntimeError('翻訳結果が空でした')
            usage=r.json().get('usage') or {}
            return {
                'text': translated,
                'input_tokens': int(usage.get('prompt_tokens') or 0),
                'output_tokens': int(usage.get('completion_tokens') or 0),
                'cached_input_tokens': int((usage.get('prompt_tokens_details') or {}).get('cached_tokens') or 0),
                'usage_reported': bool(usage),
            }
        except requests.RequestException:
            if attempt==4: raise
            time.sleep(min(2**attempt*2,30))
    raise RuntimeError('APIの再試行回数を超えました')


def configured_api_key():
    """Missing secrets.toml must not crash the translation page."""
    env_key = os.environ.get('OPENAI_API_KEY', '')
    try:
        return st.secrets.get('OPENAI_API_KEY', env_key)
    except Exception:
        return env_key


with st.sidebar:
    st.subheader('翻訳設定')
    key=st.text_input('OpenAI APIキー',type='password',value=configured_api_key())
    model=st.text_input('モデル',value=MODEL_DEFAULT)
    extra=st.text_area('用語・翻訳上の追加指示（任意）',placeholder='例：inference=推論、ramp=量産立ち上げ')
    st.caption('APIキーはこのアプリから翻訳APIへのリクエストにのみ使用します。利用料金はAPI側で発生します。')
    st.divider()
    st.subheader('翻訳API料金設定（USD / 100万トークン）')
    st.caption('モデルの最新単価を確認して入力してください。初期値はgpt-4.1-miniの標準料金の参考値です。モデル変更時は単価も変更してください。')
    input_price=st.number_input('入力トークン単価（USD）',min_value=0.0,value=0.40,step=0.05,format='%.4f',key='translation_input_price')
    output_price=st.number_input('出力トークン単価（USD）',min_value=0.0,value=1.60,step=0.05,format='%.4f',key='translation_output_price')
    cached_price=st.number_input('キャッシュ済み入力トークン単価（USD）',min_value=0.0,value=0.10,step=0.05,format='%.4f',key='translation_cached_price')
    usd_jpy=st.number_input('参考為替 USD/JPY（任意設定）',min_value=0.01,value=150.0,step=1.0,format='%.2f',key='translation_usd_jpy')


# Google OAuth Authorization Code flow, configured via Streamlit secrets.
# Tokens live in the user's Streamlit session, not in source files.
DRIVE_SCOPE = "https://www.googleapis.com/auth/drive"

def _google_settings():
    try:
        values = dict(st.secrets)
    except Exception:
        values = {}
    def setting(name):
        return str(values.get(name, os.getenv(name, ""))).strip()
    return setting("GOOGLE_CLIENT_ID"), setting("GOOGLE_CLIENT_SECRET"), setting("GOOGLE_REDIRECT_URI"), setting("GOOGLE_OAUTH_STATE_SECRET")

def _google_signer():
    _, _, _, secret = _google_settings()
    if not secret or len(secret) < 24:
        raise RuntimeError("GOOGLE_OAUTH_STATE_SECRET に24文字以上のランダム文字列を設定してください。")
    return URLSafeTimedSerializer(secret, salt="ir-webcast-google-drive-oauth-v1")

def _google_login_url():
    import secrets as pysecrets
    client_id, client_secret, redirect_uri, _ = _google_settings()
    if not client_id or not client_secret or not redirect_uri:
        raise RuntimeError("Streamlit Secretsに GOOGLE_CLIENT_ID、GOOGLE_CLIENT_SECRET、GOOGLE_REDIRECT_URI を設定してください。")
    if not redirect_uri.startswith("https://") and not redirect_uri.startswith("http://localhost:"):
        raise RuntimeError("GOOGLE_REDIRECT_URIにはHTTPSのアプリURLを設定してください。")
    nonce=pysecrets.token_urlsafe(24)
    st.session_state["google_oauth_nonce"]=nonce
    state=_google_signer().dumps({"nonce":nonce,"v":1})
    return "https://accounts.google.com/o/oauth2/v2/auth?" + urlencode({
        "client_id":client_id, "redirect_uri":redirect_uri, "response_type":"code",
        "scope":DRIVE_SCOPE, "access_type":"offline", "prompt":"consent",
        "include_granted_scopes":"true", "state":state,
    })

def _google_oauth_callback():
    params=st.query_params
    if "error" in params:
        st.error("Google認証がキャンセルされました: "+str(params.get("error")))
        st.query_params.clear()
        return
    if "code" not in params:
        return
    try:
        state=str(params.get("state",""))
        payload=_google_signer().loads(state,max_age=600)
        nonce=st.session_state.get("google_oauth_nonce")
        if not payload.get("nonce"):
            raise RuntimeError("Google認証のstateにnonceがありません。") 
        # The signed, short-lived state also works if Streamlit starts a fresh
        # WebSocket session after the Google redirect. Match the nonce when
        # the original Streamlit session survived the round trip.
        if nonce and payload.get("nonce")!=nonce:
            raise RuntimeError("認証状態が一致しません。もう一度ログインしてください。")
        client_id,client_secret,redirect_uri,_=_google_settings()
        result=requests.post("https://oauth2.googleapis.com/token",data={
            "code":str(params["code"]), "client_id":client_id,
            "client_secret":client_secret,"redirect_uri":redirect_uri,
            "grant_type":"authorization_code",
        },timeout=30)
        result.raise_for_status()
        token=result.json()
        if not token.get("access_token"):
            raise RuntimeError("Googleからアクセストークンが返されませんでした。")
        st.session_state["google_drive_tokens"]={
            "access_token":token["access_token"],
            "refresh_token":token.get("refresh_token"),
            "expires_at":time.time()+int(token.get("expires_in",3600)),
        }
        st.session_state.pop("google_oauth_nonce",None)
        st.success("Google Driveへの接続が完了しました。")
    except (BadSignature,SignatureExpired):
        st.error("Google認証の状態確認に失敗しました。もう一度ログインしてください。")
    except Exception as exc:
        st.error(f"Google認証に失敗しました: {exc}")
    finally:
        # Do not retain single-use authorization codes in browser history/query params.
        st.query_params.clear()

def _google_access_token():
    tokens=st.session_state.get("google_drive_tokens") or {}
    access=tokens.get("access_token")
    if not access:
        raise RuntimeError("Googleアカウントでログインしてください。")
    if time.time()<float(tokens.get("expires_at",0))-90:
        return access
    refresh=tokens.get("refresh_token")
    if not refresh:
        st.session_state.pop("google_drive_tokens",None)
        raise RuntimeError("Google認証の有効期限が切れました。再ログインしてください。")
    client_id,client_secret,_,_=_google_settings()
    result=requests.post("https://oauth2.googleapis.com/token",data={
        "client_id":client_id,"client_secret":client_secret,
        "refresh_token":refresh,"grant_type":"refresh_token",
    },timeout=30)
    if not result.ok:
        st.session_state.pop("google_drive_tokens",None)
        raise RuntimeError("Googleの認証更新に失敗しました。再ログインしてください。")
    refreshed=result.json()
    tokens["access_token"]=refreshed["access_token"]
    tokens["expires_at"]=time.time()+int(refreshed.get("expires_in",3600))
    st.session_state["google_drive_tokens"]=tokens
    return tokens["access_token"]

def google_login_panel():
    st.subheader("Google Drive接続")
    _google_oauth_callback()
    if st.session_state.get("google_drive_tokens"):
        st.success("Google Driveに接続済み")
        if st.button("Google Driveからログアウト",key="google_drive_logout"):
            st.session_state.pop("google_drive_tokens",None)
            st.rerun()
    else:
        try:
            url=_google_login_url()
            st.link_button("Googleアカウントでログイン",url,type="primary")
        except Exception as exc:
            st.warning(str(exc))
            st.caption("初回のみGoogle CloudでOAuthクライアントの設定が必要です。READMEをご確認ください。")

# Separate tool: convert every TXT in a Google Drive folder to native Google Docs.
def _folder_id(url):
    from urllib.parse import urlparse, parse_qs
    parsed = urlparse(url.strip())
    if (parsed.hostname or '').lower() not in ('drive.google.com', 'www.drive.google.com'):
        raise ValueError('Google DriveフォルダのURLを入力してください。')
    segments = [x for x in parsed.path.split('/') if x]
    if 'folders' in segments:
        idx = segments.index('folders')
        value = segments[idx + 1] if idx + 1 < len(segments) else ''
    else:
        value = parse_qs(parsed.query).get('id', [''])[0]
    if not re.fullmatch(r'[A-Za-z0-9_-]+', value):
        raise ValueError('フォルダIDを取得できませんでした。')
    return value


def _drive_auth_headers(token):
    if not token.strip():
        raise ValueError('フォルダ内の一覧取得・Googleドキュメント作成にはGoogle Driveの書き込み権限を持つOAuthアクセストークンが必要です。')
    return {'Authorization': 'Bearer ' + token.strip()}


def _drive_get(endpoint, headers, params=None):
    response = requests.get(endpoint, headers=headers, params=params, timeout=60)
    if not response.ok:
        raise RuntimeError(f'Google Drive API HTTP {response.status_code}: {response.text[:450]}')
    return response.json()


def _list_folder(folder_id, headers):
    items = []
    page_token = None
    while True:
        params = {
            'q': f"'{folder_id}' in parents and trashed = false",
            'fields': 'nextPageToken,files(id,name,mimeType,size,parents)',
            'pageSize': 1000,
            'supportsAllDrives': 'true',
            'includeItemsFromAllDrives': 'true',
        }
        if page_token:
            params['pageToken'] = page_token
        result = _drive_get('https://www.googleapis.com/drive/v3/files', headers, params)
        items.extend(result.get('files', []))
        page_token = result.get('nextPageToken')
        if not page_token:
            return items


def _convert_txt_file(file_info, folder_id, headers):
    source_id = file_info['id']
    source_name = file_info['name']
    response = requests.get(
        f'https://www.googleapis.com/drive/v3/files/{source_id}',
        headers=headers,
        params={'alt': 'media', 'supportsAllDrives': 'true'},
        timeout=120,
    )
    if not response.ok:
        raise RuntimeError(f'TXT取得 HTTP {response.status_code}: {response.text[:300]}')
    data = response.content
    try:
        decoded = data.decode('utf-8-sig')
    except UnicodeDecodeError:
        try:
            decoded = data.decode('cp932')
        except UnicodeDecodeError:
            raise RuntimeError('UTF-8 / Shift_JISとして読み込めません。')
    if not decoded.strip():
        raise RuntimeError('空のTXTは変換しません。')
    # Native Docs import. Source bytes are unchanged and original TXT is retained.
    new_name = re.sub(r'(?i)\\.txt$', '', source_name)
    metadata = {'name': new_name, 'mimeType': 'application/vnd.google-apps.document', 'parents': [folder_id]}
    import requests as _requests
    from requests_toolbelt.multipart.encoder import MultipartEncoder
    multipart = MultipartEncoder(fields={
        'metadata': ('metadata', json.dumps(metadata, ensure_ascii=False), 'application/json; charset=UTF-8'),
        'file': ('file.txt', decoded.encode('utf-8'), 'text/plain; charset=UTF-8'),
    })
    created = _requests.post(
        'https://www.googleapis.com/upload/drive/v3/files',
        params={'uploadType': 'multipart', 'fields': 'id,name,mimeType,webViewLink', 'supportsAllDrives': 'true'},
        headers={**headers, 'Content-Type': multipart.content_type},
        data=multipart,
        timeout=180,
    )
    if not created.ok:
        raise RuntimeError(f'Google Docs作成 HTTP {created.status_code}: {created.text[:400]}')
    return created.json()


def folder_converter_page():
    st.subheader('📁 フォルダ内TXT → Googleドキュメント一括変換')
    st.caption('指定フォルダ直下のTXTをGoogleドキュメント形式で同じフォルダに新規作成。元のTXTは削除しません。')
    folder_url = st.text_input('Google DriveフォルダURL', key='convert_folder_url',
                               placeholder='https://drive.google.com/drive/u/0/folders/...')
    google_login_panel()
    st.caption('Googleアカウントでログイン後、フォルダURLを入力してください。トークンの手動入力は不要です。')
    if st.button('TXTファイルを検索', key='convert_scan'):
        try:
            folder_id = _folder_id(folder_url)
            headers = _drive_auth_headers(_google_access_token())
            items = _list_folder(folder_id, headers)
            txts = [f for f in items if f.get('mimeType') == 'text/plain' and f.get('name','').lower().endswith('.txt')]
            existing_docs = {f.get('name') for f in items if f.get('mimeType') == 'application/vnd.google-apps.document'}
            rows = []
            for f in txts:
                target_name = re.sub(r'(?i)\\.txt$', '', f['name'])
                rows.append({'id': f['id'], 'source': f['name'], 'target': target_name,
                             'status': '既存のGoogleドキュメントあり（スキップ）' if target_name in existing_docs else '変換対象'})
            st.session_state['folder_conversion_scan'] = {'folder_id': folder_id, 'rows': rows}
        except Exception as exc:
            st.error(f'検索できませんでした: {exc}')
    scan = st.session_state.get('folder_conversion_scan')
    if scan:
        rows = scan['rows']
        pending = [r for r in rows if r['status'] == '変換対象']
        st.write(f'TXT: {len(rows)}件｜変換対象: {len(pending)}件｜既存の同名Googleドキュメント: {len(rows)-len(pending)}件')
        if rows:
            st.dataframe([{'元TXT': r['source'], '変換後Googleドキュメント': r['target'], '状態': r['status']} for r in rows],
                         use_container_width=True, hide_index=True)
        confirm = st.checkbox('対象TXTをGoogleドキュメントとして新規作成する（元TXTは保持）', key='convert_confirm')
        if st.button('一括変換を実行', type='primary', disabled=not (pending and confirm), key='convert_execute'):
            try:
                if _folder_id(folder_url) != scan['folder_id']:
                    raise ValueError('フォルダURLが変更されています。再検索してください。')
                headers = _drive_auth_headers(_google_access_token())
                # Re-scan at execution time to avoid duplicate docs created by other users since preview.
                live_items = _list_folder(scan['folder_id'], headers)
                existing = {f['name'] for f in live_items if f.get('mimeType') == 'application/vnd.google-apps.document'}
                live_txt = {f['id']: f for f in live_items if f.get('mimeType') == 'text/plain'}
                successes = []
                failures = []
                skipped = []
                bar = st.progress(0)
                for i, row in enumerate(pending):
                    try:
                        if row['target'] in existing:
                            skipped.append(row['source'])
                        elif row['id'] not in live_txt:
                            failures.append((row['source'], 'TXTが移動または削除されました'))
                        else:
                            created = _convert_txt_file(live_txt[row['id']], scan['folder_id'], headers)
                            successes.append((row['source'], created.get('id')))
                            existing.add(row['target'])
                    except Exception as exc:
                        failures.append((row['source'], str(exc)))
                    bar.progress((i+1)/len(pending))
                st.success(f'変換完了: {len(successes)}件。スキップ: {len(skipped)}件。エラー: {len(failures)}件。')
                if successes:
                    st.dataframe([{'元TXT': name, 'Googleドキュメント': f'https://docs.google.com/document/d/{fid}/edit'}
                                  for name, fid in successes], hide_index=True, use_container_width=True)
                for name, err in failures:
                    st.error(f'{name}: {err}')
                st.session_state.pop('folder_conversion_scan', None)
            except Exception as exc:
                st.error(f'一括変換を開始できませんでした: {exc}')


def pdf_to_txt_page():
    """Extract selectable PDF text locally; never fabricate OCR text."""
    import fitz
    st.subheader('📄 PDF → メタデータ付きTXT')
    st.caption('PDFのテキストをページ順に抽出し、先頭に企業名・決算期などのメタデータを付けます。OpenAI APIは使わず、変換自体にAPI料金は発生しません。')
    uploaded_pdf=st.file_uploader('決算スクリプトのPDFをアップロード',type=['pdf'],key='pdf_source_upload')
    if not uploaded_pdf:
        return
    pdf_bytes=uploaded_pdf.getvalue()
    pdf_hash=hashlib.sha256(pdf_bytes).hexdigest()
    try:
        doc=fitz.open(stream=pdf_bytes,filetype='pdf')
        if doc.needs_pass:
            st.error('パスワード付きPDFには対応していません。解除したPDFを使用してください。')
            doc.close()
            return
        page_count=len(doc)
        if page_count==0:
            st.error('PDFにページがありません。')
            doc.close()
            return
        extracted=[]
        empty_pages=[]
        for page_idx,page in enumerate(doc):
            text=page.get_text('text',sort=True).strip()
            if not text:
                empty_pages.append(page_idx+1)
            extracted.append(text)
        pdf_metadata=doc.metadata or {}
        doc.close()
    except Exception as exc:
        st.error(f'PDFを読み込めませんでした: {exc}')
        return
    st.info(f'ページ数: {page_count}｜抽出文字数: {sum(len(x) for x in extracted):,}｜文字を抽出できないページ: {len(empty_pages)}')
    if empty_pages:
        st.warning(f'ページ {", ".join(map(str,empty_pages[:30]))} はテキストを抽出できませんでした。'
                   'スキャン画像PDFの場合はOCRが必要です。この機能では画像から文字を推測・生成しません。')
    company=st.text_input('企業名',key=f'pdf_meta_company_{pdf_hash}',placeholder='例: NVIDIA')
    period=st.text_input('決算期',key=f'pdf_meta_period_{pdf_hash}',placeholder='例: FY2027 Q2')
    event_date=st.text_input('決算発表日・イベント日',key=f'pdf_meta_date_{pdf_hash}',placeholder='例: 2026-08-26')
    source_url=st.text_input('原本PDFのURL（任意）',key=f'pdf_meta_url_{pdf_hash}')
    title=st.text_input('資料タイトル（任意）',value=str(pdf_metadata.get('title') or ''),key=f'pdf_meta_title_{pdf_hash}')
    preserve_pages=st.checkbox('本文にページ区切りを残す',value=True,key=f'pdf_preserve_pages_{pdf_hash}')
    header_lines=[
        '# IR Webcast Metadata',
        f'- Company: {company}',
        f'- Fiscal Period: {period}',
        f'- Event Date: {event_date}',
        f'- Document Title: {title}',
        f'- Original Filename: {Path(uploaded_pdf.name).name}',
        f'- Source URL: {source_url}',
        '- Source Format: PDF',
        '- Extraction Method: PyMuPDF text extraction (no OCR)',
        f'- Source Pages: {page_count}',
        f'- Source SHA256: {pdf_hash}',
    ]
    body_parts=[]
    for idx,text in enumerate(extracted,1):
        if preserve_pages:
            body_parts.append(f'===== 原本 {idx} ページ =====\n{text}')
        elif text:
            body_parts.append(text)
    body='\n\n'.join(body_parts)
    txt='\n'.join(header_lines)+'\n\n# Original Transcript\n\n'+body+'\n'
    # Verify the output is parsed as metadata + body by the translation tab.
    parsed_header,parsed_body,_=split_metadata(txt)
    if not parsed_header.startswith('# IR Webcast Metadata\n') or not parsed_body.strip():
        st.error('メタデータの生成に失敗しました。ダウンロードせず再度お試しください。')
        return
    st.caption('入力されていないメタデータは空欄のまま残します。企業名や日付はPDFから推測しません。')
    st.success(f'メタデータ認識OK：{len(parsed_header):,}文字｜本文：{len(parsed_body):,}文字')
    with st.expander('変換結果の先頭をプレビュー',expanded=True):
        st.code(txt[:5000],language='text')
    if not body.strip() or not any(x.strip() for x in extracted):
        st.error('PDFから本文を抽出できませんでした。OCR済みPDFを使用してください。')
        return
    name=Path(uploaded_pdf.name).stem+'.txt'
    st.download_button('メタデータ付きTXTをダウンロード',data=txt.encode('utf-8-sig'),
                       file_name=name,mime='text/plain',type='primary',
                       key='pdf_txt_download')
    st.success('TXT変換完了。ダウンロードしたTXTを「全文日本語翻訳」タブにアップロードできます。')

feature = st.radio('翻訳ツール内の機能',
                   ['全文日本語翻訳', 'PDFをメタデータ付きTXTに変換', 'フォルダ内TXTをGoogleドキュメントに一括変換'],
                   horizontal=True, key='translation_feature')
if feature == 'PDFをメタデータ付きTXTに変換':
    pdf_to_txt_page()
    st.stop()
if feature == 'フォルダ内TXTをGoogleドキュメントに一括変換':
    folder_converter_page()
    st.stop()

mode=st.radio('入力方法',['TXTファイルをアップロード','Google Docs / DriveのURL'],horizontal=True)
raw=None;filename=None
if mode=='TXTファイルをアップロード':
    uploaded=st.file_uploader('TXTを選択（UTF-8推奨）',type=['txt'])
    if uploaded:
        try:raw=uploaded.getvalue().decode('utf-8-sig')
        except UnicodeDecodeError:
            try:raw=uploaded.getvalue().decode('cp932')
            except UnicodeDecodeError:st.error('文字コードを判定できませんでした。UTF-8に変換してください。')
        filename=uploaded.name
else:
    url=st.text_input('Google Docs / DriveのURL',placeholder='https://drive.google.com/file/d/.../view')
    st.caption('Googleドキュメント（/document/d/）と、Google Driveに保存したTXT（/file/d/）の両方に対応します。')
    google_login_panel()
    if st.button('ドキュメントを読み込む'):
        try:
            raw,filename=fetch_google_doc(url,_google_access_token() if st.session_state.get('google_drive_tokens') else '')
            st.session_state['source']=(raw,filename,url)
        except Exception as e:st.error(str(e))
    if 'source' in st.session_state and st.session_state['source'][2]==url:
        raw,filename,_=st.session_state['source']

if raw is not None and filename:
    header,body,section=split_metadata(raw)
    source_id=hashlib.sha256(raw.encode()).hexdigest()
    sections=chunks(body)
    output_name='Japanese_Translation_'+re.sub(r'^(?:Japanese_Translation_)','',filename)
    st.info(f'ファイル: {filename}｜本文: {len(body):,}文字｜翻訳チャンク: {len(sections)}｜メタデータ: {"あり" if header.strip() else "なし"}')
    with st.expander('保持するメタデータ'):st.code(header or 'メタデータなし',language='text')
    state_key='translated_'+source_id+'_'+model
    if state_key not in st.session_state:st.session_state[state_key]={}
    done=st.session_state[state_key]
    # Legacy session values from v24 were plain strings. Preserve translations but
    # do not invent token counts for earlier requests.
    for _idx,_value in list(done.items()):
        if isinstance(_value,str):
            done[_idx]={'text':_value,'input_tokens':0,'output_tokens':0,
                        'cached_input_tokens':0,'usage_reported':False}
    def show_api_cost():
        values=list(done.values())
        input_tokens=sum(v.get('input_tokens',0) for v in values)
        output_tokens=sum(v.get('output_tokens',0) for v in values)
        cached_tokens=sum(v.get('cached_input_tokens',0) for v in values)
        billable_regular=max(input_tokens-cached_tokens,0)
        cost=(billable_regular*input_price+cached_tokens*cached_price+output_tokens*output_price)/1_000_000
        known=sum(bool(v.get('usage_reported')) for v in values)
        a,b,c,d=st.columns(4)
        a.metric('入力トークン',f'{input_tokens:,}')
        b.metric('出力トークン',f'{output_tokens:,}')
        c.metric('推定API料金',f'${cost:.4f}')
        d.metric('円換算（参考）',f'¥{cost*usd_jpy:,.2f}')
        st.caption(f'実績トークン取得: {known}/{len(values)} チャンク｜キャッシュ済み入力: {cached_tokens:,} トークン。'
                   'APIが返すusageに設定単価を掛けた推定額です。'
                   '過去バージョンの翻訳や失敗・再試行時の課金、追加料金は含まれない場合があります。'
                   '実際の請求額はOpenAIのUsage/Billing画面で確認してください。')
        return cost
    st.progress(len(done)/max(len(sections),1),text=f'{len(done)}/{len(sections)} チャンク完了')
    if done: show_api_cost()
    if st.button('全文翻訳を開始 / 続きから再開',type='primary',disabled=not key):
        bar=st.progress(len(done)/max(len(sections),1))
        status=st.empty()
        for i,part in enumerate(sections):
            if i in done:continue
            status.write(f'翻訳中: {i+1}/{len(sections)}')
            try:done[i]=translate_chunk(key,model,part,i+1,len(sections),extra)
            except Exception as e:
                st.error(f'チャンク{i+1}で停止: {e}。再実行で続きから再開できます。');break
            bar.progress(len(done)/len(sections))
            show_api_cost()
        if len(done)==len(sections):st.success('全文翻訳が完了しました。')
    if done:
        complete=len(done)==len(sections)
        rendered='\n\n'.join(done[i]['text'] for i in range(len(sections)) if i in done)
        if complete:
            result=header+'\n# 本文（日本語訳）\n'+rendered+'\n'
            st.download_button('日本語訳TXTをダウンロード',result.encode('utf-8-sig'),file_name=output_name,mime='text/plain')
        else:
            partial_name=output_name.replace('.txt','_PARTIAL.txt')
            partial=header+'\n# 本文（日本語訳・途中まで）\n'+rendered
            st.download_button('途中経過TXTをダウンロード',partial.encode('utf-8-sig'),file_name=partial_name,mime='text/plain')
        st.text_area('翻訳プレビュー（冒頭）',rendered[:3500],height=260)
else:
    st.caption('TXTをアップロードするか、Google Docs / DriveのURLを読み込んでください。')
