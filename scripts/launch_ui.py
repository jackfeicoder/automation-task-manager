"""Desktop entry point: reuse a running server or start it, then open the UI."""
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from urllib.request import ProxyHandler, build_opener

ROOT = Path(__file__).resolve().parents[1]


def launch():
    from dotenv import load_dotenv
    load_dotenv(ROOT / '.env')
    host = os.environ.get('AUTOMATION_HOST', '127.0.0.1')
    if host not in ('127.0.0.1', 'localhost', '::1'):
        raise ValueError('AUTOMATION_HOST 应设置为本机地址')
    port = int(os.environ.get('AUTOMATION_PORT', '8765'))
    if not 1 <= port <= 65535:
        raise ValueError('AUTOMATION_PORT 应在 1 到 65535 之间')
    url = f'http://{"[::1]" if host == "::1" else host}:{port}'
    opener = build_opener(ProxyHandler({}))

    def ready():
        try:
            with opener.open(url + '/api/health', timeout=1) as response:
                return json.loads(response.read(4096)).get('status') == 'ok'
        except (OSError, ValueError, AttributeError):
            return False

    if not ready():
        pythonw = ROOT / '.venv/Scripts/pythonw.exe'
        if not pythonw.is_file():
            raise RuntimeError('请先在项目目录运行 scripts/setup.ps1')
        process = subprocess.Popen([str(pythonw), str(ROOT / 'scripts/background.py')],
            cwd=ROOT, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, creationflags=subprocess.CREATE_NO_WINDOW)
        deadline = time.monotonic() + 30
        while not ready():
            if process.poll() is not None or time.monotonic() >= deadline:
                raise RuntimeError('服务启动未完成，请查看项目 data/server.log')
            time.sleep(0.5)
    os.startfile(url)


if __name__ == '__main__':
    try:
        launch()
    except Exception as error:
        import ctypes
        ctypes.windll.user32.MessageBoxW(None, str(error), '脚本管理器启动提示', 0x10)
        sys.exit(1)
