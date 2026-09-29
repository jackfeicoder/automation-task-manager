"""Rewards regressions using public card text and offline doubles, no account traffic."""
import json
from types import SimpleNamespace

import pytest

from tasks.edge_rewards import main as rewards
from tasks.edge_rewards.page_tasks import classify, discover, explore_term, identity, quota_text
from tasks.edge_rewards.network_guard import NetworkBlocked


def card(title='航班', href='https://www.bing.com/?form=ML2PCR', **extra):
    return dict(title=title, href=href, text=title + ' +10', source=rewards.EARN_PAGE,
                quest_child=False, disabled=False, **extra)


@pytest.mark.parametrize('href,text,expected', [
    ('https://www.bing.com/search?q=birds', '鸟类 +15', 'visit'),
    ('https://www.bing.com/rewards/checkuser?ru=%2Fsearch%3Fq%3Dquiz', '问答 +30', 'visit'),
    ('https://www.bing.com/rewards/checkuser?ru=https%3A%2F%2Fexample.org', '问答 +30', 'manual'),
    ('https://www.bing.com.evil.example/search?q=birds', '鸟类 +15', 'manual'),
    ('https://www.bing.com:444/search?q=birds', '鸟类 +15', 'manual'),
    ('https://www.bing.com:invalid/search', '鸟类 +15', 'manual'),
    ('ms-search://query?query=birds', '鸟类 +100', 'manual'),
    ('https://rewards.bing.com/redeem/goal', '设置兑换目标 +5', 'manual'),
    ('https://rewards.bing.com/earn/quest/dashboard', '认识新首页 +100', 'quest'),
    ('https://rewards.bing.com/earn/quest/spotify', 'Spotify 订阅', 'manual'),
    ('https://www.youtube.com/@microsoftrewards', '观看视频 +10', 'visit'),
    ('https://www.youtube.com/@someoneelse', '观看视频 +10', 'manual'),
])
def test_offer_allowlist(href, text, expected):
    offer = card(href=href)
    offer['text'] = text
    assert classify(offer)[0] == expected


def test_completed_and_locked_tasks_do_not_run():
    offer = card()
    offer['text'] += ' 已完成'
    assert classify(offer)[0] == 'completed'
    offer['text'] = '航班 +10 明天解锁'
    assert classify(offer)[0] == 'waiting'
    offer['text'] = '航班 +10'
    offer['disabled'] = True
    assert classify(offer)[0] == 'waiting'


def test_different_explore_cards_with_same_url_are_preserved():
    rows = [dict(href='https://www.bing.com/?form=ML2PCR', title=title,
                 text=title + ' +10', group='在必应上浏览', disabled=False, visible=True)
            for title in ['航班', '保险', '航班']]
    page = SimpleNamespace(url=rewards.EARN_PAGE, evaluate=lambda script: rows)
    found = discover(page)
    assert [c['title'] for c in found] == ['航班', '保险']
    assert all(c['kind'] == 'explore' for c in found)
    assert identity(found[0]) != identity(found[1])
    assert explore_term(found[0]) != explore_term(found[1])


@pytest.mark.parametrize('text,expected', [
    ('积分明细\n必应搜索\n已激活 2x 倍增\n20/100 50\n活动 371', (20, 100)),
    ('Bing searches\n10 / 50', (10, 50)),
    ('必应搜索\n100/100', (100, 100)),
    ('必应搜索\n101/100', None),
    ('今日积分 391 活动 371', None),
])
def test_current_search_cap_not_old_struck_out_cap(text, expected):
    assert quota_text(text) == expected


def test_searches_fall_back_to_non_sensitive_example_terms(monkeypatch, tmp_path):
    monkeypatch.setattr(rewards, 'ROOT', tmp_path)
    monkeypatch.setattr(rewards, 'QUERIES', tmp_path / 'missing.txt')
    example = tmp_path / 'queries.example.txt'
    example.write_text('# comment\nfirst term\nfirst term\nsecond term\n', encoding='utf-8')
    monkeypatch.setattr(rewards, 'EXAMPLE_QUERIES', example)
    assert rewards.queries(3) == ['first term', 'second term']


def setup_run(monkeypatch, tmp_path, offers):
    page = SimpleNamespace(url=rewards.DASHBOARD)
    context = SimpleNamespace(pages=[page])
    guard = SimpleNamespace(check=lambda **kw: None)
    balances = iter([100, 115])
    monkeypatch.setattr(rewards, 'ROOT', tmp_path)
    monkeypatch.setattr(rewards, 'dashboard', lambda *args: {'balance': next(balances)})
    monkeypatch.setattr(rewards, 'view', lambda p, g, url: setattr(p, 'url', url))
    monkeypatch.setattr(rewards, 'expand_tasks', lambda *args: None)
    monkeypatch.setattr(rewards, 'claim_pending', lambda *args: None)
    monkeypatch.setattr(rewards, 'browse_home_sections', lambda *args: None)
    monkeypatch.setattr(rewards, 'discover', lambda p: offers if p.url == rewards.EARN_PAGE else [])
    return context, page, guard, rewards.Options(0, 15, 10, 30, True)


