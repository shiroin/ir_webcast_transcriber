import io, os, re, json, time, zipfile, hashlib
from pathlib import Path
import requests
import streamlit as st

st.title('🌐 TXT / Googleドキュメント 全文日本語翻訳')
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
    doc_match = re.search(r'(?:docs\\.google\\.com/document/d/)([\\w-]+)', url)
    drive_match = re.search(r'(?:drive\\.google\\.com/file/d/)([\\w-]+)', url)
    query_match = re.search(r'[?&]id=([\\w-]+)', url)
    if doc_match:
        file_id, kind = doc_match.group(1), 'doc'
    elif drive_match:
        file_id, kind = drive_match.group(1), 'file'
    elif query_match and ('drive.google.com' in url or 'docs.google.com' in url):
        file_id, kind = query_match.group(1), 'file'
    else:
        raise ValueError('Googleドキュメント（/document/d/）またはGoogle DriveのTXTファイル（/file/d/）のURLを入力してください。')

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
    if b'\\x00' in response.content[:4096]:
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
    # The Drive file ID is stable and avoids falsely claiming the original filename.
    return content, f'GoogleDoc_{file_id}.txt' if kind == 'doc' else f'GoogleDrive_{file_id}.txt'

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
            return translated
        except requests.RequestException:
            if attempt==4: raise
            time.sleep(min(2**attempt*2,30))
    raise RuntimeError('APIの再試行回数を超えました')


def metadata_as_japanese(header):
    # Maintain all existing metadata fields and values except language/translation status.
    header=re.sub(r'(?m)^(本文言語\s*:\s*).*$',r'\1日本語（全文翻訳）',header)
    header=re.sub(r'(?m)^(翻訳状況\s*:\s*).*$',r'\1全文翻訳済み（機械翻訳・未校正）',header)
    if not re.search(r'(?m)^本文言語\s*:',header):header+='本文言語: 日本語（全文翻訳）\n'
    if not re.search(r'(?m)^翻訳状況\s*:',header):header+='翻訳状況: 全文翻訳済み（機械翻訳・未校正）\n'
    return header

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
    token=st.text_input('OAuthアクセストークン（非公開ドキュメントの場合・任意）',type='password')
    if st.button('ドキュメントを読み込む'):
        try:
            raw,filename=fetch_google_doc(url,token)
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
    st.progress(len(done)/max(len(sections),1),text=f'{len(done)}/{len(sections)} チャンク完了')
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
        if len(done)==len(sections):st.success('全文翻訳が完了しました。')
    if done:
        complete=len(done)==len(sections)
        rendered='\n\n'.join(done[i] for i in range(len(sections)) if i in done)
        if complete:
            result=metadata_as_japanese(header)+'\n# 本文（日本語訳）\n'+rendered+'\n'
            st.download_button('日本語訳TXTをダウンロード',result.encode('utf-8-sig'),file_name=output_name,mime='text/plain')
        else:
            partial_name=output_name.replace('.txt','_PARTIAL.txt')
            partial=header+'\n翻訳状況: 部分翻訳（未完成）\n# 本文（日本語訳・途中まで）\n'+rendered
            st.download_button('途中経過TXTをダウンロード',partial.encode('utf-8-sig'),file_name=partial_name,mime='text/plain')
        st.text_area('翻訳プレビュー（冒頭）',rendered[:3500],height=260)
else:
    st.caption('TXTを選択するか、Googleドキュメントを読み込んでください。')
