"""Execution regressions with an offline browser double, no account access."""
from contextlib import contextmanager, nullcontext
from types import SimpleNamespace

import pytest

from tasks.edge_rewards import main as rewards
from tasks.edge_rewards import network_guard


@pytest.fixture
def offline_browser(monkeypatch):
    sync_api = pytest.importorskip('playwright.sync_api')
    observed = {'closed': False}

    def close():
        observed['closed'] = True
        if observed.get('cleanup_error'):
            raise RuntimeError('offline cleanup failure')

    context = SimpleNamespace(pages=[object()], close=close, route=lambda *args: None)

    def launch(profile, **kwargs):
        observed['launch'] = kwargs
        return context

    @contextmanager
    def playwright():
        yield SimpleNamespace(chromium=SimpleNamespace(launch_persistent_context=launch))

    def run(context, page, guard, options, terms):
        observed['terms'] = terms
        return rewards.result('success', 'offline success')

    def forbidden(*args, **kwargs):
        pytest.fail('VPN and proxy checks must be off by default')

    monkeypatch.setattr(sync_api, 'sync_playwright', playwright)
    monkeypatch.setattr(rewards, 'profile_lock', nullcontext)
    monkeypatch.setattr(rewards, 'edge_executable', lambda: 'offline/msedge.exe')
    monkeypatch.setattr(rewards, 'run_tasks', run)
    for name in ['proxy_reasons', 'process_reasons', 'local_snapshot', 'check_country']:
        monkeypatch.setattr(network_guard, name, forbidden)
    return observed


def test_vpn_proxy_settings_do_not_block_browser_start(offline_browser):
    outcome = rewards.execute(env={'REWARDS_SEARCH_COUNT': '0', 'HTTPS_PROXY': 'http://localhost:7890'})
    assert outcome['status'] == 'success'
    assert '--no-proxy-server' not in offline_browser['launch']['args']
    assert offline_browser['launch']['executable_path'] == 'offline/msedge.exe'
    assert offline_browser['closed']


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
    assert 'launch' not in offline_browser


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
