import asyncio
from datetime import datetime, timezone
import json
from pathlib import Path
import time

from fastapi.testclient import TestClient
import psutil
import pytest

from backend.app.environment import Environment
from backend.app.main import ROOT, create_app
from backend.app.models import Schedule, Settings, Task
from backend.app.runner.engine import Engine
from backend.app.scheduler.timing import next_time
from backend.app.storage.database import Store


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.delenv('AUTOMATION_API_TOKEN', raising=False)
    with TestClient(create_app(ROOT, tmp_path)) as client:
        yield client


def task(task_id='test', code='print("ok")', **kwargs):
    return Task(id=task_id, name=task_id, command=['{python}','-c',code], cwd='tasks/example', **kwargs)


def wait_run(client, run_id, timeout=12):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        run = client.get('/api/runs/' + run_id).json()
        if run['status'] not in {'queued','running'}:
            return run
        time.sleep(0.1)
    pytest.fail('run did not finish')


def test_frequency_timezone():
    base = datetime(2026,9,28,0,0,tzinfo=timezone.utc)
    assert next_time(Schedule(kind='daily', time='09:00'), base) == '2026-09-28T01:00:00+00:00'
    assert next_time(Schedule(kind='weekly', time='09:00', weekdays=[1]), base) == '2026-09-29T01:00:00+00:00'
    assert next_time(Schedule(kind='interval', interval_minutes=5), base) == '2026-09-28T00:05:00+00:00'
    assert next_time(Schedule(kind='cron', cron='0 9 * * *'), base) == '2026-09-28T01:00:00+00:00'
    with pytest.raises(ValueError):
        Schedule(kind='cron', cron='wrong')
    with pytest.raises(ValueError):
        Schedule(kind='weekly', weekdays=[])


def test_crash_isolation_and_duplicate(client):
    client.post('/api/tasks', json=task('bad','import sys;sys.exit(3)').model_dump()).raise_for_status()
    client.post('/api/tasks', json=task('good','import time;time.sleep(0.3);print("ok")').model_dump()).raise_for_status()
    batch = client.post('/api/tasks/batch',json={'ids':['bad','good','missing'],'action':'run'}).json()
    assert batch[2]['status'] == 'not_found'
    duplicate = client.post('/api/tasks/good/run').json()
    assert duplicate['status'] == 'already_running'
    assert wait_run(client,batch[0]['id'])['status'] == 'failed'
    assert wait_run(client,batch[1]['id'])['status'] == 'success'


def test_timeout_kills_descendants(client):
    code = 'import subprocess,sys,time; child=subprocess.Popen([sys.executable,"-c","import time;time.sleep(60)"]);print("CHILD_PID="+str(child.pid),flush=True);time.sleep(60)'
    client.post('/api/tasks',json=task('tree',code,timeout_seconds=1).model_dump()).raise_for_status()
    run = wait_run(client,client.post('/api/tasks/tree/run').json()['id'])
    assert run['status'] == 'timeout'
    pid = int(run['log'].split('CHILD_PID=')[1].splitlines()[0])
    for _ in range(30):
        if not psutil.pid_exists(pid):
            break
        time.sleep(0.1)
    assert not psutil.pid_exists(pid)


def test_daily_once_and_resource_lock(client):
    body = task('once',daily_once=True).model_dump()
    client.post('/api/tasks',json=body).raise_for_status()
    run = wait_run(client,client.post('/api/tasks/once/run').json()['id'])
    assert run['status'] == 'success'
    assert client.post('/api/tasks/once/run').json()['status'] == 'already_completed'
    for name in ['serial_a','serial_b']:
        client.post('/api/tasks',json=task(name,'import time;time.sleep(0.5)',resource_group='account').model_dump()).raise_for_status()
    runs = client.post('/api/tasks/batch',json={'ids':['serial_a','serial_b']}).json()
    first,second = [wait_run(client,item['id']) for item in runs]
    assert second['started_at'] >= first['finished_at']


def test_stop_and_delete(client):
    client.post('/api/tasks',json=task('stop','import time;time.sleep(30)').model_dump()).raise_for_status()
    run_id = client.post('/api/tasks/stop/run').json()['id']
    assert client.delete('/api/tasks/stop').status_code == 409
    client.post('/api/runs/'+run_id+'/stop').raise_for_status()
    assert wait_run(client,run_id)['status'] == 'cancelled'
    assert client.delete('/api/tasks/stop').status_code == 200


def test_environment_redaction_and_validation(client, monkeypatch, tmp_path):
    monkeypatch.setenv('GITHUB_TOKEN','do-not-pass-this-secret')
    environment = Environment(tmp_path)
    environment.update({'CUSTOM_TOKEN':'sample-private-value'})
    assert 'GITHUB_TOKEN' not in environment.child([])
    assert environment.redact('sample-private-value') == '[REDACTED]'
    assert environment.sanitize({'credits':100,'accessToken':'hidden'}) == {'credits':100,'accessToken':'[REDACTED]'}
    response = client.put('/api/environment',json={'values':{'CUSTOM_TOKEN':'sample-private-value'}})
    assert response.status_code == 200
    assert 'sample-private-value' not in response.text
    assert client.put('/api/environment',json={'values':{'GITHUB_TOKEN':'x'}}).status_code == 422
    assert client.post('/api/tasks',json=task('escape').model_copy(update={'cwd':'..'}).model_dump()).status_code == 422
    assert client.post('/api/tasks',json=task('origin').model_dump(),headers={'origin':'https://other.example'}).status_code == 403


def test_deleted_manifest_stays_deleted(client):
    assert client.delete('/api/tasks/example').status_code == 200
    assert client.post('/api/tasks/discover').json()['added'] == []


def test_authentication(tmp_path, monkeypatch):
    monkeypatch.setenv('AUTOMATION_API_TOKEN','management-secret')
    with TestClient(create_app(ROOT,tmp_path)) as client:
        assert client.get('/api/tasks').status_code == 401
        assert client.post('/api/auth/login',json={'token':'wrong'}).status_code == 401
        assert client.post('/api/auth/login',json={'token':'management-secret'}).status_code == 200
        assert client.get('/api/tasks').status_code == 200


def test_scheduler_and_restart_recovery(tmp_path):
    store = Store(tmp_path)
    item = task('scheduled',enabled=True,schedule=Schedule(kind='interval',interval_minutes=10))
    store.save_task(item,create=True)
    with store.connect() as db:
        db.execute('UPDATE tasks SET next_run=? WHERE id=?',('2026-01-01T00:00:00+00:00','scheduled'))
    assert store.due_tasks(True) == ['scheduled']
    assert store.due_tasks(True) == []
    run_id = store.enqueue('scheduled')['id']
    store.update_run(run_id,status='running')
    store.recover()
    assert store.run(run_id)['status'] == 'interrupted'


def test_single_scheduler_per_data_directory(tmp_path):
    from backend.app.storage.instance import InstanceLock
    first=InstanceLock(tmp_path/'scheduler.lock')
    with pytest.raises(RuntimeError):
        InstanceLock(tmp_path/'scheduler.lock')
    first.close()
    second=InstanceLock(tmp_path/'scheduler.lock')
    second.close()


def test_description_edit_preserves_schedule(tmp_path):
    store=Store(tmp_path)
    item=task('interval',enabled=True,schedule=Schedule(kind='interval',interval_minutes=10))
    store.save_task(item,create=True)
    due=store.list_tasks()[0]['next_run']
    item.description='Updated description'
    store.save_task(item)
    assert store.list_tasks()[0]['next_run']==due
