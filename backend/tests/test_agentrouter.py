import http.client
import json
from pathlib import Path
import shutil
import subprocess
import threading

import pytest

from backend.app.models import Task
from scripts.prepare_agentrouter import prepare
from tasks.agentrouter.main import Bridge

ROOT = Path(__file__).resolve().parents[2]
ORIGIN = 'chrome-extension://' + 'a' * 32


@pytest.fixture
def bridge():
    server = Bridge('test-local-pairing-' + 'x' * 48, address=('127.0.0.1', 0))
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    yield server
    server.shutdown()
    server.server_close()
    worker.join()


def request(server, method='GET', path='/job', body=None, **headers):
    conn = http.client.HTTPConnection(*server.server_address, timeout=3)
    data = json.dumps(body) if body is not None else None
    base = {'Origin': ORIGIN, 'Authorization': 'Bearer ' + server.secret}
    conn.request(method, path, data, {**base, **headers})
    response = conn.getresponse()
    status, result = response.status, response.read()
    conn.close()
    return status, json.loads(result) if result else {}


def test_requires_local_host_extension_origin_and_pairing(bridge):
    for headers in ({'Origin': 'https://agentrouter.org'}, {'Authorization': 'Bearer wrong'},
                    {'Host': 'evil.example'}, {'Origin': '', 'Authorization': 'Bearer wrong'}):
        assert request(bridge, **headers)[0] == 403
    assert not bridge.connected.is_set()
    status, body = request(bridge)
    assert status == 200 and body['job'] == bridge.job_id
    assert bridge.connected.is_set()
    assert bridge.secret not in json.dumps(body)
    assert request(bridge, Origin='')[0] == 200


def test_success_requires_relogin_and_discards_extra_sensitive_fields(bridge):
    body = {'job': bridge.job_id, 'status': 'success', 'logged_in': True}
    assert request(bridge, 'POST', '/result', body)[0] == 422
    body['login_clicked'] = True
    body['page_text'] = 'private account or token text'
    assert request(bridge, 'POST', '/result', body)[0] == 200
    assert bridge.finished.is_set()
    assert bridge.result['status'] == 'success'
    assert 'private account' not in json.dumps(bridge.result)
    assert request(bridge)[1] == {'job': None}
    assert request(bridge, 'POST', '/result', body)[0] == 409


def test_expired_and_other_job_results_rejected(bridge):
    body = {'job': 'old-job', 'status': 'needs_login'}
    assert request(bridge, 'POST', '/result', body)[0] == 422
    body['job'] = bridge.job_id
    body['status'] = 'unknown'
    assert request(bridge, 'POST', '/result', body)[0] == 422
    body['status'] = 'needs_login'
    bridge.deadline = 0
    assert request(bridge)[1] == {'job': None}
    assert request(bridge, 'POST', '/result', body)[0] == 409


def test_local_build_preserves_pairing_and_never_changes_source(tmp_path):
    source = ROOT / 'tasks/agentrouter/chrome-extension'
    shutil.copytree(source, tmp_path / 'tasks/agentrouter/chrome-extension')
    output = prepare(tmp_path)
    config = json.loads((tmp_path / 'data/agentrouter/bridge.json').read_text('utf-8'))
    assert len(config['secret']) >= 40
    assert config['secret'] in (output / 'config.js').read_text('utf-8')
    assert config['secret'] not in (source / 'config.js').read_text('utf-8')
    prepare(tmp_path)
    assert json.loads((tmp_path / 'data/agentrouter/bridge.json').read_text('utf-8')) == config


def test_plugin_daily_success_protection_and_no_publisher_credentials():
    task = Task.model_validate_json((ROOT / 'tasks/agentrouter/task.json').read_text('utf-8'))
    assert task.daily_once and task.max_attempts == 1
    assert task.schedule.time == '09:10' and not task.enabled
    assert task.env_names == []
    manifest = json.loads((ROOT / 'tasks/agentrouter/chrome-extension/manifest.json').read_text('utf-8'))
    assert manifest['host_permissions'] == ['http://127.0.0.1:18765/*', 'https://agentrouter.org/*']
    assert 'cookies' not in manifest['permissions']
    assert 'tabs' not in manifest['permissions']
    assert 'debugger' in manifest['permissions']


def test_extension_lifecycle():
    node = shutil.which('node')
    if not node:
        pytest.skip('Node is only needed for extension fixture tests')
    subprocess.run([node, str(ROOT / 'backend/tests/fixtures/agentrouter-extension.test.cjs')],
                   cwd=ROOT, check=True, timeout=15)
