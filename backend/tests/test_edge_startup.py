from types import SimpleNamespace

import pytest

from tasks.edge_rewards import desktop_browser as desktop


def fail_connection(_):
    raise ValueError(desktop.CONNECTION_HINT)


def test_connected_browser_is_reused_without_starting(monkeypatch):
    monkeypatch.setattr(desktop, 'probe_desktop_edge', lambda endpoint: 'ws://connected')
    monkeypatch.setattr(desktop, 'start_desktop_edge', lambda port: pytest.fail('must reuse'))
    assert desktop.ensure_desktop_edge({}) == 'ws://connected'


def test_open_normal_browser_is_not_terminated_or_restarted(monkeypatch):
    monkeypatch.setattr(desktop, 'probe_desktop_edge', fail_connection)
    monkeypatch.setattr(desktop, 'edge_is_running', lambda: True)
    monkeypatch.setattr(desktop, 'start_desktop_edge', lambda port: pytest.fail('must preserve desktop'))
    with pytest.raises(ValueError, match='普通启动'):
        desktop.ensure_desktop_edge({})


def test_closed_edge_starts_then_waits_for_valid_connection(monkeypatch):
    responses = iter([None, None, 'ws://connected'])
    launches = []
    def probe(endpoint):
        assert endpoint == 'http://127.0.0.1:9333'
        value = next(responses)
        if value is None:
            raise ValueError(desktop.CONNECTION_HINT)
        return value
    monkeypatch.setattr(desktop, 'probe_desktop_edge', probe)
    monkeypatch.setattr(desktop, 'edge_is_running', lambda: False)
    monkeypatch.setattr(desktop, 'edge_executable', lambda: 'installed-edge')
    monkeypatch.setattr(desktop, 'start_desktop_edge', launches.append)
    monkeypatch.setattr(desktop.time, 'sleep', lambda value: None)
    assert desktop.ensure_desktop_edge({'REWARDS_EDGE_ENDPOINT':'http://127.0.0.1:9333'}) == 'ws://connected'
    assert launches == [9333]


def test_other_browser_port_does_not_trigger_edge_launch(monkeypatch):
    def probe(endpoint):
        raise ValueError('不是 Microsoft Edge')
    monkeypatch.setattr(desktop, 'probe_desktop_edge', probe)
    monkeypatch.setattr(desktop, 'start_desktop_edge', lambda port: pytest.fail('wrong browser port'))
    with pytest.raises(ValueError, match='不是 Microsoft Edge'):
        desktop.ensure_desktop_edge({})


def test_launcher_uses_desktop_shell_outside_worker_job(monkeypatch):
    calls = []
    def run(command, **kwargs):
        calls.append((command, kwargs))
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(desktop.subprocess, 'run', run)
    desktop.start_desktop_edge(9333)
    command, kwargs = calls[0]
    assert 'Shell.Application' in command[-1] and 'ShellExecute' in command[-1]
    assert kwargs['env']['TASK_HARBOR_EDGE_PORT'] == '9333'
    assert kwargs['env']['TASK_HARBOR_EDGE_SCRIPT'].endswith('start_edge.ps1')
    assert '-Reuse' in command[-1]
