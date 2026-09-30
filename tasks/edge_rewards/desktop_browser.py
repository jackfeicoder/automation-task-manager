"""Attach to the user's desktop Edge; own task pages only, never its profile."""
import json
import os
from pathlib import Path
import subprocess
import time
import psutil
from urllib.parse import urlsplit
from urllib.request import ProxyHandler, Request, build_opener


CONNECTION_HINT = ('当前 Edge 是普通启动，尚未开放本机自动化连接。请保存页面并退出 Edge，'
                   '再运行本任务，任务会自动打开原有配置；也可双击桌面的“Edge Rewards”入口，'
                   '或运行 ./scripts/start_edge.ps1。登录信息保留在原配置中。')


def edge_executable():
    if os.name != 'nt':
        raise ValueError('Rewards 插件需要 Windows 和 Microsoft Edge')
    import winreg
    registered = []
    # App Paths also covers installations relocated to another drive.
    for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        for view in (winreg.KEY_WOW64_64KEY, winreg.KEY_WOW64_32KEY):
            try:
                with winreg.OpenKey(hive, r'Software\Microsoft\Windows\CurrentVersion\App Paths\msedge.exe',
                                    0, winreg.KEY_READ | view) as key:
                    value = winreg.QueryValueEx(key, '')[0]
                path = Path(os.path.expandvars(str(value).strip('"')))
                if path.is_file():
                    registered.append(path)
            except OSError:
                pass
    drive = os.environ.get('SYSTEMDRIVE', 'C:')
    roots = [os.environ.get('LOCALAPPDATA'), os.environ.get('PROGRAMFILES(X86)'),
             os.environ.get('PROGRAMFILES'), os.environ.get('PROGRAMW6432'),
             drive + '/Program Files (x86)', drive + '/Program Files']
    for root in roots:
        if root:
            path = Path(root) / 'Microsoft/Edge/Application/msedge.exe'
            if path.is_file():
                return str(path)
    if registered:
        return str(registered[0])
    raise ValueError('未找到已安装的 Microsoft Edge；请先安装 Edge 后重跑')


def edge_is_running():
    for process in psutil.process_iter(['name']):
        try:
            if (process.info['name'] or '').lower() == 'msedge.exe':
                return True
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    return False


def start_desktop_edge(port):
    """Explorer launches Edge outside the worker's kill-on-close process job."""
    root = Path(__file__).resolve().parents[2]
    shell = Path(os.environ.get('SYSTEMROOT', 'C:/Windows')) / 'System32/WindowsPowerShell/v1.0/powershell.exe'
    env = dict(os.environ, TASK_HARBOR_EDGE_SCRIPT=str(root / 'scripts/start_edge.ps1'),
               TASK_HARBOR_EDGE_PORT=str(port), TASK_HARBOR_EDGE_ROOT=str(root))
    command = (
        "$ErrorActionPreference='Stop'; "
        "$taskEdgeShell=New-Object -ComObject Shell.Application; "
        "$taskEdgeArguments='-NoProfile -ExecutionPolicy Bypass -File '+"
        "[char]34+$env:TASK_HARBOR_EDGE_SCRIPT+[char]34+' -Reuse -Port '+$env:TASK_HARBOR_EDGE_PORT; "
        "$taskEdgeShell.ShellExecute('powershell.exe',$taskEdgeArguments,$env:TASK_HARBOR_EDGE_ROOT,'open',0)"
    )
    try:
        completed = subprocess.run([str(shell), '-NoProfile', '-NonInteractive', '-Command', command],
            env=env, capture_output=True, timeout=10, creationflags=subprocess.CREATE_NO_WINDOW)
        if completed.returncode:
            raise ValueError()
    except (OSError, subprocess.TimeoutExpired, ValueError):
        raise ValueError('桌面 Edge 自动启动未完成，请双击“Edge Rewards”入口，或运行 ./scripts/start_edge.ps1') from None


