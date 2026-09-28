"""Check tracked files before publishing; report paths, never secret values."""
import os
from pathlib import Path
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[1]


def verify():
    files=subprocess.check_output(['git','ls-files','-z'],cwd=ROOT).decode().split('\0')
    forbidden=[name for name in files if name.startswith(('data/','.venv/')) or (name.startswith('.env') and name!='.env.example')]
    known=[value.encode() for key,value in os.environ.items() if any(word in key.upper() for word in ('TOKEN','SECRET','PASSWORD','API_KEY')) and len(value)>=8]
    sys.path.insert(0,str(ROOT))
    from tasks.workbuddy.main import load_credentials
    try:
        credential=load_credentials()
        known.append(credential.token.encode())
    except (ValueError,OSError):
        pass
    hits=[]
    for name in files:
        if name and (ROOT/name).is_file():
            content=(ROOT/name).read_bytes()
            if any(value and value in content for value in known):
                hits.append(name)
    if forbidden or hits:
        raise RuntimeError('发布检查发现运行数据或密钥，请清理这些文件：'+', '.join(sorted(set(forbidden+hits))))
    return len([name for name in files if name])


if __name__=='__main__':
    print(f'Publish check passed: {verify()} tracked files, no runtime data or known secrets')
