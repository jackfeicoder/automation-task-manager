"""Daily check-in using the local WorkBuddy desktop session.

Credential values are never printed. API responses are classified explicitly:
HTTP 200 alone is not treated as evidence of a completed check-in.
"""
import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import ssl
import sys
import urllib.error
import urllib.request
from urllib.parse import urlsplit

ALLOWED_HOSTS = {'copilot.tencent.com','www.codebuddy.cn','www.workbuddy.cn'}
STATUS_PATH = '/v2/billing/meter/checkin-activity-status'
CLAIM_PATH = '/v2/billing/meter/daily-checkin'


@dataclass
class Credential:
    token: str
    uid: str
    domain: str
    path: str = ''
    expires_at: object = None

    @property
    def endpoint(self):
        domain = self.domain.strip().rstrip('/')
        parsed = urlsplit(domain if '://' in domain else 'https://' + domain)
        if parsed.scheme != 'https' or parsed.hostname not in ALLOWED_HOSTS or parsed.port not in {None,443} or parsed.path not in {'','/'} or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError('WorkBuddy 服务域名应为受支持的腾讯 HTTPS 地址')
        return 'https://' + parsed.hostname


def candidate_paths(env):
    override = env.get('WORKBUDDY_AUTH_FILE','')
    if override:
        return [Path(override).expanduser()]
    suffix = Path('CodeBuddyExtension/Data/Public/auth/workbuddy-desktop.info')
    if os.name == 'nt':
        return [Path(os.environ[name]) / suffix for name in ('LOCALAPPDATA','APPDATA') if os.environ.get(name)]
    if sys.platform == 'darwin':
        return [Path.home() / 'Library/Application Support' / suffix]
    return [Path(os.environ.get('XDG_CONFIG_HOME',Path.home()/'.config')) / suffix]


def load_credentials(env=None):
    env = os.environ if env is None else env
    if env.get('WORKBUDDY_ACCESS_TOKEN'):
        if not env.get('WORKBUDDY_USER_ID'):
            raise ValueError('使用环境变量 Token 时，请同时设置 WORKBUDDY_USER_ID')
        credential = Credential(env['WORKBUDDY_ACCESS_TOKEN'],env['WORKBUDDY_USER_ID'],env.get('WORKBUDDY_DOMAIN') or 'copilot.tencent.com')
        credential.endpoint
        return credential
    for path in candidate_paths(env):
        if not path.is_file():
            continue
        try:
            document = json.loads(path.read_text(encoding='utf-8-sig'))
            auth,account = document.get('auth') or {},document.get('account') or {}
            token,uid = auth.get('accessToken'),account.get('uid')
            if not token or not uid:
                raise ValueError('登录文件缺少 accessToken 或用户 ID，请在客户端重新登录')
            credential = Credential(str(token),str(uid),env.get('WORKBUDDY_DOMAIN') or auth.get('domain') or 'copilot.tencent.com',str(path),auth.get('expiresAt'))
            credential.endpoint
            return credential
        except (json.JSONDecodeError, AttributeError):
            raise ValueError('WorkBuddy 登录文件格式异常，请在客户端重新登录') from None
    raise ValueError('尚未找到 WorkBuddy 登录状态；请登录桌面客户端，或设置 WORKBUDDY_AUTH_FILE')


def doctor(env=None):
    try:
        credential = load_credentials(env)
        expired = False
        if credential.expires_at:
            try:
                value = float(credential.expires_at)
                if value > 10**11:
                    value /= 1000
                expired = value < datetime.now(timezone.utc).timestamp()
            except (ValueError,TypeError):
                pass
        return {'ready': not expired, 'path': credential.path, 'endpoint': credential.endpoint,
            'message': '已检测到登录状态；令牌可能已过期，请打开 WorkBuddy 让客户端刷新登录。' if expired else '已检测到登录凭据。实际有效性将在执行签到时由服务端确认。'}
    except (ValueError,OSError) as error:
        return {'ready':False,'message':str(error)}


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def request_api(credential, path, method='POST'):
    request = urllib.request.Request(credential.endpoint + path,
        data=b'{}' if method=='POST' else None, method=method,
        headers={'Authorization':'Bearer '+credential.token,'X-User-Id':credential.uid,
            'X-Domain':urlsplit(credential.endpoint).hostname,'Content-Type':'application/json',
            'Accept':'application/json','User-Agent':'AutomationTaskManager/1.0'})
    opener = urllib.request.build_opener(NoRedirect(),urllib.request.HTTPSHandler(context=ssl.create_default_context()))
    try:
        with opener.open(request,timeout=20) as response:
            return response.status,json.loads(response.read(1024*1024).decode('utf-8'))
    except urllib.error.HTTPError as error:
        try:
            payload = json.loads(error.read(65536).decode('utf-8'))
        except (ValueError,UnicodeDecodeError):
            payload = {}
        return error.code,payload