def test_failed_offer_does_not_block_next_offer(monkeypatch, tmp_path):
    offers = [dict(card(title=title), kind='visit', reason='') for title in ['broken', 'working']]
    args = setup_run(monkeypatch, tmp_path, offers)
    visited = []

    def visit(*args):
        title = args[-1]['title']
        visited.append(title)
        if title == 'broken':
            raise TimeoutError('offline timeout')
        return 'completed', '页面确认已完成'

    monkeypatch.setattr(rewards, 'visit_offer', visit)
    outcome = rewards.run_tasks(*args, [])
    assert visited == ['broken', 'working']
    assert outcome['data']['credits'] == 15
    assert [x['status'] for x in outcome['data']['tasks'][:2]] == ['skipped', 'completed']
    assert json.loads((tmp_path / 'data/rewards/last-run.json').read_text('utf-8')) == outcome


def test_network_guard_failure_stops_all_account_actions(monkeypatch, tmp_path):
    offers = [dict(card(title=title), kind='visit', reason='') for title in ['first', 'second']]
    args = setup_run(monkeypatch, tmp_path, offers)
    visited = []

    def visit(*args):
        visited.append(args[-1]['title'])
        raise NetworkBlocked('VPN detected')

    monkeypatch.setattr(rewards, 'visit_offer', visit)
    with pytest.raises(NetworkBlocked):
        rewards.run_tasks(*args, [])
    assert visited == ['first']


def test_popups_close_even_when_offer_action_times_out(monkeypatch):
    offer = dict(card(), kind='visit', reason='')
    page = SimpleNamespace(url=rewards.EARN_PAGE)
    closed = []
    child = SimpleNamespace(close=lambda: closed.append('child'))
    context = SimpleNamespace(pages=[page])

    def click(**kwargs):
        context.pages.append(child)
        raise TimeoutError('popup action timeout')

    monkeypatch.setattr(rewards, 'view', lambda *args: None)
    monkeypatch.setattr(rewards, 'expand_tasks', lambda *args: None)
    monkeypatch.setattr(rewards, 'discover', lambda p: [offer])
    monkeypatch.setattr(rewards, 'find_card', lambda *args: SimpleNamespace(click=click))
    monkeypatch.setattr(rewards, 'dismiss_completion', lambda *args: False)
    guard = SimpleNamespace(check=lambda **kw: None)
    with pytest.raises(TimeoutError):
        rewards.visit_offer(context, page, guard, rewards.Options(0, 15, 10, 30, True), offer)
    assert closed == ['child']


def test_unknown_quiz_never_chooses_random_answer():
    page = SimpleNamespace(locator=lambda selector: SimpleNamespace(inner_text=lambda: 'Unknown question'))
    rewards.answer_quiz(page, None, None)  # No choice locator or click is available.


def test_search_accepts_ready_results_when_other_resources_are_still_loading():
    from urllib.parse import parse_qs, urlsplit
    events = []

    def goto(url, **kwargs):
        assert kwargs['wait_until'] == 'domcontentloaded'
        assert urlsplit(url).hostname == 'www.bing.com'
        events.append(parse_qs(urlsplit(url).query)['q'][0])

    def wait_for_url(pattern, **kwargs):
        assert pattern.match('https://www.bing.com/search?q=venv')
        if kwargs.get('wait_until') != 'domcontentloaded':
            raise TimeoutError('A delayed widget never completes the load event')

    page = SimpleNamespace(goto=goto, wait_for_url=wait_for_url,
                           bring_to_front=lambda: None)
    rewards.submit_search(page, SimpleNamespace(check=lambda **kw: None), 'venv 中文 & a+b')
    assert events == ['venv 中文 & a+b']


def test_profile_counters_can_arrive_after_dom_loaded(monkeypatch):
    responses = iter([None, None, {'balance': 100, 'progress': 25, 'maximum': 100}])
    waits = []
    monkeypatch.setattr(rewards, 'state', lambda page: next(responses))
    monkeypatch.setattr(rewards, 'dismiss_completion', lambda *args: False)
    monkeypatch.setattr(rewards, 'pause', lambda p,g,seconds: waits.append(seconds))
    assert rewards.loaded_state(None, None)['balance'] == 100
    assert waits == [1, 1]


def test_missing_login_does_not_wait_forever(monkeypatch):
    monkeypatch.setattr(rewards, 'state', lambda page: None)
    monkeypatch.setattr(rewards, 'dismiss_completion', lambda *args: False)
    assert rewards.loaded_state(None, None, timeout=0) is None


def test_click_retries_once_only_after_known_completion_notice_is_closed(monkeypatch):
    dismissals = iter([False, True])
    attempts = []
    monkeypatch.setattr(rewards, 'dismiss_completion', lambda *args: next(dismissals))

    def click(**kwargs):
        attempts.append(True)
        if len(attempts) == 1:
            raise TimeoutError('Completion notice intercepted the click')

    rewards.click_ready(SimpleNamespace(click=click), None, None)
    assert len(attempts) == 2


