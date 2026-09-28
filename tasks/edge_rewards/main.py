"""Visible Edge Rewards activities and user-supplied desktop searches.

Independent implementation; no credentials, profiles or raw page captures in source.
"""
import argparse
from contextlib import contextmanager
from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import sys
import time
from urllib.parse import parse_qs, urlsplit

if __package__:
    from .network_guard import NetworkBlocked, NetworkGuard
else:
    from network_guard import NetworkBlocked, NetworkGuard

ROOT = Path(__file__).resolve().parents[2]
PROFILE = ROOT / 'data/profiles/edge-rewards'
QUERIES = ROOT / 'data/rewards/queries.txt'
DASHBOARD = 'https://rewards.bing.com/dashboard'
MICROSOFT_HOSTS = ('bing.com', 'bing.net', 'microsoft.com', 'microsoftonline.com',
    'live.com', 'msn.com', 'msauth.net', 'msauthimages.net', 'msftauth.net',
    'msftauthimages.net', 'msecnd.net', 'azureedge.net', 'akamaized.net')
COMPLETE = re.compile(r'completed|already earned|已完成|已获得|已獲得|已完成', re.I)
LOCKED = re.compile(r'locked|tomorrow|expired|not available|已锁定|已鎖定|明天|已过期', re.I)
EARN = re.compile(r'earn\s*\+?\s*\d+\s*points|(?:赚取|獲得|获得|贏取)\s*\d+\s*(?:积分|積分)|\+\s*\d+\s*(?:积分|積分)', re.I)


def result(status, message, **data):
    return {'status': status, 'message': message, 'data': data}


@dataclass
class Options:
    searches: int
    interval: int
    action_wait: int
    activities: int
    headless: bool

    @classmethod
    def load(cls, env):
        def number(name, default, minimum, maximum):
            try:
                value = int(env.get(name) or default)
            except ValueError:
                raise ValueError(f'{name} 应为整数') from None
            if not minimum <= value <= maximum:
                raise ValueError(f'{name} 应在 {minimum} 到 {maximum} 之间')
            return value
        headless = env.get('REWARDS_HEADLESS', 'false').lower().strip()
        if headless not in ('true', 'false', '1', '0'):
            raise ValueError('REWARDS_HEADLESS 应为 true 或 false')
        return cls(number('REWARDS_SEARCH_COUNT', 10, 0, 50),
            number('REWARDS_SEARCH_INTERVAL', 15, 10, 120),
            number('REWARDS_ACTION_WAIT', 10, 5, 60),
            number('REWARDS_MAX_ACTIVITIES', 10, 0, 30), headless in ('true', '1'))


@contextmanager
def profile_lock():
    # CLI login and scheduled tasks must not open the same browser profile together.
    sys.path.insert(0, str(ROOT))
    from backend.app.storage.instance import InstanceLock
    PROFILE.parent.mkdir(parents=True, exist_ok=True)
    lock = InstanceLock(PROFILE.parent / 'edge-rewards.lock')
    try:
        yield
    finally:
        lock.close()


def host_allowed(url):
    parsed = urlsplit(url)
    return (parsed.scheme == 'https' and not parsed.username and not parsed.password
        and parsed.port in (None, 443) and any(parsed.hostname == host or (parsed.hostname or '').endswith('.' + host) for host in MICROSOFT_HOSTS))


def gate_requests(context, guard):
    def route(request_route):
        try:
            guard.check()
            if not host_allowed(request_route.request.url):
                request_route.abort('blockedbyclient')
                return
            request_route.continue_()
        except NetworkBlocked as error:
            guard.error = str(error)
            request_route.abort('blockedbyclient')
    context.route('**/*', route)


def pause(page, guard, seconds):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        guard.check()
        page.wait_for_timeout(min(250, max(1, (deadline - time.monotonic()) * 1000)))
    guard.check(full=True)


def state(page):
    # Only return aggregate counters; never copy cookies, raw HTML or account fields.
    counters = page.evaluate('''() => {
        const root = window.dashboard;
        const user = root && (root.userStatus || root.userData);
        const number = value => {
            if (typeof value === 'number' && Number.isSafeInteger(value) && value >= 0) return value;
            if (typeof value === 'string' && /^\\s*[\\d,]+\\s*$/.test(value)) return Number(value.replace(/,/g, ''));
            return null;
        };
        let balance = user ? number(user.availablePoints) : null;
        if (balance === null) {
            for (const selector of ['#availablePoints', '#balanceToolTipDiv', '#id_rc', '[data-testid="rewards-points"]']) {
                const node = document.querySelector(selector);
                if (node && node.getBoundingClientRect().width) {
                    balance = number(node.innerText);
                    if (balance !== null) break;
                }
            }
        }
        if (balance === null) {
            const legacy = document.querySelector('mee-rewards-counter-animation');
            if (legacy) balance = number(legacy.textContent);
        }
        if (balance === null) {
            const text = document.body.innerText;
            const match = text.match(/(?:Available points|可用积分|可用積分)\\s*[:：]?\\s*([\\d,]+)/i)
                || text.match(/([\\d,]+)\\s*(?:Available points|可用积分|可用積分)/i);
            if (match) balance = number(match[1]);
        }
        const pc = user && user.counters && user.counters.pcSearch;
        const item = Array.isArray(pc) ? pc[0] : pc;
        const progress = item ? number(item.pointProgress) : null;
        const maximum = item ? number(item.pointProgressMax) : null;
        return {balance, progress, maximum};
    }''')
    text = page.locator('body').inner_text(timeout=10000)
    if re.search(r'looks like you.re on the go|unusual activity|account.*(?:restricted|suspended)|看起来你正在|异常活动|帳戶.*限制|账户.*限制|captcha|verify you.re human|验证.*真人', text, re.I):
        raise NetworkBlocked('Rewards 显示地区、账号或验证提示，请在客户端手动处理')
    parsed = urlsplit(page.url)
    if parsed.hostname not in ('rewards.bing.com', 'rewards.microsoft.com') or '/welcome' in parsed.path or counters['balance'] is None:
        return None
    return counters


