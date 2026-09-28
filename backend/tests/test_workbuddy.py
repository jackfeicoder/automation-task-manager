import json
import pytest
from tasks.workbuddy.main import Credential, classify_claim, doctor, execute, load_credentials


def test_credential_file_and_doctor(tmp_path):
    path=tmp_path/'session.json'
    path.write_text(json.dumps({'auth':{'accessToken':'private-value','domain':'copilot.tencent.com'},'account':{'uid':'123'}}))
    env={'WORKBUDDY_AUTH_FILE':str(path)}
    result=doctor(env)
    assert result['ready']
    assert 'private-value' not in json.dumps(result)
    assert load_credentials(env).uid=='123'
    assert not doctor({'WORKBUDDY_AUTH_FILE':str(tmp_path/'missing')})['ready']


def test_credential_host_validation():
    for domain in ['https://evil.example','https://copilot.tencent.com@evil.example','http://copilot.tencent.com','https://copilot.tencent.com/path']:
        with pytest.raises(ValueError):
            Credential('token','uid',domain).endpoint


@pytest.mark.parametrize('http,payload,status',[
    (200,{'code':0,'data':{'credit':100}},'success'),
    (200,{'credit':1000},'success'),
    (400,{'code':10001},'already_completed'),
    (200,{'code':10001},'already_completed'),
    (401,{},'needs_login'),
    (403,{},'needs_attention'),
    (429,{},'needs_attention'),
    (502,{},'failed'),
    (200,None,'needs_attention'),
    (200,{'code':12345,'data':{'credit':100}},'failed'),
    (200,{'code':0,'data':{}},'needs_attention'),
])
def test_response_contract(http,payload,status):
    assert classify_claim(http,payload)['status']==status


def test_claim_calls_post_and_verifies_null(monkeypatch):
    monkeypatch.setattr('tasks.workbuddy.main.load_credentials',lambda env:Credential('secret','uid','copilot.tencent.com'))
    responses=iter([(200,{'data':{'today_checked_in':False}}),(200,None),(400,{'code':10001})])
    methods=[]
    def request(credential,path,method='POST'):
        methods.append(method)
        return next(responses)
    monkeypatch.setattr('tasks.workbuddy.main.request_api',request)
    assert execute({})['status']=='already_completed'
    assert methods==['POST','POST','POST']
