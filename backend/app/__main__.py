import os
from pathlib import Path
from dotenv import load_dotenv
import uvicorn

load_dotenv(Path(__file__).resolve().parents[2] / '.env')
host = os.environ.get('AUTOMATION_HOST','127.0.0.1')
if host not in {'127.0.0.1','localhost','::1'}:
    raise SystemExit('当前版本使用本机监听，请将 AUTOMATION_HOST 设置为 127.0.0.1')
uvicorn.run('backend.app.main:app', host=host, port=int(os.environ.get('AUTOMATION_PORT','8765')), workers=1)
