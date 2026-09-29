"""DOM-based offer discovery for the legacy dashboard and current earn page."""
import re
from urllib.parse import parse_qs, unquote, urljoin, urlsplit

EARN_PAGE = 'https://rewards.bing.com/earn'
DONE = re.compile(r'completed|already earned|已完成|已获得|已獲得', re.I)
LOCKED = re.compile(r'locked|tomorrow|expired|需要.*级别|明天|已锁定|已过期', re.I)
POINTS = re.compile(r'\+\s*(\d+)|earn\s+(\d+)\s+points|赚取\s*(\d+)\s*积分', re.I)

# Extract displayed task cards only. Never export HTML, cookies or account fields.
CARD_DOM = r'''() => Array.from(document.querySelectorAll('main a[href]')).map(a => {
    const section = a.closest('[role="group"]');
    const title = a.querySelector('h3, p, img[alt]');
    return {href: a.getAttribute('href'),
        title: title ? (title.innerText || title.getAttribute('alt') || '') : a.innerText,
        text: a.innerText, group: section ? section.getAttribute('aria-label') ||
            (section.getAttribute('aria-labelledby') || '').split(/\s+/).map(id => document.getElementById(id)?.innerText || '').join(' ') : '',
        disabled: a.getAttribute('aria-disabled') === 'true',
        visible: !!a.getBoundingClientRect().width};
})'''


def clean(value):
    return ' '.join(value.replace('\u200b', '').split())


def identity(card):
    # Different explore cards intentionally share the SAME homepage URL.
    return (card['source'], card['href'], clean(card['title']))


def bing_url(href):
    try:
        url = urlsplit(href)
        return (url.scheme == 'https' and url.hostname in ('bing.com', 'www.bing.com')
            and not url.username and not url.password and url.port in (None, 443))
    except ValueError:
        return False


def classify(card):
    text = clean(card['text'])
    href = card['href']
    if DONE.search(text):
        return 'completed', '页面已完成'
    if card.get('disabled') or LOCKED.search(text):
        return 'waiting', '未解锁、等级限制或跨天等待'
    try:
        url = urlsplit(href)
        valid = url.scheme == 'https' and not url.username and not url.password and url.port in (None, 443)
    except ValueError:
        valid = False
    if not valid:
        return 'manual', '需要桌面客户端或外部应用'
    if url.hostname == 'rewards.bing.com' and url.path.startswith('/earn/quest/'):
        return ('quest', '') if POINTS.search(text) else ('manual', '应用、订阅或虚拟物品任务')
    if url.path.startswith('/redeem') or re.search(r'purchase|mastercard|game pass|抽奖|订阅|购买|兑换|安装|下载', text, re.I):
        return 'manual', '需要安装、订阅、兑换或购买'
    if bing_url(href):
        args = {k.lower(): v for k, v in parse_qs(url.query).items()}
        if url.path == '/rewards/checkuser':
            target = unquote(args.get('ru', [''])[0])
            if not target.startswith('/search?') or target.startswith('//'):
                return 'manual', '未识别的活动跳转'
            return 'visit', '问答或投票；仅在服务端确认完成时记为成功'
        if url.path == '/search' and POINTS.search(text):
            return 'visit', ''
        if url.path in ('', '/') and args.get('form', [''])[0].upper() == 'ML2PCR':
            return 'explore', ''
        if card.get('quest_child') and url.path == '/search':
            return 'visit', ''
    if POINTS.search(text) and url.hostname in ('www.youtube.com', 'www.tiktok.com') and re.match(r'^/(?:@microsoftrewards|channel/UC_JBS0jA4xrt0G-qDVOYooA)(?:/|$)', url.path):
        return 'visit', ''
    if card.get('quest_child') and url.hostname == 'rewards.bing.com' and url.path in ('/earn', '/about', '/dashboard', '/dashboard/'):
        return 'visit', ''
    return 'manual', '当前任务需手动处理'


def discover(page):
    found = []
    seen = set()
    child = '/earn/quest/' in urlsplit(page.url).path
    for raw in page.evaluate(CARD_DOM):
        if not raw['visible'] or (not raw['group'] and not child):
            continue
        card = {**raw, 'href': urljoin(page.url, raw['href']),
            'title': clean(raw['title']), 'source': page.url.split('?')[0],
            'quest_child': child}
        key = identity(card)
        if key in seen:
            continue
        kind, reason = classify(card)
        card.update(kind=kind, reason=reason)
        seen.add(key)
        found.append(card)
    return found


def find_card(page, card):
    for anchor in page.locator('main a[href]').all():
        if not anchor.is_visible():
            continue
        if urljoin(page.url, anchor.get_attribute('href') or '') != card['href']:
            continue
        title = anchor.locator('h3, p, img[alt]').first
        text = title.inner_text() if title.count() and title.evaluate('(e) => e.tagName') != 'IMG' else (title.get_attribute('alt') if title.count() else anchor.inner_text())
        if clean(text or '') == card['title']:
            return anchor
    return None


def quota_text(text):
    # A doubled quota displays "10/100 50": the struck-out old cap is NOT 50.
    match = re.search(r'(?:必应搜索|Bing searches?|Bing search)\s*(?:已激活[^\n]*|[^\n]*activated[^\n]*)?\s*(\d[\d,]*)\s*/\s*(\d[\d,]*)', text, re.I)
    if not match:
        return None
    used, maximum = (int(part.replace(',', '')) for part in match.groups())
    return (used, maximum) if 0 <= used <= maximum else None


def explore_term(card):
    text = card['text']
    for pattern, term in [(r'航班|flight', '上海到北京航班'),
        (r'保险|insurance', '旅行保险计划比较'),
        (r'贷款|loan', '个人贷款与学生贷款选项比较'),
        (r'购物|shopping', '无线键盘优惠'),
        (r'美容|香水|beauty', '护发产品比较'),
        (r'手机套餐|mobile plan', '手机套餐比较'),
        (r'邮轮|cruise', '邮轮旅行目的地'),
        (r'互联网套餐|internet plan', '家庭宽带套餐比较')]:
        if re.search(pattern, text, re.I):
            return term
    return None