def unwrap(payload):
    if isinstance(payload,dict) and isinstance(payload.get('data'),dict):
        return payload['data']
    return payload


def classify_claim(http_status,payload):
    outer = payload if isinstance(payload,dict) else {}
    data = unwrap(payload)
    code = outer.get('code')
    if code is None and isinstance(data,dict):
        code = data.get('code')
    if http_status==401:
        return {'status':'needs_login','message':'登录状态已过期，请打开 WorkBuddy 刷新登录后重试。'}
    if http_status==403:
        return {'status':'needs_attention','message':'服务端拒绝签到，请在 WorkBuddy 客户端检查账号或活动资格。'}
    if str(code)=='10001':
        return {'status':'already_completed','message':'服务端确认今日已签到','data':{'already_completed':True}}
    if http_status==429:
        return {'status':'needs_attention','message':'请求频率受到限制，请稍后手动重试。'}
    if http_status>=500:
        # A write might have succeeded before a gateway failure. The endpoint is
        # idempotent per account/day, so a bounded retry is safe for this plugin.
        return {'status':'failed','message':f'签到服务暂时异常（HTTP {http_status}）','retryable':True}
    if not 200<=http_status<300:
        return {'status':'failed','message':f'签到接口返回 HTTP {http_status}；请检查客户端或接口版本。'}
    if code is not None and str(code) not in {'0','200'}:
        return {'status':'failed','message':f'签到返回业务错误码 {code}'}
    if isinstance(data,dict):
        credit = data.get('credit',data.get('credits'))
        if isinstance(credit,(int,float)) and not isinstance(credit,bool) and credit>=0:
            return {'status':'success','message':f'今日签到成功，领取 {credit:g} 积分','data':{'credits':credit}}
    # Empty/null responses are ambiguous: keep them out of the daily-success cache.
    return {'status':'needs_attention','message':'签到接口响应格式待核验，请在客户端查看今日签到状态。'}


def execute(env=None,status_only=False):
    try:
        credential=load_credentials(env)
    except (ValueError,OSError) as error:
        return {'status':'needs_login','message':str(error)}
    try:
        print('正在查询签到活动状态…',flush=True)
        http_status,payload=request_api(credential,STATUS_PATH,'POST')
        if http_status in {401,403}:
            return classify_claim(http_status,payload)
        data=unwrap(payload)
        # Do not trust today_checked_in alone; some desktop versions report it
        # inconsistently. The daily-checkin endpoint supplies the final result.
        if status_only:
            fields = sorted(data.keys()) if isinstance(data,dict) else []
            return {'status':'success' if http_status==200 else 'failed',
                'message':f'签到状态查询 HTTP {http_status}',
                'data':{'response_fields':fields}}
        print('正在领取今日签到积分…',flush=True)
        http_status,payload=request_api(credential,CLAIM_PATH)
        result=classify_claim(http_status,payload)
        if result['status']=='needs_attention' and http_status==200 and payload is None:
            # A second claim yields the service's explicit already-signed code,
            # establishing completion without guessing from an empty response.
            verify_status,verify_payload=request_api(credential,CLAIM_PATH)
            result=classify_claim(verify_status,verify_payload)
        return result
    except (urllib.error.URLError,TimeoutError,OSError):
        return {'status':'failed','message':'签到请求网络异常；将按配置进行有限重试。','retryable':True}
    except (ValueError,UnicodeDecodeError):
        return {'status':'needs_attention','message':'签到接口返回了非预期内容，请检查客户端状态。'}


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    mode=parser.add_mutually_exclusive_group()
    mode.add_argument('--doctor',action='store_true')
    mode.add_argument('--status',action='store_true')
    mode.add_argument('--claim',action='store_true')
    args=parser.parse_args()
    if args.doctor:
        print(json.dumps(doctor(),ensure_ascii=False))
    else:
        result=execute(status_only=args.status)
        print('AUTOMATION_RESULT='+json.dumps(result,ensure_ascii=False),flush=True)
        sys.exit(0 if result['status'] in {'success','already_completed'} else 1)