def dashboard(page, guard, wait=3):
    guard.check(full=True)
    page.goto(DASHBOARD, wait_until='domcontentloaded', timeout=30000)
    pause(page, guard, wait)
    return state(page)


def cards(page, guard):
    # Expand only the daily-activity disclosure, never account/purchase/consent buttons.
    disclosure = page.get_by_role('button', name=re.compile(r'^(?:每日活动|每日活動|Daily activities|Daily activity)$', re.I))
    if disclosure.count() == 1 and disclosure.get_attribute('aria-expanded') == 'false':
        guard.check(full=True)
        disclosure.click(timeout=5000)
        pause(page, guard, 1)
    found = []
    anchors = page.locator('a[href]')
    for index in range(anchors.count()):
        anchor = anchors.nth(index)
        text = ' '.join((anchor.get_attribute('aria-label') or '', anchor.inner_text(), anchor.get_attribute('title') or ''))
        href = anchor.get_attribute('href') or ''
        if not anchor.is_visible() or not EARN.search(text) or COMPLETE.search(text) or LOCKED.search(text):
            continue
        url = urlsplit(href)
        form = parse_qs(url.query).get('form', [''])[0]
        # Task families requiring answers, downloads, purchases or app/device
        # impersonation remain manual. Restrict clicks to ordinary Bing search offers.
        if url.scheme != 'https' or url.hostname not in ('www.bing.com', 'bing.com') or url.path != '/search':
            continue
        if not re.match(r'^(?:ML2X|ML1|tgrew)', form, re.I):
            continue
        if re.search(r'quiz|puzzle|redeem|purchase|兑换|購買|购买|测验|測驗', text + href, re.I):
            continue
        if href not in {item['href'] for item in found}:
            found.append({'href': href})
    return found


def queries(count):
    if count == 0:
        return []
    if not QUERIES.is_file():
        raise ValueError('请先创建 data/rewards/queries.txt，每行填写一个搜索词；也可将搜索数量设为 0')
    lines = list(dict.fromkeys(line.strip() for line in QUERIES.read_text(encoding='utf-8-sig').splitlines() if line.strip() and not line.lstrip().startswith('#')))
    if not lines or any(len(line) > 200 for line in lines):
        raise ValueError('搜索词文件应包含非空搜索词，每行最多 200 字符')
    return lines[:count]


def run_tasks(context, page, guard, options, terms):
    before = dashboard(page, guard)
    if before is None:
        return result('needs_login', '登录状态或积分余额未识别，请先运行 --login；若已登录，请更新页面适配')
    completed = 0
    searches = 0
    balance = before['balance']
    print('已读取积分余额，开始处理支持的任务。', flush=True)
    offers = cards(page, guard)[:options.activities]
    for index, offer in enumerate(offers, 1):
        guard.check(full=True)
        anchor = page.locator('a[href]')
        # Attribute comparison avoids interpolating untrusted card URLs into CSS.
        match = None
        for candidate in anchor.all():
            if candidate.is_visible() and candidate.get_attribute('href') == offer['href']:
                match = candidate
                break
        if match is None:
            continue
        old_pages = list(context.pages)
        match.click(timeout=10000)
        pause(page, guard, options.action_wait)
        for child in context.pages:
            if child not in old_pages:
                child.close()
        current = dashboard(page, guard)
        if current is None or current['balance'] <= balance:
            return result('needs_attention', '活动积分增长未确认，已停止后续操作', activities_completed=completed, searches_completed=searches)
        completed += 1
        balance = current['balance']
        print(f'已确认第 {index} 项活动积分到账。', flush=True)
    search_page = context.new_page()
    for index, term in enumerate(terms, 1):
        current = state(page)
        if current is None:
            return result('needs_attention', '积分状态读取失败，停止搜索', activities_completed=completed, searches_completed=searches)
        if current['maximum'] == 0:
            return result('needs_attention', '账号未显示可用桌面搜索积分额度，停止搜索', activities_completed=completed, searches_completed=searches)
        if current['maximum'] is not None and current['maximum'] > 0 and current['progress'] is not None and current['progress'] >= current['maximum']:
            print('桌面搜索额度已完成。', flush=True)
            break
        guard.check(full=True)
        search_page.goto('https://www.bing.com/', wait_until='domcontentloaded', timeout=30000)
        field = search_page.locator('#sb_form_q')
        field.wait_for(state='visible', timeout=10000)
        field.fill(term)
        guard.check(full=True)
        field.press('Enter')
        search_page.wait_for_url(re.compile(r'^https://(?:www\.)?bing\.com/search\?'), timeout=30000)
        pause(search_page, guard, options.interval)
        current = dashboard(page, guard)
        if current is None or current['balance'] <= balance:
            return result('needs_attention', '搜索积分增长未确认，可能有冷却或页面变化；已停止后续搜索', activities_completed=completed, searches_completed=searches)
        searches += 1
        balance = current['balance']
        print(f'已确认第 {index} 次搜索积分到账。', flush=True)
    guard.check(full=True)
    earned = balance - before['balance']
    if earned > 0:
        return result('success', f'已确认本次增加 {earned} 积分', credits=earned,
            activities_completed=completed, searches_completed=searches)
    if before['maximum'] is not None and before['maximum'] > 0 and before['progress'] is not None and before['progress'] >= before['maximum'] and not offers:
        return result('already_completed', '桌面搜索额度已完成，未发现支持的未完成活动')
    return result('needs_attention', '未确认新增积分；页面中其他任务请手动完成')


