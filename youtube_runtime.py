"""Configure a supported JS runtime for yt-dlp YouTube EJS challenges."""
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.request
import zipfile
from pathlib import Path


def _version(cmd):
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=8)
        return (p.stdout or p.stderr).strip() if p.returncode == 0 else ''
    except (OSError, subprocess.TimeoutExpired):
        return ''


def _node_supported():
    path = shutil.which('node')
    if not path:
        return None
    version = _version([path, '--version'])
    m = re.match(r'v(\d+)\.', version)
    return path if m and int(m.group(1)) >= 22 else None


def _deno_supported(path):
    if not path or not Path(path).is_file():
        return False
    m = re.search(r'(\d+)\.(\d+)\.(\d+)', _version([str(path), '--version']))
    return bool(m and tuple(map(int, m.groups())) >= (2, 3, 0))


def _install_deno():
    """Best-effort bounded official binary install to ephemeral app cache.

    Failure does not crash Streamlit; user gets a clear diagnostic.
    """
    machine = platform.machine().lower()
    system = platform.system().lower()
    if system != 'linux' or machine not in ('x86_64', 'amd64', 'aarch64', 'arm64'):
        raise RuntimeError(f'Deno自動導入の対象外: {system}/{machine}')
    target = 'x86_64-unknown-linux-gnu' if machine in ('x86_64', 'amd64') else 'aarch64-unknown-linux-gnu'
    root = Path(tempfile.gettempdir()) / 'ir_transcriber_deno'
    root.mkdir(parents=True, exist_ok=True)
    binary = root / 'deno'
    if _deno_supported(binary):
        return str(binary)
    url = f'https://github.com/denoland/deno/releases/download/v2.5.6/deno-{target}.zip'
    archive = root / 'deno.zip'
    try:
        request = urllib.request.Request(url, headers={'User-Agent': 'IR-Webcast-Transcriber/1.0'})
        with urllib.request.urlopen(request, timeout=25) as response, archive.open('wb') as output:
            total = 0
            while True:
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if total > 100 * 1024 * 1024:
                    raise RuntimeError('Denoの配布ファイルが100MBを超えました')
                output.write(chunk)
        with zipfile.ZipFile(archive) as z:
            if 'deno' not in z.namelist():
                raise RuntimeError('Denoの配布ファイルに実行ファイルがありません')
            with z.open('deno') as src, binary.open('wb') as dst:
                shutil.copyfileobj(src, dst)
        binary.chmod(0o755)
        if not _deno_supported(binary):
            raise RuntimeError('導入したDenoが正常に起動しません')
        return str(binary)
    finally:
        archive.unlink(missing_ok=True)


def youtube_js_args():
    deno = shutil.which('deno')
    if _deno_supported(deno):
        return ['--js-runtimes', f'deno:{deno}'], f'Deno: {_version([deno, "--version"]).splitlines()[0]}'
    node = _node_supported()
    if node:
        return ['--js-runtimes', f'node:{node}'], f'Node.js: {_version([node, "--version"])}'
    try:
        deno = _install_deno()
        return ['--js-runtimes', f'deno:{deno}'], f'Deno: {_version([deno, "--version"]).splitlines()[0]}'
    except Exception as exc:
        raise RuntimeError('YouTube用JavaScript実行環境を用意できません。'
                           'Deno 2.3以降またはNode.js 22以降が必要です。'
                           f' 自動導入のエラー: {exc}') from exc
