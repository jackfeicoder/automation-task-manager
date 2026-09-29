import json
import os
import re
import sys
from pathlib import Path

WORKBUDDY_KEYS = ('WORKBUDDY_AUTH_FILE','WORKBUDDY_ACCESS_TOKEN','WORKBUDDY_USER_ID','WORKBUDDY_DOMAIN')


class Environment:
    def __init__(self, data_dir):
        self.path = data_dir / 'environment.json'

    def values(self):
        return json.loads(self.path.read_text(encoding='utf-8')) if self.path.exists() else {}

    def update(self, values):
        current = self.values()
        for key, value in values.items():
            if not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', key) or key.upper() in {'GITHUB_TOKEN','GH_TOKEN','AUTOMATION_API_TOKEN'}:
                raise ValueError('变量名无效，或属于平台保留密钥')
            if value is None:
                current.pop(key, None)
            else:
                current[key] = value
        temporary = self.path.with_suffix('.tmp')
        temporary.write_text(json.dumps(current, ensure_ascii=False, indent=2), encoding='utf-8')
        temporary.replace(self.path)

    def get(self, name):
        return self.values().get(name, os.environ.get(name, ''))

    def summary(self, extra=()):
        names = sorted(set(WORKBUDDY_KEYS) | set(self.values()) | set(extra))
        return [{'name': name, 'configured': bool(self.get(name)),
                 'source': 'local' if name in self.values() else 'environment' if os.environ.get(name) else 'unset'} for name in names]

    def child(self, names):
        # GitHub and platform credentials are deliberately absent from child environments.
        base_names = ('PATH','PATHEXT','SYSTEMROOT','WINDIR','COMSPEC','TEMP','TMP','HOME','USERPROFILE',
                      'LOCALAPPDATA','APPDATA','LANG','LC_ALL','SSL_CERT_FILE','SSL_CERT_DIR',
                      'PROGRAMFILES','PROGRAMFILES(X86)','PROGRAMW6432','SYSTEMDRIVE',
                      'HTTP_PROXY','HTTPS_PROXY','NO_PROXY')
        env = {key: os.environ[key] for key in base_names if key in os.environ}
        for name in names:
            if name.upper() not in {'GITHUB_TOKEN','GH_TOKEN','AUTOMATION_API_TOKEN'} and self.get(name):
                env[name] = self.get(name)
        env.update(PYTHONUTF8='1', PYTHONUNBUFFERED='1', VIRTUAL_ENV=str(Path(sys.prefix)))
        env['PATH'] = str(Path(sys.executable).parent) + os.pathsep + env.get('PATH','')
        return env

    def redact(self, text):
        values = list(self.values().values()) + [value for key,value in os.environ.items() if re.search(r'TOKEN|SECRET|PASSWORD|API_KEY|COOKIE', key, re.I)]
        for value in sorted(set(values), key=len, reverse=True):
            if value and len(value) >= 6:
                text = text.replace(value, '[REDACTED]')
        text = re.sub(r'(?i)(Bearer\s+)[^\s"\']+', r'\1[REDACTED]', text)
        text = re.sub(r'eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+', '[REDACTED]', text)
        return text

    def sanitize(self,value):
        if isinstance(value,str):
            return self.redact(value)
        if isinstance(value,list):
            return [self.sanitize(item) for item in value]
        if isinstance(value,dict):
            return {key:'[REDACTED]' if re.search(r'token|secret|password|api.?key|cookie',key,re.I) else self.sanitize(item) for key,item in value.items()}
        return value
