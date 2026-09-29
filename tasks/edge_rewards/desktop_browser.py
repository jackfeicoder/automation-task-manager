"""Attach to the user's desktop Edge; own task pages only, never its profile."""
import json
from urllib.parse import urlsplit
from urllib.request import ProxyHandler, Request, build_opener


CONNECTION_HINT = ('桌面 Edge 尚未开放本机自动化连接。请先关闭所有 Edge 窗口，'
                   '在项目目录运行 ./scripts/start_edge.ps1，打开后回界面重跑。'
                   '脚本复用原有 Edge 配置和登录信息。')


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
    endpoint = endpoint_url(env)
    socket_url = probe_desktop_edge(endpoint)
    try:
        browser = playwright.chromium.connect_over_cdp(socket_url, timeout=5000, no_defaults=True)
    except Exception:
        raise ValueError(CONNECTION_HINT) from None
    if not browser.contexts:
        raise ValueError('桌面 Edge 没有可用的配置窗口，请用 start_edge.ps1 打开后重跑')
    return TaskPages(browser.contexts[0])
