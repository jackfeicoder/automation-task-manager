"""Build a locally paired extension; credentials never enter tracked source files."""
import json
from pathlib import Path
import secrets
import shutil

ROOT = Path(__file__).resolve().parents[1]


def prepare(root=ROOT):
    folder = root / 'data/agentrouter'
    folder.mkdir(parents=True, exist_ok=True)
    config_file = folder / 'bridge.json'
    if config_file.exists():
        secret = json.loads(config_file.read_text('utf-8'))['secret']
        if not isinstance(secret, str) or len(secret) < 40:
            raise ValueError('Local pairing configuration is invalid; restore the local data backup.')
    else:
        secret = secrets.token_urlsafe(48)
        config_file.write_text(json.dumps({'secret': secret}), encoding='utf-8')
    destination = folder / 'chrome-extension'
    shutil.copytree(root / 'tasks/agentrouter/chrome-extension', destination, dirs_exist_ok=True)
    (destination / 'config.js').write_text('const BRIDGE_SECRET = ' + json.dumps(secret) + ';\n', encoding='utf-8')
    return destination


if __name__ == '__main__':
    destination = prepare()
    print('Local extension ready:', destination)
    print('In desktop Chrome: chrome://extensions > Developer mode > Load unpacked > select this folder.')
    print('Keep Chrome open. Scan plugins in Task Harbor, then run Agent Router daily check-in.')
