"""Desktop shortcut launcher: connect the original Edge profile with clear prompts."""
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def launch():
    from dotenv import load_dotenv
    from tasks.edge_rewards.desktop_browser import ensure_desktop_edge
    load_dotenv(ROOT / '.env')
    env = dict(os.environ)
    local = ROOT / 'data/environment.json'
    if local.is_file():
        values = json.loads(local.read_text('utf-8-sig'))
        value = values.get('REWARDS_EDGE_ENDPOINT')
        if isinstance(value, str):
            env['REWARDS_EDGE_ENDPOINT'] = value
    ensure_desktop_edge(env)


if __name__ == '__main__':
    try:
        launch()
    except Exception as error:
        import ctypes
        message = str(error) if isinstance(error, ValueError) else 'Edge 启动未完成，请在项目目录运行 scripts/start_edge.ps1 查看提示。'
        ctypes.windll.user32.MessageBoxW(None, message, 'Edge Rewards 启动提示', 0x10)
        sys.exit(1)
