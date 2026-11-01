"""Google OAuth and Drive Docs upload shared by transcription and translation pages."""
import os
import time
import re
from urllib.parse import urlencode, urlparse
import requests
import streamlit as st
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired

DRIVE_SCOPE = "https://www.googleapis.com/auth/drive"

def _google_settings():
    try:
        values = dict(st.secrets)
    except Exception:
        values = {}
    def setting(name):
        return str(values.get(name, os.getenv(name, ""))).strip()
    return setting("GOOGLE_CLIENT_ID"), setting("GOOGLE_CLIENT_SECRET"), setting("GOOGLE_REDIRECT_URI"), setting("GOOGLE_OAUTH_STATE_SECRET")

def _secret_refresh_token():
    """Long-lived refresh token in Secrets removes the per-session Google login."""
    try:
        value = st.secrets.get("GOOGLE_REFRESH_TOKEN", "")
    except Exception:
        value = ""
    return str(value or os.getenv("GOOGLE_REFRESH_TOKEN", "")).strip()

def ensure_google_auth():
    """Return True when Drive calls can proceed, using Secrets if no session login."""
    if st.session_state.get("google_drive_tokens"):
        return True
    refresh = _secret_refresh_token()
    if not refresh:
        return False
    # expires_at=0 makes _google_access_token refresh immediately.
    st.session_state["google_drive_tokens"] = {"access_token": "pending", "refresh_token": refresh,
                                               "expires_at": 0, "from_secrets": True}
    try:
        _google_access_token()
        return True
    except Exception as exc:
        st.session_state.pop("google_drive_tokens", None)
        st.warning(f"SecretsのGOOGLE_REFRESH_TOKENで接続できませんでした: {exc}")
        return False

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
    from workflow_utils import oauth_preferences
    state=_google_signer().dumps({"nonce":nonce,"v":1,"prefs":oauth_preferences(),
                                  "page":st.session_state.get("ir_page", "")})
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
        from workflow_utils import restore_oauth_preferences
        restore_oauth_preferences(payload.get("prefs", {}))
        page = payload.get("page")
        if page in ("📄 PDF → 全文翻訳 → Google Drive", "📑 Earnings + Follow-up 統合"):
            st.session_state["ir_page"] = page
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
    if st.session_state.get("google_drive_tokens", {}).get("from_secrets") or (
            not st.session_state.get("google_drive_tokens") and ensure_google_auth()):
        st.success("Google Driveに自動接続済み（SecretsのGOOGLE_REFRESH_TOKEN）")
        return
    if st.session_state.get("google_drive_tokens"):
        st.success("Google Driveに接続済み")
        refresh = st.session_state["google_drive_tokens"].get("refresh_token")
        if refresh:
            with st.expander("次回からログイン不要にする（初回のみ）"):
                st.caption("以下をStreamlit CloudのSecretsに GOOGLE_REFRESH_TOKEN として保存すると、"
                           "以後Googleログインが不要になります。第三者に共有しないでください。"
                           "保存する場合は、アプリを非公開にするか APP_PASSWORD を設定してください。")
                st.code(f'GOOGLE_REFRESH_TOKEN = "{refresh}"', language="toml")
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


def parse_folder_id(folder_url):
    value=(folder_url or "").strip()
    parsed=urlparse(value)
    if parsed.hostname not in ("drive.google.com","www.drive.google.com"):
        raise ValueError("Google DriveフォルダURLを入力してください。")
    match=re.search(r"/folders/([A-Za-z0-9_-]+)",parsed.path)
    if not match:
        raise ValueError("URLに /folders/フォルダID がありません。")
    return match.group(1)

def upload_as_google_doc(folder_id, filename, text):
    """Import UTF-8 text as a native Google Doc in the target folder."""
    from requests_toolbelt.multipart.encoder import MultipartEncoder
    token=_google_access_token()
    if not text.strip():
        raise ValueError("空のテキストはアップロードできません。")
    title=filename[:-4] if filename.lower().endswith(".txt") else filename
    metadata={"name":title,"mimeType":"application/vnd.google-apps.document","parents":[folder_id]}
    import json
    multipart=MultipartEncoder(fields={
        "metadata":("metadata",json.dumps(metadata,ensure_ascii=False).encode("utf-8"),"application/json; charset=UTF-8"),
        "file":(filename,text.encode("utf-8"),"text/plain; charset=UTF-8"),
    })
    response=requests.post(
        "https://www.googleapis.com/upload/drive/v3/files",
        params={"uploadType":"multipart","fields":"id,name,mimeType,webViewLink","supportsAllDrives":"true"},
        headers={"Authorization":f"Bearer {token}","Content-Type":multipart.content_type},
        data=multipart,timeout=120,
    )
    if not response.ok:
        raise RuntimeError(f"Google Drive API HTTP {response.status_code}: {response.text[:700]}")
    result=response.json()
    if result.get("mimeType")!="application/vnd.google-apps.document":
        raise RuntimeError("Googleドキュメント形式で作成されたことを確認できませんでした。")
    return result

def existing_google_doc(folder_id, title):
    """Avoid duplicate docs after a partial failure or repeated click."""
    token=_google_access_token()
    safe=title.replace("\\","\\\\").replace("'","\\'")
    query=f"'{folder_id}' in parents and name = '{safe}' and mimeType = 'application/vnd.google-apps.document' and trashed = false"
    response=requests.get("https://www.googleapis.com/drive/v3/files",params={
        "q":query,"fields":"files(id,name,mimeType,webViewLink)","pageSize":100,
        "supportsAllDrives":"true","includeItemsFromAllDrives":"true"
    },headers={"Authorization":f"Bearer {token}"},timeout=30)
    response.raise_for_status()
    return (response.json().get("files") or [None])[0]

def google_api(method, url, **kwargs):
    """Authorized call to any Google REST API (Drive / Docs / Sheets) with readable errors."""
    if not ensure_google_auth():
        raise RuntimeError("Google Driveに接続されていません。ログインするか GOOGLE_REFRESH_TOKEN を設定してください。")
    headers = {"Authorization": f"Bearer {_google_access_token()}", **kwargs.pop("headers", {})}
    response = requests.request(method, url, headers=headers, timeout=kwargs.pop("timeout", 60), **kwargs)
    if not response.ok:
        raise RuntimeError(f"Google API HTTP {response.status_code}: {response.text[:600]}")
    return response.json() if response.content else {}

def parse_doc_id(url):
    """Accept docs.google.com/document/d/<id> and drive.google.com/file/d/<id> links."""
    match = re.search(r"/(?:document|file)/d/([A-Za-z0-9_-]+)", (url or "").strip())
    if not match:
        raise ValueError(f"GoogleドキュメントのURLを認識できません: {url}")
    return match.group(1)
