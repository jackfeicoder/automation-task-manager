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
from urllib.parse import urlencode, urlsplit

if __package__:
    from .network_guard import NetworkBlocked, NetworkGuard
    from .desktop_browser import attach, endpoint_url, probe_desktop_edge
    from .page_tasks import EARN_PAGE, discover, explore_term, find_card, identity, quota_text
else:
    from network_guard import NetworkBlocked, NetworkGuard
    from desktop_browser import attach, endpoint_url, probe_desktop_edge
    from page_tasks import EARN_PAGE, discover, explore_term, find_card, identity, quota_text

ROOT = Path(__file__).resolve().parents[2]
QUERIES = ROOT / 'data/rewards/queries.txt'
EXAMPLE_QUERIES = Path(__file__).with_name('queries.example.txt')
DASHBOARD = 'https://rewards.bing.com/dashboard'
MICROSOFT_HOSTS = ('bing.com', 'bing.net', 'microsoft.com', 'microsoftonline.com',
    'live.com', 'msn.com', 'msauth.net', 'msauthimages.net', 'msftauth.net',
    'msftauthimages.net', 'msecnd.net', 'azureedge.net', 'akamaized.net',
    'youtube.com', 'ytimg.com', 'googlevideo.com', 'tiktok.com', 'tiktokcdn.com', 'tiktokv.com')


def result(status, message, **data):
    return {'status': status, 'message': message, 'data': data}


def edge_executable():
    if os.name != 'nt':
        raise ValueError('Rewards 插件需要 Windows 和 Microsoft Edge')
    drive = os.environ.get('SYSTEMDRIVE', 'C:')
    roots = [os.environ.get('LOCALAPPDATA'), os.environ.get('PROGRAMFILES(X86)'),
             os.environ.get('PROGRAMFILES'), os.environ.get('PROGRAMW6432'),
             drive + '/Program Files (x86)', drive + '/Program Files']
    for root in roots:
        if root:
            path = Path(root) / 'Microsoft/Edge/Application/msedge.exe'
            if path.is_file():
                return str(path)
    raise ValueError('未找到已安装的 Microsoft Edge；请先安装 Edge 后重跑')


def browser_failure(error, stage):
    # Surface useful browser codes without returning URLs, tokens or raw traces.
    code = re.search(r'\bERR_[A-Z_]+\b', str(error))
    if code:
        hints = {
            'ERR_PROXY_CONNECTION_FAILED': '当前系统代理连接失败，请检查代理程序是否运行及代理端口',
            'ERR_TUNNEL_CONNECTION_FAILED': '当前代理隧道连接失败，请检查代理线路',
            'ERR_NAME_NOT_RESOLVED': '域名解析失败，请检查当前网络的 DNS',
            'ERR_INTERNET_DISCONNECTED': '当前网络未连接',
            'ERR_CONNECTION_TIMED_OUT': '网站连接超时，请检查当前网络或稍后重跑',
            'ERR_CERT_AUTHORITY_INVALID': '网站证书验证失败，请在 Edge 中检查证书提示',
        }
        identifier = code.group()
        return f'{stage}未完成：{identifier}；' + hints.get(identifier, '请检查当前网络或在 Edge 中手动打开网站')
    if type(error).__name__ == 'TimeoutError':
        return f'{stage}超时；请检查当前网络是否能打开 Rewards，或稍后重跑'
    hint = '请检查 Edge 安装及独立窗口是否被占用' if stage == '启动 Edge' else '请检查页面是否能打开、登录是否有效及页面适配'
    return f'{stage}未完成（{type(error).__name__}）；{hint}'


@dataclass
class Options:
    searches: int
    interval: int
    action_wait: int
    activities: int
    headless: bool
    poll_option: str = ''

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
        preference = env.get('REWARDS_POLL_OPTION', '').strip()
        if len(preference) > 200:
            raise ValueError('REWARDS_POLL_OPTION 最多 200 字符')
        return cls(number('REWARDS_SEARCH_COUNT', 10, 0, 50),
            number('REWARDS_SEARCH_INTERVAL', 15, 10, 120),
            number('REWARDS_ACTION_WAIT', 10, 5, 60),
            number('REWARDS_MAX_ACTIVITIES', 30, 0, 100), headless in ('true', '1'), preference)


