"""Company master sheet: maps a company to its IR audio page, transcript folder and analysis note."""
import os
import time
from urllib.parse import quote

import streamlit as st
from google_drive_integration import google_api

DEFAULT_SHEET_ID = "1NMjMQ6JeUuRRyy4X7m79YCTnklUgkdE76407QYY5ahQ"
DEFAULT_SHEET_NAME = "global_company_watch_300"
HEADER_ROW = 2
# Header text in row 2 -> internal key. Matching by header keeps working if columns move.
COLUMNS = {
    "Ticker": "ticker",
    "企業名": "company",
    "日本名": "japanese_name",
    "本拠地": "country",
    "決算資料": "results_url",
    "音声": "audio_page",
    "フォルダ": "root_folder",
    "Quarterly_Analysis": "transcript_folder",
    "05_決算分析ノート": "note_url",
    "05更新": "note_updated",
}
CACHE_SECONDS = 600


def _setting(name, default):
    try:
        value = st.secrets.get(name, "")
    except Exception:
        value = ""
    return str(value or os.getenv(name, "") or default).strip()


def sheet_location():
    return _setting("COMPANY_SHEET_ID", DEFAULT_SHEET_ID), _setting("COMPANY_SHEET_NAME", DEFAULT_SHEET_NAME)


def column_letter(index):
    """0-based column index -> A1 letter (0 -> A, 29 -> AD)."""
    letters = ""
    index += 1
    while index:
        index, rem = divmod(index - 1, 26)
        letters = chr(65 + rem) + letters
    return letters


def parse_rows(values):
    """Convert raw sheet values (starting at the header row) into company dicts."""
    if not values:
        return [], {}
    header = [str(x).strip() for x in values[0]]
    positions = {key: header.index(name) for name, key in COLUMNS.items() if name in header}
    missing = [name for name, key in COLUMNS.items() if key in ("company", "transcript_folder", "note_url")
               and key not in positions]
    if missing:
        raise RuntimeError("企業マスターに必要な列見出しがありません: " + ", ".join(missing))
    companies = []
    for offset, row in enumerate(values[1:], start=1):
        item = {key: (str(row[pos]).strip() if pos < len(row) else "") for key, pos in positions.items()}
        if not item.get("company"):
            continue
        item["row"] = HEADER_ROW + offset
        companies.append(item)
    return companies, positions


def load_companies(force=False):
    cached = st.session_state.get("company_registry_cache")
    if cached and not force and time.time() - cached["at"] < CACHE_SECONDS:
        return cached["companies"]
    sheet_id, sheet_name = sheet_location()
    rng = quote(f"'{sheet_name}'!A{HEADER_ROW}:DZ", safe="")
    data = google_api("GET", f"https://sheets.googleapis.com/v4/spreadsheets/{sheet_id}/values/{rng}")
    companies, positions = parse_rows(data.get("values", []))
    st.session_state["company_registry_cache"] = {"at": time.time(), "companies": companies,
                                                  "positions": positions}
    return companies


def label(item):
    names = item["company"]
    if item.get("japanese_name") and item["japanese_name"] != item["company"]:
        names += f"（{item['japanese_name']}）"
    return f"{item.get('ticker') or '-'}｜{names}"


def guess_company(companies, *texts):
    """Pick the company whose name/ticker appears in a page title or URL (longest match wins)."""
    haystack = " ".join(t for t in texts if t).lower()
    if not haystack:
        return None
    best, best_len = None, 0
    for item in companies:
        for name in (item.get("company"), item.get("japanese_name"), item.get("ticker")):
            name = (name or "").strip()
            # Short tickers ("A", "MU") match too many words; require 3+ characters.
            if len(name) >= 3 and name.lower() in haystack and len(name) > best_len:
                best, best_len = item, len(name)
    return best


def _column_position(key):
    positions = st.session_state.get("company_registry_cache", {}).get("positions") or {}
    if key not in positions:
        load_companies(force=True)
        positions = st.session_state["company_registry_cache"]["positions"]
    if key not in positions:
        header = next(name for name, k in COLUMNS.items() if k == key)
        raise RuntimeError(f"企業マスターに「{header}」列が見つかりません。")
    return positions[key]


def _write_cell(item, key, value, input_option="RAW"):
    sheet_id, sheet_name = sheet_location()
    cell = f"'{sheet_name}'!{column_letter(_column_position(key))}{item['row']}"
    google_api("PUT", f"https://sheets.googleapis.com/v4/spreadsheets/{sheet_id}/values/{quote(cell, safe='')}",
               params={"valueInputOption": input_option}, json={"range": cell, "values": [[value]]})
    item[key] = value


def write_note_url(item, url):
    """Record a newly created analysis note back into the 05_決算分析ノート column."""
    _write_cell(item, "note_url", url)


def _write_dates(updates):
    """Batch-write 05更新 dates as real dates formatted yyyy-mm-dd."""
    sheet_id, sheet_name = sheet_location()
    col = _column_position("note_updated")
    letter = column_letter(col)
    google_api("POST", f"https://sheets.googleapis.com/v4/spreadsheets/{sheet_id}/values:batchUpdate",
               json={"valueInputOption": "USER_ENTERED",
                     "data": [{"range": f"'{sheet_name}'!{letter}{item['row']}", "values": [[day]]}
                              for item, day in updates]})
    meta = google_api("GET", f"https://sheets.googleapis.com/v4/spreadsheets/{sheet_id}",
                      params={"fields": "sheets.properties(sheetId,title)"})
    tab = next(x["properties"]["sheetId"] for x in meta.get("sheets", []) if x["properties"]["title"] == sheet_name)
    google_api("POST", f"https://sheets.googleapis.com/v4/spreadsheets/{sheet_id}:batchUpdate", json={"requests": [{
        "repeatCell": {"range": {"sheetId": tab, "startRowIndex": item["row"] - 1, "endRowIndex": item["row"],
                                 "startColumnIndex": col, "endColumnIndex": col + 1},
                       "cell": {"userEnteredFormat": {"numberFormat": {"type": "DATE", "pattern": "yyyy-mm-dd"}}},
                       "fields": "userEnteredFormat.numberFormat"}} for item, _ in updates]})
    for item, day in updates:
        item["note_updated"] = day


def write_note_updated(item, day=None):
    """Record today's date (JST) in 05更新 after a quarter was added to the analysis note.

    Entered as a real date shown as yyyy-mm-dd, like the neighbouring Financial更新日 column."""
    from datetime import datetime
    from zoneinfo import ZoneInfo
    day = day or datetime.now(ZoneInfo("Asia/Tokyo")).strftime("%Y-%m-%d")
    _write_dates([(item, day)])
    return day
