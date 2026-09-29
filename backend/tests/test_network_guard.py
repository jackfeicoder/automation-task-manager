"""Process snapshot regressions: no user account or external network access."""
import ctypes
import os
from types import SimpleNamespace

import pytest

from tasks.edge_rewards import network_guard as guard


@pytest.fixture
def snapshot(monkeypatch):
    pytest.importorskip('ctypes.wintypes')
    if os.name != 'nt':
        pytest.skip('Native process snapshot is Windows-only')
    state = SimpleNamespace(names=['[System Process]', 'Secure System', 'msedge.exe'],
                            position=0, error=18, handle=123, closed=[])

    def create(flags, pid):
        assert flags == 2 and pid == 0
        return state.handle

    def first(handle, pointer):
        assert pointer._obj.dwSize == ctypes.sizeof(pointer._obj)
        if not state.names:
            return False
        pointer._obj.szExeFile = state.names[0]
        return True

    def next_entry(handle, pointer):
        state.position += 1
        if state.position >= len(state.names):
            return False
        pointer._obj.szExeFile = state.names[state.position]
        return True

    def close(handle):
        state.closed.append(handle)
        return True

    kernel = SimpleNamespace(CreateToolhelp32Snapshot=create, Process32FirstW=first,
                             Process32NextW=next_entry, CloseHandle=close)
    monkeypatch.setattr(guard.ctypes, 'WinDLL', lambda *args, **kw: kernel)
    monkeypatch.setattr(guard.ctypes, 'get_last_error', lambda: state.error)
    return state


def test_system_names_are_read_and_snapshot_handle_closes(snapshot):
    assert guard.windows_process_names() == snapshot.names
    assert snapshot.closed == [123]


def test_empty_snapshot_is_not_accepted(snapshot):
    snapshot.names = []
    with pytest.raises(guard.NetworkBlocked, match='读取进程快照失败'):
        guard.windows_process_names()
    assert snapshot.closed == [123]


def test_unreadable_snapshot_is_not_accepted(snapshot):
    snapshot.handle = ctypes.c_void_p(-1).value
    with pytest.raises(guard.NetworkBlocked, match='读取进程快照失败'):
        guard.windows_process_names()
    assert not snapshot.closed


def test_incomplete_enumeration_blocks_and_closes_handle(snapshot):
    snapshot.error = 5
    with pytest.raises(guard.NetworkBlocked, match='读取中断'):
        guard.windows_process_names()
    assert snapshot.closed == [123]


def test_unknown_process_still_blocks(snapshot):
    snapshot.names[1] = ''
    with pytest.raises(guard.NetworkBlocked, match='未知名称'):
        guard.windows_process_names()
    assert snapshot.closed == [123]


@pytest.mark.parametrize('program', ['mihomo.exe', 'v2rayN.exe', 'WireGuard.exe', 'openvpn.exe'])
def test_known_vpn_is_still_blocked(monkeypatch, program):
    monkeypatch.setattr(guard, 'windows_process_names', lambda: ['msedge.exe', program])
    assert guard.process_reasons()


def test_non_vpn_system_processes_do_not_block(monkeypatch):
    monkeypatch.setattr(guard, 'windows_process_names',
                        lambda: ['[System Process]', 'Secure System', 'msedge.exe'])
    assert guard.process_reasons() == []


def test_process_api_failure_remains_blocking(monkeypatch):
    def broken():
        raise OSError('offline failure')
    monkeypatch.setattr(guard, 'windows_process_names', broken)
    with pytest.raises(guard.NetworkBlocked, match='读取进程列表失败'):
        guard.process_reasons()


def test_default_mode_does_not_read_vpn_proxy_or_region(monkeypatch):
    def prohibited(*args, **kwargs):
        pytest.fail('Default mode must not run direct-network checks')
    for name in ['proxy_reasons', 'process_reasons', 'local_snapshot', 'check_country']:
        monkeypatch.setattr(guard, name, prohibited)
    check = guard.NetworkGuard({'HTTPS_PROXY': 'http://localhost:7890',
                                'REWARDS_COUNTRY_CODE': 'unused'})
    check.start()
    check.check()
    check.check(full=True)
    check.close()
    assert not check.enabled
    assert check.thread is None


def test_explicit_direct_network_check_remains_available(monkeypatch):
    def blocked(*args, **kwargs):
        raise guard.NetworkBlocked('VPN detected')
    monkeypatch.setattr(guard, 'local_snapshot', blocked)
    check = guard.NetworkGuard({'REWARDS_REQUIRE_DIRECT_NETWORK': 'true'})
    with pytest.raises(guard.NetworkBlocked, match='VPN detected'):
        check.start()
    check.close()
