"""Execution regressions with an offline browser double, no account access."""
from contextlib import contextmanager, nullcontext
from types import SimpleNamespace

import pytest

from tasks.edge_rewards import main as rewards
from tasks.edge_rewards import network_guard
from tasks.edge_rewards import desktop_browser


@pytest.fixture
def offline_browser(monkeypatch):
    sync_api = pytest.importorskip('playwright.sync_api')
    observed = {'closed': False, 'routes': [], 'user_closed': False}

    class Page:
        def __init__(self, user=False):
            self.user = user
            self.closed = False

        def opener(self):
            return None

        def set_viewport_size(self, size):
            assert not self.user, 'Existing user page viewport must stay unchanged'

        def is_closed(self):
            return self.closed

        def route(self, *args):
            assert not self.user
            observed['routes'].append(args)

        def close(self):
            assert not self.user, 'Existing user page must stay open'
            observed['closed'] = True
            if observed.get('cleanup_error'):
                raise RuntimeError('offline cleanup failure')
            self.closed = True

    class Context:
        pages = [Page(user=True)]

        def on(self, event, callback):
            self.callback = callback

        def remove_listener(self, event, callback):
            assert callback == self.callback
            observed['disconnected'] = True

        def new_page(self):
            page = Page()
            self.callback(page)
            return page

        def close(self):
            pytest.fail('Existing desktop context must stay open')

        def route(self, *args):
            pytest.fail('User context must not receive global routing rules')

    context = Context()

    def connect(endpoint, **kwargs):
        observed['connected'] = endpoint
        assert kwargs['no_defaults'] is True
        if observed.get('connection_error'):
            raise ConnectionError('offline failure')
        return SimpleNamespace(contexts=[context])

    def launch(*args, **kwargs):
        pytest.fail('Desktop mode must not launch a replacement profile')

    @contextmanager
    def playwright():
        yield SimpleNamespace(chromium=SimpleNamespace(connect_over_cdp=connect,
                                                       launch_persistent_context=launch))

    def run(context, page, guard, options, terms):
        observed['terms'] = terms
        return rewards.result('success', 'offline success')

    def forbidden(*args, **kwargs):
        pytest.fail('VPN and proxy checks must be off by default')

    monkeypatch.setattr(sync_api, 'sync_playwright', playwright)
    monkeypatch.setattr(rewards, 'profile_lock', nullcontext)
    monkeypatch.setattr(rewards, 'edge_executable', lambda: 'offline/msedge.exe')
    probe = lambda endpoint: 'ws://127.0.0.1:9222/devtools/browser/offline'
    monkeypatch.setattr(rewards, 'probe_desktop_edge', probe)
    monkeypatch.setattr(desktop_browser, 'probe_desktop_edge', probe)
    monkeypatch.setattr(rewards, 'run_tasks', run)
    for name in ['proxy_reasons', 'process_reasons', 'local_snapshot', 'check_country']:
        monkeypatch.setattr(network_guard, name, forbidden)
    return observed


def test_vpn_proxy_settings_do_not_block_browser_start(offline_browser):
    outcome = rewards.execute(env={'REWARDS_SEARCH_COUNT': '0', 'HTTPS_PROXY': 'http://localhost:7890'})
    assert outcome['status'] == 'success'
    assert offline_browser['connected'] == 'ws://127.0.0.1:9222/devtools/browser/offline'
    assert offline_browser['closed']
    assert offline_browser['disconnected']
    assert not offline_browser['user_closed']


def test_bad_query_file_still_runs_visible_daily_activities(offline_browser, monkeypatch):
    def broken(count):
        raise ValueError('Empty search file')
    monkeypatch.setattr(rewards, 'queries', broken)
    assert rewards.execute(env={})['status'] == 'success'
    assert offline_browser['terms'] == []
    assert offline_browser['closed']


def test_cleanup_failure_does_not_replace_task_result(offline_browser):
    offline_browser['cleanup_error'] = True
    assert rewards.execute(env={'REWARDS_SEARCH_COUNT': '0'})['status'] == 'success'


def test_doctor_works_with_proxy_without_launching_browser(offline_browser):
    outcome = rewards.execute('doctor', env={'HTTP_PROXY': 'http://localhost:7890'})
    assert outcome['status'] == 'success'
    assert outcome['data']['network_check'] is False
    assert 'connected' not in offline_browser


def test_connection_failure_does_not_create_empty_profile(offline_browser):
    offline_browser['connection_error'] = True
    outcome = rewards.execute(env={'REWARDS_SEARCH_COUNT': '0'})
    assert outcome['status'] == 'needs_attention'
    assert 'start_edge.ps1' in outcome['message']


def test_malformed_request_url_is_rejected_without_parser_error():
    assert rewards.host_allowed('https://www.bing.com:not-a-port/search') is False
    assert rewards.host_allowed('https://[broken') is False


def test_edge_locator_uses_installed_path(monkeypatch, tmp_path):
    from pathlib import Path
    if rewards.os.name != 'nt':
        pytest.skip('Windows only')
    path = tmp_path / 'Microsoft/Edge/Application/msedge.exe'
    path.parent.mkdir(parents=True)
    path.touch()
    monkeypatch.setenv('LOCALAPPDATA', str(tmp_path))
    assert Path(rewards.edge_executable()) == path


def test_edge_locator_works_without_programfiles_in_child_env(monkeypatch, tmp_path):
    if rewards.os.name != 'nt':
        pytest.skip('Windows only')
    for key in ['LOCALAPPDATA', 'PROGRAMFILES(X86)', 'PROGRAMFILES', 'PROGRAMW6432']:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv('SYSTEMDRIVE', str(tmp_path))
    path = tmp_path / 'Program Files (x86)/Microsoft/Edge/Application/msedge.exe'
    path.parent.mkdir(parents=True)
    path.touch()
    assert rewards.edge_executable() == str(path)


def test_network_error_exposes_code_without_private_url():
    message = rewards.browser_failure(
        RuntimeError('net::ERR_PROXY_CONNECTION_FAILED at https://example.org/?access_token=private-value'),
        '加载 Rewards 页面')
    assert 'ERR_PROXY_CONNECTION_FAILED' in message
    assert '代理' in message
    assert 'private-value' not in message and 'example.org' not in message
