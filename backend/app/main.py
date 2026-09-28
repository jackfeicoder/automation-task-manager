from contextlib import asynccontextmanager
import hashlib
import json
import os
from pathlib import Path
import platform
import secrets
import sqlite3
import sys

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from starlette.middleware.trustedhost import TrustedHostMiddleware

from backend.app.environment import Environment
from backend.app.models import BatchRequest, EnvironmentUpdate, Settings, Task
from backend.app.runner.engine import Engine, task_directory
from backend.app.storage.database import Store

ROOT = Path(__file__).resolve().parents[2]


def create_app(root=ROOT, data_dir=None):
    load_dotenv(root / '.env', override=False)
    store = Store(data_dir or root / 'data')
    environment = Environment(store.data_dir)
    engine = Engine(root, store, environment)
    token = os.environ.get('AUTOMATION_API_TOKEN', '')
    session = hashlib.sha256(token.encode()).hexdigest() if token else ''

    @asynccontextmanager
    async def lifespan(app):
        discover()
        await engine.start()
        yield
        await engine.stop()

    app = FastAPI(title='Automation Task Manager', lifespan=lifespan)
    app.state.store, app.state.engine = store, engine
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=['localhost','127.0.0.1','[::1]','testserver'])

    @app.middleware('http')
    async def protect(request: Request, call_next):
        if request.url.path.startswith('/api/'):
            origin = request.headers.get('origin')
            if origin and origin != f'{request.url.scheme}://{request.headers.get("host")}':
                return JSONResponse({'detail': '跨域请求被拒绝'}, status_code=403)
            if token and request.url.path not in {'/api/auth/login','/api/health'}:
                bearer = request.headers.get('authorization', '').removeprefix('Bearer ')
                cookie = request.cookies.get('automation_session', '')
                if not (secrets.compare_digest(bearer, token) or secrets.compare_digest(cookie, session)):
                    return JSONResponse({'detail': '请输入管理访问密钥'}, status_code=401)
        response = await call_next(request)
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['X-Frame-Options'] = 'DENY'
        response.headers['Referrer-Policy'] = 'same-origin'
        if request.url.path.startswith('/api/'):
            response.headers['Cache-Control'] = 'no-store'
        return response

    def validate_task(task):
        try:
            task_directory(root, task.cwd)
        except ValueError as error:
            raise HTTPException(422, str(error)) from None

    def discover():
        added, errors = [], []
        for manifest in sorted((root / 'tasks').glob('*/task.json')):
            try:
                task = Task.model_validate_json(manifest.read_text(encoding='utf-8'))
                validate_task(task)
                if not store.task(task.id) and not store.removed(task.id):
                    store.save_task(task, create=True)
                    added.append(task.id)
            except Exception as error:
                errors.append({'file': str(manifest.relative_to(root)), 'error': environment.redact(str(error))})
        return {'added': added, 'errors': errors}

    @app.get('/api/health')
    def health():
        return {'status': 'ok', 'authentication_required': bool(token)}

    class Login(BaseModel):
        token: str

    @app.post('/api/auth/login')
    def login(body: Login):
        if token and not secrets.compare_digest(body.token, token):
            raise HTTPException(401, '访问密钥错误')
        response = JSONResponse({'status': 'ok'})
        response.set_cookie('automation_session', session, httponly=True, samesite='strict', max_age=86400)
        return response

    @app.post('/api/auth/logout')
    def logout():
        response = JSONResponse({'status': 'ok'})
        response.delete_cookie('automation_session')
        return response

    @app.get('/api/tasks')
    def tasks():
        return store.list_tasks()

    @app.post('/api/tasks', status_code=201)
    def add_task(task: Task):
        validate_task(task)
        try:
            store.save_task(task, create=True)
        except sqlite3.IntegrityError:
            raise HTTPException(409, '任务 ID 已存在') from None
        return task

    @app.post('/api/tasks/discover')
    def scan():
        return discover()

    @app.put('/api/tasks/{task_id}')
    def edit_task(task_id: str, task: Task):
        if not store.task(task_id):
            raise HTTPException(404, '任务不存在')
        if task.id != task_id:
            raise HTTPException(422, '任务 ID 在创建后保持固定')
        validate_task(task)
        store.save_task(task)
        return task

    @app.delete('/api/tasks/{task_id}')
    def delete_task(task_id: str):
        if not store.task(task_id):
            raise HTTPException(404, '任务不存在')
        try:
            store.delete_task(task_id)
        except ValueError as error:
            raise HTTPException(409, str(error)) from None
        return {'status': 'deleted'}

    def enqueue(task_id):
        try:
            return store.enqueue(task_id)
        except KeyError:
            raise HTTPException(404, '任务不存在') from None

    @app.post('/api/tasks/{task_id}/run', status_code=202)
    def run_task(task_id: str):
        return enqueue(task_id)

    @app.post('/api/tasks/batch')
    def batch(body: BatchRequest):
        results = []
        for task_id in dict.fromkeys(body.ids):
            task = store.task(task_id)
            if not task:
                results.append({'task_id': task_id, 'status': 'not_found'})
            elif body.action == 'run':
                results.append(enqueue(task_id))
            else:
                task.enabled = body.action == 'enable'
                store.save_task(task)
                results.append({'task_id': task_id, 'status': 'updated'})
        return results

    @app.get('/api/runs')
    def runs(task_id: str | None = None, limit: int = 100):
        return store.runs(task_id, max(1, min(limit, 500)))

    @app.get('/api/runs/{run_id}')
    def run_detail(run_id: str):
        run = store.run(run_id)
        if not run:
            raise HTTPException(404, '执行记录不存在')
        path = engine.log_dir / (run['id'] + '.log')
        run['log'] = path.read_text(encoding='utf-8') if path.exists() else ''
        return run

    @app.post('/api/runs/{run_id}/stop')
    async def stop_run(run_id: str):
        try:
            return engine.cancel(run_id)
        except KeyError:
            raise HTTPException(404, '执行记录不存在') from None

    @app.get('/api/settings')
    def settings():
        return store.settings()

    @app.put('/api/settings')
    def update_settings(body: Settings):
        store.save_settings(body)
        return body

    @app.get('/api/environment')
    def env_summary():
        names = [name for task in store.list_tasks() for name in task['env_names']]
        return {'python': sys.version.split()[0], 'executable': sys.executable,
            'venv': sys.prefix != sys.base_prefix, 'platform': platform.platform(),
            'variables': environment.summary(names), 'errors': engine.errors}

    @app.put('/api/environment')
    def update_environment(body: EnvironmentUpdate):
        try:
            environment.update(body.values)
        except ValueError as error:
            raise HTTPException(422, str(error)) from None
        return env_summary()

    @app.get('/api/plugins/workbuddy/doctor')
    def workbuddy_doctor():
        try:
            from tasks.workbuddy.main import doctor
            return doctor({name: environment.get(name) for name in ('WORKBUDDY_AUTH_FILE','WORKBUDDY_ACCESS_TOKEN','WORKBUDDY_USER_ID','WORKBUDDY_DOMAIN')})
        except ImportError:
            return {'ready': False, 'message': 'WorkBuddy 插件尚未安装'}

    frontend = root / 'frontend'
    if (frontend / 'src').is_dir():
        app.mount('/assets', StaticFiles(directory=frontend / 'src'), name='assets')

    @app.get('/')
    def index():
        if not (frontend / 'index.html').exists():
            return JSONResponse({'message': '前端尚未安装，请访问 /docs'})
        return FileResponse(frontend / 'index.html')

    return app


app = create_app()