def local_environment():
    from dotenv import dotenv_values
    env = dict(os.environ)
    # CLI login and managed runs share the same country/configuration, never a password.
    sources = [dotenv_values(ROOT / '.env', interpolate=False)]
    path = ROOT / 'data/environment.json'
    if path.is_file():
        sources.append(json.loads(path.read_text(encoding='utf-8-sig')))
    for source in sources:
        env.update({key: value for key, value in source.items() if key.startswith('REWARDS_') and isinstance(value, str)})
    return env


def execute(mode='run', env=None):
    guard = None
    try:
        env = local_environment() if env is None else env
        options = Options.load(env)
        terms = queries(options.searches) if mode == 'run' else []
        guard = NetworkGuard(env)
        print('检查 VPN、代理、网卡、路由和出口地区…', flush=True)
        guard.start()  # Must complete before importing/launching a browser.
        if mode == 'doctor':
            return result('success', '本次网络检查通过；运行任务时会再次检查', country=guard.country)
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            return result('needs_attention', '请先安装 requirements-browser.txt 中的浏览器依赖')
        with profile_lock(), sync_playwright() as playwright:
            guard.check(full=True)
            context = playwright.chromium.launch_persistent_context(str(PROFILE), channel='msedge',
                headless=False if mode == 'login' else options.headless, service_workers='block',
                args=['--no-proxy-server', '--disable-extensions', '--disable-background-networking',
                    '--disable-component-update', '--disable-sync', '--no-first-run', '--no-default-browser-check'])
            try:
                gate_requests(context, guard)
                page = context.pages[0] if context.pages else context.new_page()
                if mode == 'login':
                    guard.check(full=True)
                    page.goto(DASHBOARD, wait_until='domcontentloaded', timeout=30000)
                    print('请在 Edge 窗口手动登录微软账号；登录信息仅保存在本机独立配置目录。', flush=True)
                    deadline = time.monotonic() + 600
                    while time.monotonic() < deadline:
                        pause(page, guard, 2)
                        if state(page):
                            guard.check(full=True)
                            page.goto('https://www.bing.com/', wait_until='domcontentloaded', timeout=30000)
                            pause(page, guard, 3)
                            if dashboard(page, guard):
                                return result('success', '登录状态已保存在本机，可手动运行 Rewards 任务')
                    return result('needs_login', '登录等待超时，请重新运行 --login')
                return run_tasks(context, page, guard, options, terms)
            finally:
                context.close()
    except NetworkBlocked as error:
        return result('needs_attention', '网络保护已拦截：' + str(error))
    except ValueError as error:
        return result('needs_attention', str(error))
    except Exception:
        if guard and guard.error:
            return result('needs_attention', '网络保护已拦截：' + guard.error)
        return result('needs_attention', 'Edge 操作未完成；请检查 Edge 安装、独立配置目录占用、登录状态或页面适配')
    finally:
        if guard:
            guard.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument('--login', action='store_true', help='通过网络检查后打开 Edge，手动登录')
    modes.add_argument('--doctor', action='store_true', help='仅检查网络，不访问微软网站')
    modes.add_argument('--run', action='store_true', help='执行积分任务')
    args = parser.parse_args()
    outcome = execute('login' if args.login else 'doctor' if args.doctor else 'run')
    print('AUTOMATION_RESULT=' + json.dumps(outcome, ensure_ascii=False), flush=True)
    sys.exit(0 if outcome['status'] in ('success', 'already_completed') else 1)