@contextmanager
def profile_lock():
    # One Rewards operation at a time; the actual profile remains owned by Edge.
    sys.path.insert(0, str(ROOT))
    from backend.app.storage.instance import InstanceLock
    directory = ROOT / 'data/rewards'
    directory.mkdir(parents=True, exist_ok=True)
    try:
        lock = InstanceLock(directory / 'edge-rewards.lock')
    except RuntimeError:
        raise ValueError('另一个 Rewards 操作正在使用桌面 Edge；请结束后重跑') from None
    try:
        yield
    finally:
        lock.close()


def host_allowed(url):
    try:
        parsed = urlsplit(url)
        return (parsed.scheme == 'https' and not parsed.username and not parsed.password
            and parsed.port in (None, 443) and any(parsed.hostname == host or (parsed.hostname or '').endswith('.' + host) for host in MICROSOFT_HOSTS))
    except ValueError:
        return False


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
            const profile = Array.from(document.querySelectorAll('button[aria-label]'))
                .find(n => /^(查看个人资料|View profile|Profile)$/i.test(n.getAttribute('aria-label') || ''));
            if (profile) {
                for (const node of profile.querySelectorAll('p')) {
                    balance = number(node.innerText);
                    if (balance !== null) break;
                }
            }
        }
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
    page.bring_to_front()
    page.goto(DASHBOARD, wait_until='domcontentloaded', timeout=30000)
    page.bring_to_front()
    pause(page, guard, wait)
    return loaded_state(page, guard)


def loaded_state(page, guard, timeout=12):
    # The current React page can finish DOM loading before profile counters arrive.
    deadline = time.monotonic() + timeout
    while True:
        dismiss_completion(page, guard)
        current = state(page)
        if current is not None or time.monotonic() >= deadline:
            return current
        pause(page, guard, 1)


def dismiss_completion(page, guard):
    # This delayed success notice blocks the underlying controls after refresh.
    # Only its explicit Close button is handled; other dialogs stay untouched.
    dialog = page.get_by_role('dialog', name=re.compile(r'^(干得漂亮|Well done|Nicely done)$', re.I))
    if dialog.count() == 1 and dialog.is_visible():
        close = dialog.get_by_role('button', name=re.compile(r'^(关闭|Close)$', re.I))
        if close.count() == 1:
            guard.check(full=True)
            # The notice can place its footer below a small desktop viewport.
            # Keyboard activation uses the same explicit Close control.
            close.press('Enter', timeout=5000)
            pause(page, guard, 1)
            return True
    return False


def click_ready(control, page, guard, timeout=5000):
    dismiss_completion(page, guard)
    try:
        control.click(timeout=timeout)
    except Exception as error:
        if type(error).__name__ != 'TimeoutError' or not dismiss_completion(page, guard):
            raise
        control.click(timeout=timeout)


def view(page, guard, url):
    guard.check(full=True)
    page.bring_to_front()
    page.goto(url, wait_until='domcontentloaded', timeout=30000)
    page.bring_to_front()
    pause(page, guard, 3)
    current = loaded_state(page, guard)
    if current is None:
        parsed = urlsplit(page.url)
        rewards_page = (parsed.hostname in ('rewards.bing.com', 'rewards.microsoft.com')
                        and '/welcome' not in parsed.path)
        # Balance and activity widgets load independently. A working points
        # control is enough for reading quota; balance is required at run start/end.
        points_control = page.get_by_role('button', name=re.compile(r'^(今日积分|Today.s points)', re.I))
        balance_link = page.get_by_role('link', name=re.compile(r'^(可用积分|Available points)', re.I))
        if not rewards_page or not (points_control.count() == 1 or balance_link.count() == 1):
            raise ValueError('页面登录状态或积分余额未识别')
    return current


