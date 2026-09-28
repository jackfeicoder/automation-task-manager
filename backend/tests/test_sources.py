import json
from fastapi.testclient import TestClient
from backend.app.main import create_app


def test_plugin_source_creation_validation_and_backup(tmp_path):
    root=tmp_path/'project'
    (root/'tasks').mkdir(parents=True)
    with TestClient(create_app(root,root/'data')) as client:
        body={'id':'custom','name':'Custom','command':['{python}','main.py'],'cwd':'tasks/custom'}
        assert client.post('/api/tasks',json=body).status_code==201
        source={'path':'main.py','content':'print("first", flush=True)\n'}
        assert client.put('/api/tasks/custom/file',json=source).status_code==200
        assert client.get('/api/tasks/custom/files').json()['files']==['main.py']
        assert client.get('/api/tasks/custom/file',params={'path':'main.py'}).json()['content']==source['content']
        for name in ['../../outside.py','.env','nested/.hidden.py','C:/outside.py']:
            assert client.put('/api/tasks/custom/file',json={'path':name,'content':'print(1)'}).status_code==422
        assert client.put('/api/tasks/custom/file',json={'path':'main.py','content':'def broken('}).status_code==422
        source['content']='print("second", flush=True)\n'
        response=client.put('/api/tasks/custom/file',json=source)
        assert response.status_code==200
        backup=root/'data/artifacts/revisions'/response.json()['backup']
        assert 'first' in json.loads(backup.read_text(encoding='utf-8'))['content']
        run=client.post('/api/tasks/custom/run').json()
        assert client.put('/api/tasks/custom/file',json=source).status_code==409
        client.post('/api/runs/'+run['id']+'/stop')
