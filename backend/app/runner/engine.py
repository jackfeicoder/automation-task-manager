import asyncio
import codecs
from datetime import timedelta
import json
import os
from pathlib import Path
import sys
import subprocess

from backend.app.models import Task
from backend.app.runner.processes import ProcessTree
from backend.app.scheduler.timing import utc_now

RESULT_PREFIX = 'AUTOMATION_RESULT='
RESULT_STATUSES = {'success','already_completed','failed','needs_login','needs_attention'}


def task_directory(root, cwd):
    directory = (root / cwd).resolve()
    if not directory.is_relative_to(root.resolve()) or not directory.is_dir():
        raise ValueError('工作目录必须是项目内已存在的目录')
    return directory


class Engine:
    def __init__(self, root, store, environment):
        self.root, self.store, self.environment = root, store, environment
        self.active = {}
        self.loop_task = None
        self.log_dir = store.data_dir / 'logs'
        self.log_dir.mkdir(exist_ok=True)
        self.errors = []
        self.last_cleanup = None

    async def start(self):
        self.store.recover()
        self.loop_task = asyncio.create_task(self.loop())

    async def stop(self):
        if self.loop_task:
            self.loop_task.cancel()
            await asyncio.gather(self.loop_task, return_exceptions=True)
        for item in list(self.active.values()):
            item['future'].cancel()
        await asyncio.gather(*(item['future'] for item in list(self.active.values())), return_exceptions=True)

    async def loop(self):
        while True:
            try:
                settings = self.store.settings()
                if settings.scheduler_enabled:
                    for task_id in self.store.due_tasks(settings.catch_up):
                        self.store.enqueue(task_id, 'schedule')
                for run_id, item in list(self.active.items()):
                    if item['future'].done():
                        # execute() records errors; consuming the result prevents unhandled warnings.
                        item['future'].result()
                        del self.active[run_id]
                groups = {item['group'] for item in self.active.values() if item['group']}
                for run in self.store.queued():
                    if len(self.active) >= settings.max_parallel:
                        break
                    task = Task.model_validate(run['snapshot'])
                    if task.resource_group and task.resource_group in groups:
                        continue
                    self.store.update_run(run['id'], status='running', started_at=utc_now().isoformat())
                    self.active[run['id']] = {'group': task.resource_group,
                        'future': asyncio.create_task(self.execute(run, task))}
                    if task.resource_group:
                        groups.add(task.resource_group)
                if self.last_cleanup is None or (utc_now() - self.last_cleanup).total_seconds() > 3600:
                    self.clean_logs(settings.log_retention_days)
                    self.last_cleanup = utc_now()
            except asyncio.CancelledError:
                raise
            except Exception as error:
                self.errors = (self.errors + [self.environment.redact(str(error))])[-10:]
            await asyncio.sleep(0.5)

    def clean_logs(self, days):
        cutoff = (utc_now() - timedelta(days=days)).timestamp()
        active = set(self.active)
        for path in self.log_dir.glob('*.log'):
            if path.stem not in active and path.stat().st_mtime < cutoff:
                path.unlink(missing_ok=True)

    def cancel(self, run_id):
        run = self.store.run(run_id)
        if not run:
            raise KeyError(run_id)
        if run_id in self.active:
            self.active[run_id]['future'].cancel()
        elif run['status'] == 'queued':
            self.store.update_run(run_id, status='cancelled', finished_at=utc_now().isoformat(), message='已取消排队')
        return {'status': 'stopping' if run_id in self.active else self.store.run(run_id)['status']}

    async def execute(self, run, task):
        run_id = run['id']
        status, message, result, exit_code = 'failed', '', None, None
        try:
            for attempt in range(1, task.max_attempts + 1):
                self.store.update_run(run_id, attempt=attempt)
                status, message, result, exit_code = await self.attempt(run_id, task, attempt)
                if status != 'failed' or not result or not result.get('retryable') or attempt == task.max_attempts:
                    break
                await asyncio.sleep(self.store.settings().retry_delay_seconds)
        except asyncio.CancelledError:
            status, message = 'cancelled', '执行已停止，相关子进程已清理'
        except Exception as error:
            message = self.environment.redact(str(error) or type(error).__name__)
            self.append_log(run_id, '平台错误: ' + message)
        finally:
            self.store.update_run(run_id, status=status, message=message, result=result,
                                  exit_code=exit_code, finished_at=utc_now().isoformat())

    def append_log(self, run_id, text):
        path = self.log_dir / (run_id + '.log')
        if path.exists() and path.stat().st_size > 2 * 1024 * 1024:
            return
        with path.open('a', encoding='utf-8') as stream:
            stream.write(utc_now().isoformat() + ' ' + self.environment.redact(text)[:65536] + '\n')

    async def attempt(self, run_id, task, attempt):
        command = [sys.executable if part == '{python}' else part for part in task.command]
        directory = task_directory(self.root, task.cwd)
        env = self.environment.child(task.env_names)
        self.append_log(run_id, f'开始第 {attempt} 次执行')
        options = {'start_new_session': True} if os.name != 'nt' else {'creationflags': 0x08000000}
        process, tree, reader = None, None, None
        parsed = None

        async def read_output():
            nonlocal parsed
            decoder = codecs.getincrementaldecoder('utf-8')(errors='replace')
            buffer = ''
            while chunk := await asyncio.to_thread(process.stdout.read1, 4096):
                buffer += decoder.decode(chunk)
                while '\n' in buffer:
                    line, buffer = buffer.split('\n', 1)
                    consume(line.rstrip('\r'))
                if len(buffer) > 65536:
                    consume(buffer[:65536])
                    buffer = ''
            buffer += decoder.decode(b'', final=True)
            if buffer:
                consume(buffer)

        def consume(line):
            nonlocal parsed
            if line.startswith(RESULT_PREFIX):
                try:
                    value = json.loads(line[len(RESULT_PREFIX):])
                    if isinstance(value, dict) and value.get('status') in RESULT_STATUSES:
                        parsed = json.loads(self.environment.redact(json.dumps(value, ensure_ascii=False)))
                    else:
                        self.append_log(run_id, '任务结果状态无效')
                except (ValueError, TypeError):
                    self.append_log(run_id, '任务结果 JSON 格式无效')
            else:
                self.append_log(run_id, line)

        try:
            process = subprocess.Popen(command, cwd=directory, env=env, stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, **options)
            tree = ProcessTree(process.pid)
            reader = asyncio.create_task(read_output())
            try:
                await asyncio.wait_for(asyncio.to_thread(process.wait), task.timeout_seconds)
            except asyncio.TimeoutError:
                self.append_log(run_id, '超时，清理进程树')
                return 'timeout', '执行超时，相关子进程已清理', None, None
            # Closing the job kills descendants that might keep stdout open.
            tree.close()
            await asyncio.wait_for(reader, 5)
            code = process.returncode
            if parsed:
                if code != 0 and parsed['status'] in {'success','already_completed'}:
                    return 'failed', '结果与退出码不一致', parsed, code
                return parsed['status'], str(parsed.get('message', '任务执行结束'))[:2000], parsed, code
            return ('success', '脚本正常结束', None, code) if code == 0 else ('failed', f'脚本异常退出，退出码 {code}', None, code)
        finally:
            if tree:
                tree.close()
            if process and process.poll() is None:
                try:
                    process.kill()
                except ProcessLookupError:
                    pass
                await asyncio.to_thread(process.wait)
            if reader and not reader.done():
                reader.cancel()
                await asyncio.gather(reader, return_exceptions=True)
            if process and process.stdout:
                process.stdout.close()
