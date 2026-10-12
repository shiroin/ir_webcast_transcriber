import re
import time
import requests
API="https://api.openai.com/v1/chat/completions"

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
