"""Create a private GitHub repository or push using environment credentials.

Tokens never enter git remote URLs, git config, commits, or command arguments.
"""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parents[1]


def api(path, payload=None):
    token = os.environ.get('GITHUB_TOKEN') or os.environ.get('GH_TOKEN')
    if not token:
        raise RuntimeError('Set GITHUB_TOKEN or GH_TOKEN in the environment')
    request = urllib.request.Request(
        'https://api.github.com' + path,
        data=json.dumps(payload).encode() if payload is not None else None,
        headers={'Authorization': 'Bearer ' + token,
                 'Accept': 'application/vnd.github+json',
                 'X-GitHub-Api-Version': '2022-11-28',
                 'User-Agent': 'automation-task-manager'},
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.load(response)
    except urllib.error.HTTPError as error:
        try:
            message = json.load(error).get('message', '')
        except (ValueError, TypeError):
            message = ''
        raise RuntimeError(f'GitHub API {path}: HTTP {error.code} {message}') from None


def git(*args, env=None):
    subprocess.run(['git', *args], cwd=ROOT, env=env, check=True)


def create(name):
    user = api('/user')['login']
    repository = api('/user/repos', {'name': name, 'private': True,
        'description': 'Local automation dashboard with isolated script tasks and WorkBuddy check-in',
        'auto_init': False})
    git('init', '-b', 'main')
    git('remote', 'add', 'origin', repository['clone_url'])
    print(json.dumps({'url': repository['html_url'], 'owner': user, 'private': repository['private']}))


def push():
    if not (os.environ.get('GITHUB_TOKEN') or os.environ.get('GH_TOKEN')):
        raise RuntimeError('GitHub token is missing')
    from verify_publish import verify
    verify()
    with tempfile.TemporaryDirectory(prefix='automation-askpass-') as temp:
        folder = Path(temp)
        helper = folder / 'askpass.py'
        helper.write_text(
            'import os,sys\n'
            'print("x-access-token" if "username" in sys.argv[1].lower() '
            'else os.environ.get("GITHUB_TOKEN") or os.environ["GH_TOKEN"])\n', encoding='utf-8')
        if os.name == 'nt':
            launcher = folder / 'askpass.cmd'
            launcher.write_text(f'@echo off\r\n"{sys.executable}" "{helper}" %*\r\n')
        else:
            launcher = folder / 'askpass'
            launcher.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{helper}" "$@"\n')
            launcher.chmod(0o700)
        env = {**os.environ, 'GIT_ASKPASS': str(launcher), 'GIT_TERMINAL_PROMPT': '0'}
        git('-c', 'credential.helper=', 'push', '-u', 'origin', 'main', env=env)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=['create', 'push'])
    parser.add_argument('--name', default='automation-task-manager')
    args = parser.parse_args()
    try:
        create(args.name) if args.action == 'create' else push()
    except (RuntimeError, subprocess.CalledProcessError) as error:
        print(str(error), file=sys.stderr)
        sys.exit(1)
