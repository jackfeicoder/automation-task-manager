"""Check working files, the index and all ref history; never report secret values."""
import io
import json
import os
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
SENSITIVE_NAME = re.compile(r'token|secret|password|api.?key|cookie|session|user.?id|^uid$|email', re.I)
PATTERNS = (
    ('GitHub credential', re.compile(rb'\b(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{40,})\b')),
    ('JWT credential', re.compile(rb'\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]{16,}\b')),
    ('private key', re.compile(rb'-----BEGIN (?:[A-Z]+ )?PRIVATE KEY-----')),
    ('credential in URL', re.compile(rb'https?://[^\s/"\']+:[^\s/@"\']+@')),
    ('literal credential', re.compile(rb'(?i)["\']?(?:access[_-]?token|refresh[_-]?token|api[_-]?key|password|client[_-]?secret|authorization|cookie)["\']?\s*[:=]\s*["\'][^"\'\r\n]{16,}["\']')),
    ('environment credential', re.compile(rb'(?im)^[A-Z0-9_]*(?:TOKEN|SECRET|PASSWORD|API_KEY|COOKIE)[ \t]*=[ \t]*[^\s#"\'][^\r\n]{15,}$')),
)


def forbidden_path(name):
    path = Path(name)
    lower = name.replace('\\', '/').lower()
    base = path.name.lower()
    return (any(part in {'data', '.venv', 'venv', 'profiles', 'playwright-report', 'test-results'} for part in lower.split('/'))
        or (base.startswith('.env') and base != '.env.example')
        or base in {'environment.json', 'local.json', 'credentials.json', 'cookies.json',
                    'session.json', 'storage-state.json', 'storage_state.json', 'workbuddy-desktop.info',
                    'id_rsa', 'id_ed25519'}
        or '.local.' in base
        or (base.startswith('auth') and path.suffix.lower() == '.json')
        or (base.startswith('cookies') and path.suffix.lower() == '.txt')
        or path.suffix.lower() in {'.pem', '.key', '.p12', '.pfx', '.db', '.sqlite', '.sqlite3', '.log'})


def local_secrets(root):
    values = set()

    def collect(document):
        if isinstance(document, dict):
            for key, value in document.items():
                if SENSITIVE_NAME.search(key) and isinstance(value, (str, int)) and len(str(value)) >= 8:
                    values.add(str(value).encode())
                collect(value)
        elif isinstance(document, list):
            for value in document:
                collect(value)

    env = dict(os.environ)
    from dotenv import dotenv_values
    env.update({key: value for key, value in dotenv_values(root / '.env', interpolate=False).items() if value is not None})
    collect(env)
    for path in (root / 'data/environment.json',):
        if path.is_file():
            collect(json.loads(path.read_text(encoding='utf-8-sig')))
            env.update(json.loads(path.read_text(encoding='utf-8-sig')))
    sys.path.insert(0, str(ROOT))
    from tasks.workbuddy.main import candidate_paths
    for path in candidate_paths(env):
        if path.is_file():
            collect(json.loads(path.read_text(encoding='utf-8-sig')))
    return values


def verify(root=ROOT, known=None):
    root = Path(root)
    known = local_secrets(root) if known is None else known
    hits = set()

    def check(name, content, location):
        if forbidden_path(name):
            hits.add(f'{location}: {name} (local data file)')
        if any(value and value in content for value in known):
            hits.add(f'{location}: {name} (known local credential)')
        for label, pattern in PATTERNS:
            if pattern.search(content):
                hits.add(f'{location}: {name} ({label})')

    def git(*args):
        return subprocess.check_output(['git', *args], cwd=root)

    entries = [entry for entry in git('ls-files', '--stage', '-z').split(b'\0') if entry]
    objects = {}
    for entry in entries:
        metadata, name = entry.split(b'\t', 1)
        oid = metadata.split()[1].decode()
        path = name.decode('utf-8')
        objects.setdefault(oid, set()).add(path)
        if (root / path).is_file():
            check(path, (root / path).read_bytes(), 'working tree')
    # Enumerate every tree: rev-list --objects alone may omit a sensitive filename
    # when the same blob was also used under a harmless name in another commit.
    for commit in git('rev-list', '--all').decode().splitlines():
        for entry in git('ls-tree', '-r', '-z', commit).split(b'\0'):
            if not entry:
                continue
            metadata, name = entry.split(b'\t', 1)
            oid = metadata.split()[2].decode()
            objects.setdefault(oid, set()).add(name.decode('utf-8'))
    request = ''.join(oid + '\n' for oid in objects).encode()
    output = subprocess.run(['git', 'cat-file', '--batch'], input=request, stdout=subprocess.PIPE, cwd=root, check=True).stdout
    stream = io.BytesIO(output)
    for oid, names in objects.items():
        header = stream.readline().split()
        if len(header) != 3:
            raise RuntimeError('Git object audit failed')
        content = stream.read(int(header[2]))
        stream.read(1)
        if header[1] == b'blob':
            for name in names:
                check(name, content, f'Git object {oid[:12]}')
    if hits:
        raise RuntimeError('发布检查发现待处理内容（仅显示位置，不显示密钥）：\n' + '\n'.join(sorted(hits)))
    return len(entries)


if __name__=='__main__':
    print(f'Publish check passed: {verify()} tracked files; index and all ref history checked')