def ensure_desktop_edge(env):
    endpoint = endpoint_url(env)
    try:
        return probe_desktop_edge(endpoint)
    except ValueError as error:
        # Do not launch over a different browser, occupied port or invalid configuration.
        if str(error) != CONNECTION_HINT:
            raise
    if os.name != 'nt' or edge_is_running():
        raise ValueError(CONNECTION_HINT)
    if urlsplit(endpoint).hostname == '::1':
        raise ValueError('自动启动 Edge 请使用 http://127.0.0.1:<端口>；已有 IPv6 连接仍可直接使用')
    edge_executable()
    print('Edge 当前未运行，正在使用桌面原有配置自动打开连接…', flush=True)
    start_desktop_edge(urlsplit(endpoint).port)
    deadline = time.monotonic() + 25
    while time.monotonic() < deadline:
        try:
            return probe_desktop_edge(endpoint)
        except ValueError as error:
            if str(error) != CONNECTION_HINT:
                raise
        time.sleep(0.5)
    raise ValueError('Edge 连接启动超时。请打开“Edge Rewards”入口查看提示，或运行 ./scripts/start_edge.ps1')


def endpoint_url(env):
    value = env.get('REWARDS_EDGE_ENDPOINT', 'http://127.0.0.1:9222').strip()
    try:
        url = urlsplit(value)
        valid = (url.scheme == 'http' and url.hostname in ('127.0.0.1', 'localhost', '::1')
                 and url.port is not None and url.port > 0 and not url.username and not url.password
                 and url.path in ('', '/') and not url.query and not url.fragment)
    except ValueError:
        valid = False
    if not valid:
        raise ValueError('REWARDS_EDGE_ENDPOINT 应为本机 HTTP 地址，例如 http://127.0.0.1:9222')
    return value.rstrip('/')


def probe_desktop_edge(endpoint):
    # Local connection must stay local even when HTTP_PROXY is configured.
    try:
        opener = build_opener(ProxyHandler({}))
        with opener.open(Request(endpoint + '/json/version'), timeout=3) as response:
            document = json.loads(response.read(65536))
    except (OSError, ValueError):
        raise ValueError(CONNECTION_HINT) from None
    if not isinstance(document, dict):
        raise ValueError(CONNECTION_HINT)
    agent = str(document.get('User-Agent', ''))
    product = str(document.get('Browser', ''))
    if 'Edg/' not in agent and not product.startswith(('Edg/', 'Microsoft Edge/')):
        raise ValueError('连接端口对应的浏览器不是 Microsoft Edge，请检查 REWARDS_EDGE_ENDPOINT')
    socket_url = document.get('webSocketDebuggerUrl', '')
    try:
        socket = urlsplit(socket_url)
        local = (socket.scheme == 'ws' and socket.hostname in ('127.0.0.1', 'localhost', '::1')
                 and socket.port == urlsplit(endpoint).port and not socket.username
                 and not socket.password and socket.path.startswith('/devtools/browser/')
                 and not socket.query and not socket.fragment)
    except (ValueError, TypeError):
        local = False
    if not local:
        raise ValueError(CONNECTION_HINT)
    return socket_url


class TaskPages:
    """Expose only pages created for this task in an existing browser context."""
    def __init__(self, context):
        self.context = context
        self.owned = []
        self.routes = []
        self.listener = self._new_popup
        context.on('page', self.listener)

    @property
    def pages(self):
        return [page for page in self.owned if not page.is_closed()]

    def _own(self, page):
        if page not in self.owned:
            self.owned.append(page)
            # Set metrics on this task target only. Desktop windows may be
            # minimized or have a tiny client area while the task is running.
            page.set_viewport_size({'width': 1280, 'height': 900})
            for pattern, handler in self.routes:
                page.route(pattern, handler)

    def _new_popup(self, page):
        # User-created tabs and popups from their original pages stay untouched.
        try:
            if page.opener() in self.owned:
                self._own(page)
        except Exception:
            pass

    def new_page(self):
        page = self.context.new_page()
        self._own(page)
        return page

    def route(self, pattern, handler):
        self.routes.append((pattern, handler))
        for page in self.pages:
            page.route(pattern, handler)

    def close(self):
        try:
            for page in reversed(self.pages):
                try:
                    page.close()
                except Exception:
                    pass
        finally:
            self.context.remove_listener('page', self.listener)


def attach(playwright, env):
    socket_url = ensure_desktop_edge(env)
    try:
        browser = playwright.chromium.connect_over_cdp(socket_url, timeout=5000, no_defaults=True)
    except Exception:
        raise ValueError(CONNECTION_HINT) from None
    if not browser.contexts:
        raise ValueError('桌面 Edge 没有可用的配置窗口，请用 start_edge.ps1 打开后重跑')
    return TaskPages(browser.contexts[0])