def expand_tasks(page, guard):
    names = re.compile(r'^(每日活动|Daily activities|Daily activity|在必应上浏览|Explore on Bing|日常任务|More activities|任务|Quests)$', re.I)
    for button in page.get_by_role('button', name=names).all():
        if button.get_attribute('aria-expanded') == 'false':
            button.press('Enter', timeout=5000)
            pause(page, guard, 1)
    more = page.get_by_role('button', name=re.compile(r'^(显示更多|Show more)$', re.I))
    if more.count() == 1 and more.is_visible():
        more.press('Enter', timeout=5000)
        pause(page, guard, 1)


def read_quota(page, guard):
    view(page, guard, EARN_PAGE)
    button = page.get_by_role('button', name=re.compile(r'^(今日积分|Today.s points)', re.I))
    if button.count() != 1:
        return None
    dismiss_completion(page, guard)
    button.press('Enter', timeout=5000)
    pause(page, guard, 1)
    dialog = page.get_by_role('dialog', name=re.compile(r'^(积分明细|Points breakdown)$', re.I))
    dialog.wait_for(state='visible', timeout=10000)
    if dialog.count() != 1:
        raise ValueError('搜索额度窗口未识别')
    quota = quota_text(dialog.inner_text())
    close = dialog.get_by_role('button', name=re.compile(r'^(关闭|Close)$', re.I))
    close.first.press('Enter', timeout=5000)
    pause(page, guard, 1)
    return quota


def close_children(context, original):
    # Close only pages created by this operation, including unexpected popups.
    for child in list(context.pages):
        if child not in original:
            try:
                child.close()
            except Exception:
                pass


def submit_search(page, guard, term):
    page.bring_to_front()
    guard.check(full=True)
    # Use Bing's ordinary visible search URL. Its homepage field can render
    # before keyboard handlers initialize, silently dropping Enter presses.
    page.goto('https://www.bing.com/search?' + urlencode({'q': term}),
              wait_until='domcontentloaded', timeout=30000)
    # Search results are usable before delayed images or widgets finish loading.
    page.wait_for_url(re.compile(r'^https://(?:www\.)?bing\.com/search\?'),
                      wait_until='domcontentloaded', timeout=30000)


def answer_quiz(page, guard, options):
    # Match displayed questions to explicit local answers. Unknown questions skip;
    # never inspect hidden answers, click random options or handle CAPTCHAs.
    answer_file = ROOT / 'data/rewards/quiz-answers.json'
    if not answer_file.is_file():
        answer_file = Path(__file__).with_name('quiz-answers.example.json')
    answers = json.loads(answer_file.read_text(encoding='utf-8-sig'))
    if not isinstance(answers, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in answers.items()):
        return
    handled = set()
    for _ in range(5):
        text = page.locator('body').inner_text()
        if re.search(r'captcha|verify you.re human|验证.*真人|异常活动', text, re.I):
            raise NetworkBlocked('页面显示验证提示，请在客户端手动处理')
        question = next((q for q in answers if q in text and q not in handled), None)
        if question is None:
            return
        choice = page.get_by_role('link', name=answers[question], exact=True)
        if choice.count() != 1:
            return
        handled.add(question)
        guard.check(full=True)
        choice.click(timeout=5000)
        pause(page, guard, 3)
        next_button = page.get_by_role('button', name=re.compile(r'^(下一题|Next question|查看结果|See results)$', re.I))
        if next_button.count() == 1:
            next_button.click(timeout=5000)
            pause(page, guard, 3)


def vote_poll(page, guard, preference):
    # An empty preference only opens the poll and verifies completion. A user can
    # configure the exact visible option in the UI, without storing a credential.
    if not preference:
        return
    choice = page.get_by_role('link', name=preference, exact=True)
    if choice.count() == 1:
        guard.check(full=True)
        choice.click(timeout=5000)
        pause(page, guard, 3)