def test_other_blocking_dialog_does_not_trigger_click_retry(monkeypatch):
    attempts = []
    monkeypatch.setattr(rewards, 'dismiss_completion', lambda *args: False)

    def click(**kwargs):
        attempts.append(True)
        raise TimeoutError('An unrecognized dialog is blocking the page')

    with pytest.raises(TimeoutError):
        rewards.click_ready(SimpleNamespace(click=click), None, None)
    assert len(attempts) == 1


def test_claim_waits_for_dialog_and_accepts_multiline_button(monkeypatch):
    ready = False
    claimed = []
    button = SimpleNamespace(count=lambda: 1, inner_text=lambda: '可领取\n10\n领取', press=lambda *args,**kw: None)

    def wait(**kwargs):
        nonlocal ready
        ready = True

    def dialog_button(role, name):
        assert ready, 'The dialog contents are loaded asynchronously'
        if name.search('10\n待领取\n领取积分'):
            return SimpleNamespace(count=lambda: 1, press=lambda *args,**kw: claimed.append(True))
        return SimpleNamespace(count=lambda: 0)

    dialog = SimpleNamespace(wait_for=wait, get_by_role=dialog_button)
    page = SimpleNamespace(get_by_role=lambda role, name: dialog if role == 'dialog' else button)
    monkeypatch.setattr(rewards, 'view', lambda *args: None)
    monkeypatch.setattr(rewards, 'dismiss_completion', lambda *args: False)
    monkeypatch.setattr(rewards, 'pause', lambda *args: None)
    rewards.claim_pending(page, SimpleNamespace(check=lambda **kw: None), rewards.Options(0,15,10,30,False))
    assert claimed == [True]


def test_quota_dialog_uses_keyboard_controls_in_small_window(monkeypatch):
    actions = []
    ready = False

    def render(**kwargs):
        nonlocal ready
        ready = True

    def pointer_click(**kwargs):
        raise TimeoutError('Control is outside the pointer viewport')

    close = SimpleNamespace(press=lambda key,**kw: actions.append('close'),click=pointer_click)
    dialog = SimpleNamespace(wait_for=render,count=lambda: int(ready),
        inner_text=lambda: '必应搜索\n30/100',
        get_by_role=lambda role,name: SimpleNamespace(first=close))
    button = SimpleNamespace(count=lambda: 1,press=lambda key,**kw: actions.append('open'),click=pointer_click)
    page = SimpleNamespace(get_by_role=lambda role,name: dialog if role=='dialog' else button)
    monkeypatch.setattr(rewards, 'view', lambda *args: None)
    monkeypatch.setattr(rewards, 'dismiss_completion', lambda *args: False)
    monkeypatch.setattr(rewards, 'pause', lambda *args: None)
    assert rewards.read_quota(page, None) == (30,100)
    assert actions == ['open','close']


@pytest.mark.parametrize('location,valid', [(rewards.EARN_PAGE, True),
    ('https://rewards.bing.com/welcome',False), ('https://example.org/earn',False)])
def test_points_control_can_load_before_balance_but_not_on_login_or_other_sites(monkeypatch,location,valid):
    page = SimpleNamespace(url=location,bring_to_front=lambda: None,goto=lambda *args,**kwargs: None,
        get_by_role=lambda role,name: SimpleNamespace(count=lambda: int(role=='button')))
    monkeypatch.setattr(rewards, 'loaded_state', lambda *args: None)
    monkeypatch.setattr(rewards, 'pause', lambda *args: None)
    guard = SimpleNamespace(check=lambda **kwargs: None)
    if valid:
        assert rewards.view(page,guard,location) is None
    else:
        with pytest.raises(ValueError):
            rewards.view(page,guard,location)


def test_poll_preference_comes_from_managed_configuration(monkeypatch):
    options = rewards.Options.load({'REWARDS_POLL_OPTION': 'My displayed option'})
    clicked = []
    choice = SimpleNamespace(count=lambda: 1, click=lambda **kw: clicked.append(True))
    page = SimpleNamespace(get_by_role=lambda role, name, exact: choice if name == options.poll_option else None)
    monkeypatch.setattr(rewards, 'pause', lambda *args: None)
    rewards.vote_poll(page, SimpleNamespace(check=lambda **kw: None), options.poll_option)
    assert clicked == [True]
    rewards.vote_poll(None, None, '')


def test_network_guard_blocks_before_browser_import_or_launch(monkeypatch):
    events = []

    class Guard:
        def __init__(self, env):
            self.error = None
            self.enabled = True

        def start(self):
            events.append('start')
            raise NetworkBlocked('VPN detected')

        def close(self):
            events.append('close')

    monkeypatch.setattr(rewards, 'NetworkGuard', Guard)
    outcome = rewards.execute(env={'REWARDS_SEARCH_COUNT': '0'})
    assert outcome['status'] == 'needs_attention'
    assert events == ['start', 'close']
