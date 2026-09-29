"""A short-lived, authenticated loopback bridge for the desktop Chrome extension."""
import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import re
import secrets
import threading
import time

ROOT = Path(__file__).resolve().parents[2]
PORT = 18765
MESSAGES = {
    'success': '已完成退出并通过 GitHub 重新登录',
    'needs_login': 'GitHub 登录或授权需要手动处理；请先在桌面 Chrome 完成登录后重跑',
    'needs_attention': '页面未按预期完成退出或重新登录，请检查 Agent Router 页面后重跑',
}
STAGES = {'initial': '正在加载工作页面', 'menu': '正在打开账户菜单',
          'logout': '已点击退出，等待登录页面', 'login': '准备使用 GitHub 登录',
          'oauth': '已点击 GitHub 登录，等待返回控制台'}


class Bridge(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, secret, timeout=165, address=('127.0.0.1', PORT)):
        super().__init__(address, Handler)
        self.secret = secret
        self.job_id = secrets.token_hex(16)
        self.deadline = time.monotonic() + timeout
        self.connected = threading.Event()
        self.finished = threading.Event()
        self.lock = threading.Lock()
        self.result = None
        self.rejected = False
        self.stage = None


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass  # Never log Authorization headers, callback URLs or page contents.

    def allowed(self):
        port = self.server.server_address[1]
        host = self.headers.get('Host', '')
        origin = self.headers.get('Origin', '')
        # Privileged extension GET requests can omit Origin. A strong per-install
        # pairing secret is still mandatory; regular webpage origins stay denied.
        return (host == f'127.0.0.1:{port}' and
                (not origin or bool(re.fullmatch(r'chrome-extension://[a-p]{32}', origin))))

    def send_json(self, code, payload):
        self.send_response(code)
        if self.allowed() and self.headers.get('Origin'):
            self.send_header('Access-Control-Allow-Origin', self.headers['Origin'])
            self.send_header('Vary', 'Origin')
        self.send_header('Cache-Control', 'no-store')
        self.send_header('Content-Type', 'application/json')
        self.end_headers()
        self.wfile.write(json.dumps(payload).encode())

    def do_OPTIONS(self):
        if not self.allowed():
            self.send_json(403, {})
            return
        self.send_response(204)
        if self.headers.get('Origin'):
            self.send_header('Access-Control-Allow-Origin', self.headers['Origin'])
        self.send_header('Access-Control-Allow-Headers', 'Authorization, Content-Type')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST')
        self.end_headers()

    def authenticated(self):
        valid = self.allowed() and secrets.compare_digest(
            self.headers.get('Authorization', ''), 'Bearer ' + self.server.secret)
        if not valid and not self.server.rejected:
            self.server.rejected = True
            reason = '请求来源或本机地址不匹配' if not self.allowed() else '本机配对信息不匹配'
            print('扩展连接未通过验证：' + reason, flush=True)
        return valid

    def do_GET(self):
        if not self.authenticated():
            self.send_json(403, {})
            return
        if self.path != '/job':
            self.send_json(404, {})
            return
        if not self.server.connected.is_set():
            print('桌面 Chrome 扩展已连接', flush=True)
            self.server.connected.set()
        if self.server.finished.is_set() or time.monotonic() >= self.server.deadline:
            self.send_json(200, {'job': None})
        else:
            self.send_json(200, {'job': self.server.job_id, 'remaining_seconds':
                max(0, int(self.server.deadline - time.monotonic()))})

    def do_POST(self):
        if not self.authenticated():
            self.send_json(403, {})
            return
        if self.path not in {'/result', '/progress'}:
            self.send_json(404, {})
            return
        try:
            size = int(self.headers.get('Content-Length', '0'))
            if not 0 < size <= 1024:
                raise ValueError()
            body = json.loads(self.rfile.read(size))
            if self.path == '/progress':
                stage = body.get('stage')
                if body.get('job') != self.server.job_id or stage not in STAGES:
                    raise ValueError()
                if stage != self.server.stage:
                    self.server.stage = stage
                    print(STAGES[stage], flush=True)
                self.send_json(200, {'accepted': True})
                return
            status = body['status']
            if body.get('job') != self.server.job_id or status not in MESSAGES:
                raise ValueError()
            # A successful result needs both milestones, not merely an existing session.
            if status == 'success' and not (body.get('login_clicked') is True and
                                            body.get('logged_in') is True):
                raise ValueError()
        except (ValueError, KeyError, TypeError):
            self.send_json(422, {})
            return
        with self.server.lock:
            if self.server.finished.is_set() or time.monotonic() >= self.server.deadline:
                self.send_json(409, {})
                return
            self.server.result = {'status': status, 'message': MESSAGES[status]}
            self.server.finished.set()
        self.send_json(200, {'accepted': True})


def emit(status, message):
    print('AUTOMATION_RESULT=' + json.dumps(
        {'status': status, 'message': message}, ensure_ascii=False), flush=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--doctor', action='store_true')
    args = parser.parse_args()
    try:
        config = json.loads((ROOT / 'data/agentrouter/bridge.json').read_text('utf-8'))
        secret = config['secret']
        if not isinstance(secret, str) or len(secret) < 40:
            raise ValueError()
    except (OSError, ValueError, KeyError, TypeError):
        emit('needs_attention', '请先运行 .\\scripts\\setup_agentrouter.ps1 并在桌面 Chrome 加载生成的本地扩展')
        return
    if args.doctor:
        emit('success', '本机扩展配置已生成；请确认桌面 Chrome 已加载该扩展并保持打开')
        return
    try:
        server = Bridge(secret)
    except OSError:
        emit('needs_attention', '本机签到连接端口 18765 已占用，请等待上一次任务结束后重跑')
        return
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        print('等待桌面 Chrome 签到扩展连接；只处理本任务创建的页面', flush=True)
        if not server.connected.wait(45):
            emit('needs_attention', '桌面 Chrome 扩展未连接：请打开已登录的 Chrome 并加载 data/agentrouter/chrome-extension，然后重跑')
        elif server.finished.wait(max(0, server.deadline - time.monotonic())):
            emit(**server.result)
        else:
            emit('needs_attention', '登录流程超时；请在 Chrome 检查 GitHub 登录、验证或授权提示后重跑')
    finally:
        server.shutdown()
        server.server_close()


if __name__ == '__main__':
    main()