def visit_offer(context, page, guard, options, card):
    view(page, guard, card['source'])
    expand_tasks(page, guard)
    live = next((c for c in discover(page) if identity(c) == identity(card)), None)
    if live is None or live['kind'] == 'completed':
        return 'already_completed', '任务已完成或不再显示'
    if live['kind'] in ('manual', 'waiting'):
        return 'skipped', live['reason']
    anchor = find_card(page, live)
    if anchor is None:
        return 'skipped', '任务入口已变化'
    original = list(context.pages)
    try:
        guard.check(full=True)
        click_ready(anchor, page, guard, timeout=10000)
        pause(page, guard, 2)
        if live['kind'] == 'explore':
            term = explore_term(live)
            if not term:
                return 'skipped', '未识别此任务的搜索主题'
            child = next((p for p in context.pages if p not in original), None)
            if child is None:
                return 'skipped', '任务未打开搜索页'
            submit_search(child, guard, term)
        pause(page, guard, options.action_wait)
        child = next((p for p in context.pages if p not in original), None)
        if child is not None and '/rewards/checkuser' in live['href']:
            if 'PollScenarioId' in live['href'] or re.search(r'投票|poll', live['text'], re.I):
                vote_poll(child, guard, options.poll_option)
            else:
                answer_quiz(child, guard, options)
    finally:
        close_children(context, original)
    view(page, guard, card['source'])
    expand_tasks(page, guard)
    after = next((c for c in discover(page) if identity(c) == identity(card)), None)
    if after and after['kind'] == 'completed':
        return 'completed', '页面确认已完成'
    if card['quest_child'] and after is None:
        return 'progress_confirmed', '子任务入口已完成并移除；整体奖励以最终余额为准'
    # Some quizzes credit on opening. If not complete, do not guess answers or loop.
    return 'skipped', '完成状态未确认；可能需答题、客户端或冷却，继续其他任务'


def claim_pending(page, guard, options):
    view(page, guard, DASHBOARD)
    button = page.get_by_role('button', name=re.compile(r'^(可领取|Claimable)', re.I))
    if button.count() != 1:
        return
    match = re.search(r'\b([\d,]+)\b', button.inner_text())
    if not match or int(match[1].replace(',', '')) == 0:
        return
    dismiss_completion(page, guard)
    button.press('Enter', timeout=5000)
    pause(page, guard, 1)
    dialog = page.get_by_role('dialog', name=re.compile(r'^(领取积分|Claim points)$', re.I))
    dialog.wait_for(state='visible', timeout=10000)
    claim = dialog.get_by_role('button', name=re.compile(r'^\d[\d,]*\s*(?:待领取|pending)[\s\S]*?(?:领取积分|Claim points)$', re.I))
    if claim.count() == 1:
        guard.check(full=True)
        claim.press('Enter', timeout=5000)
        pause(page, guard, options.action_wait)
    else:
        close = dialog.get_by_role('button', name=re.compile(r'^(关闭|Close)$', re.I))
        if close.count():
            close.first.click(timeout=5000)


def browse_home_sections(page, guard):
    for name in ['福利', '你的进度', '每日活动', '活动', '精选兑换', '成就勋章']:
        button = page.get_by_role('button', name=name, exact=True)
        if button.count() == 1 and button.get_attribute('aria-expanded') == 'false':
            guard.check(full=True)
            button.press('Enter', timeout=5000)
            pause(page, guard, 2)


def queries(count):
    if count == 0:
        return []
    source = QUERIES if QUERIES.is_file() else EXAMPLE_QUERIES
    lines = list(dict.fromkeys(line.strip() for line in source.read_text(encoding='utf-8-sig').splitlines() if line.strip() and not line.lstrip().startswith('#')))
    if not lines or any(len(line) > 200 for line in lines):
        raise ValueError('搜索词文件应包含非空搜索词，每行最多 200 字符')
    return lines[:count]


def run_tasks(context, page, guard, options, terms):
    before = dashboard(page, guard)
    if before is None:
        return result('needs_login', '桌面 Edge 当前配置未登录 Rewards 或余额未识别。请在该 Edge 的 Rewards 页面确认登录，或用 start_edge.ps1 -ProfileDirectory "Profile 1" 选择你已登录的配置后重跑')
    ledger = []
    attempts = 0
    seen = set()
    print('读取首页和赚取页；完成页面立即关闭，单项失败继续。', flush=True)

    def note(title, status, reason):
        ledger.append({'task': title, 'status': status, 'reason': reason})
        print(f'{title}: {reason}', flush=True)

    def attempt(card):
        nonlocal attempts
        if attempts >= options.activities:
            note(card['title'], 'skipped', '本次活动数量上限')
            return
        attempts += 1
        try:
            status, reason = visit_offer(context, page, guard, options, card)
            note(card['title'], status, reason)
        except NetworkBlocked:
            raise
        except Exception as error:
            note(card['title'], 'skipped', f'操作未完成（{type(error).__name__}），继续下一项')

    if options.activities:
        try:
            claim_pending(page, guard, options)
            view(page, guard, DASHBOARD)
            browse_home_sections(page, guard)
        except NetworkBlocked:
            raise
        except Exception as error:
            note('待领取积分与首页栏目', 'skipped', f'页面需适配（{type(error).__name__}）')
        # Refresh discovery after each page: a level upgrade can unlock new offers.
        for source in [DASHBOARD, EARN_PAGE]:
            try:
                view(page, guard, source)
                expand_tasks(page, guard)
                offers = discover(page)
            except NetworkBlocked:
                raise
            except Exception as error:
                note('活动列表', 'skipped', f'页面未识别（{type(error).__name__}），继续下一页')
                continue
            for card in offers:
                if identity(card) in seen:
                    continue
                seen.add(identity(card))
                if card['kind'] == 'completed':
                    continue
                if card['kind'] in ('manual', 'waiting'):
                    note(card['title'], 'skipped', card['reason'])
                elif card['kind'] == 'quest':
                    try:
                        view(page, guard, card['href'])
                        children = discover(page)
                        for child in children:
                            if child['kind'] in ('visit', 'explore'):
                                attempt(child)
                            elif child['kind'] != 'completed':
                                note(card['title'] + ' / ' + child['title'], 'skipped', child['reason'])
                    except NetworkBlocked:
                        raise
                    except Exception as error:
                        note(card['title'], 'skipped', f'子任务页面未识别（{type(error).__name__}）')
                else:
                    attempt(card)

    searches = 0
    search_reason = '未设置搜索词'
    quota = None
    search_page = None
    try:
        quota = read_quota(page, guard) if terms else None
        for term in terms:
            if quota is None or quota[1] <= 0:
                search_reason = '搜索额度未识别，跳过搜索'
                break
            if quota[0] >= quota[1]:
                search_reason = '当前搜索额度已完成'
                break
            if search_page is None:
                search_page = context.new_page()
            guard.check(full=True)
            submit_search(search_page, guard, term)
            pause(search_page, guard, options.interval)
            updated = read_quota(page, guard)
            if updated is not None and updated[0] == quota[0]:
                # Rewards may credit asynchronously. Recheck once without issuing
                # another search, then give up if the counter still did not move.
                pause(page, guard, options.interval)
                updated = read_quota(page, guard)
            if updated is None or updated[0] <= quota[0]:
                search_reason = '搜索积分增长未确认，跳过本轮剩余搜索'
                break
            quota = updated
            searches += 1
            search_reason = '当前搜索额度已完成' if quota[0] >= quota[1] else '已用完本轮搜索词或数量额度'
    except NetworkBlocked:
        raise
    except Exception as error:
        search_reason = f'搜索页面未完成（{type(error).__name__}），结束本轮搜索'
    finally:
        if search_page:
            close_children(context, [page])
    note('桌面搜索', 'completed' if quota and quota[0] >= quota[1] else 'skipped', search_reason)
    # A quest may leave new pending credits. Claim once, never loop without progress.
    if options.activities:
        try:
            claim_pending(page, guard, options)
        except NetworkBlocked:
            raise
        except Exception:
            note('领取积分', 'skipped', '领取入口未识别')
    after = dashboard(page, guard)
    earned = after['balance'] - before['balance'] if after else 0
    completed = sum(item['status'] in ('completed', 'progress_confirmed') for item in ledger)
    skipped = sum(item['status'] == 'skipped' for item in ledger)
    outcome = result('success' if earned > 0 or completed else 'needs_attention',
        f'本次确认增加 {earned} 积分；完成/推进 {completed} 项，跳过 {skipped} 项',
        credits=earned, activities_completed=completed, searches_completed=searches,
        search_progress=list(quota) if quota else None, tasks=ledger)
    report = ROOT / 'data/rewards/last-run.json'
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(json.dumps(outcome, ensure_ascii=False, indent=2), encoding='utf-8')
    return outcome


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
    stage = '配置读取'
    try:
        env = local_environment() if env is None else env
        options = Options.load(env)
        terms = []
        if mode == 'run':
            try:
                terms = queries(options.searches)
            except (ValueError, OSError):
                print('桌面搜索词表缺失、为空或格式异常；本轮继续处理当天活动，跳过额外桌面搜索。', flush=True)
        guard = NetworkGuard(env)
        print('检查 VPN、代理和地区…' if guard.enabled else 'VPN、系统代理、WinHTTP 代理和出口地区检查已关闭；使用当前网络。', flush=True)
        guard.start()
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            return result('needs_attention', '请先安装 requirements-browser.txt 中的浏览器依赖')
        edge_executable()
        if mode == 'doctor':
            probe_desktop_edge(endpoint_url(env))
            return result('success', '桌面 Edge 连接已就绪；' + ('网络检查已通过' if guard.enabled else 'VPN / 代理不阻断任务'), network_check=guard.enabled)
        stage = '连接桌面 Edge'
        with profile_lock(), sync_playwright() as playwright:
            guard.check(full=True)
            context = attach(playwright, env)
            try:
                stage = '加载 Rewards 页面'
                gate_requests(context, guard)
                page = context.new_page()
                if mode == 'login':
                    guard.check(full=True)
                    page.goto(DASHBOARD, wait_until='domcontentloaded', timeout=30000)
                    print('请在桌面 Edge 的任务页面确认微软登录；使用原有浏览器配置。', flush=True)
                    deadline = time.monotonic() + 600
                    while time.monotonic() < deadline:
                        pause(page, guard, 2)
                        if state(page):
                            guard.check(full=True)
                            page.goto('https://www.bing.com/', wait_until='domcontentloaded', timeout=30000)
                            pause(page, guard, 3)
                            if dashboard(page, guard):
                                return result('success', '桌面 Edge 的 Rewards 登录已就绪，可手动运行任务')
                    return result('needs_login', '登录等待超时，请重新运行 --login')
                return run_tasks(context, page, guard, options, terms)
            finally:
                # Cleanup must not override the actual task result or failure.
                try:
                    context.close()
                except Exception:
                    print('任务页面清理未完成；请关闭本任务打开的页面。', flush=True)
    except NetworkBlocked as error:
        return result('needs_attention', '任务需要处理：' + str(error))
    except ValueError as error:
        return result('needs_attention', str(error))
    except Exception as error:
        if guard and guard.error:
            return result('needs_attention', '任务需要处理：' + guard.error)
        return result('needs_attention', browser_failure(error, stage))
    finally:
        if guard:
            guard.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument('--login', action='store_true', help='连接桌面 Edge，在任务页面确认登录')
    modes.add_argument('--doctor', action='store_true', help='检查依赖及可选网络检查，不访问微软网站')
    modes.add_argument('--run', action='store_true', help='执行积分任务')
    args = parser.parse_args()
    outcome = execute('login' if args.login else 'doctor' if args.doctor else 'run')
    print('AUTOMATION_RESULT=' + json.dumps(outcome, ensure_ascii=False), flush=True)
    sys.exit(0 if outcome['status'] in ('success', 'already_completed') else 1)
