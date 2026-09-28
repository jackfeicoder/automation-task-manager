import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

from backend.app.models import Settings, Task
from backend.app.scheduler.timing import local_day, next_time, utc_now

ACTIVE = ('queued', 'running')


class Store:
    def __init__(self, data_dir: Path):
        self.data_dir = data_dir
        data_dir.mkdir(parents=True, exist_ok=True)
        self.path = data_dir / 'state.db'
        with self.connect() as db:
            db.executescript('''
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS tasks (
                    id TEXT PRIMARY KEY, config TEXT NOT NULL, next_run TEXT, created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS runs (
                    id TEXT PRIMARY KEY, task_id TEXT NOT NULL, task_name TEXT NOT NULL,
                    status TEXT NOT NULL, source TEXT NOT NULL, snapshot TEXT NOT NULL,
                    day TEXT NOT NULL, created_at TEXT NOT NULL, started_at TEXT,
                    finished_at TEXT, attempt INTEGER NOT NULL DEFAULT 0,
                    message TEXT NOT NULL DEFAULT '', result TEXT, exit_code INTEGER
                );
                CREATE UNIQUE INDEX IF NOT EXISTS one_active_run ON runs(task_id)
                    WHERE status IN ('queued', 'running');
                CREATE INDEX IF NOT EXISTS runs_created ON runs(created_at DESC);
                CREATE TABLE IF NOT EXISTS settings (id INTEGER PRIMARY KEY, config TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS removed_plugins (id TEXT PRIMARY KEY);
            ''')
            db.execute('INSERT OR IGNORE INTO settings VALUES (1, ?)', (Settings().model_dump_json(),))

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def settings(self):
        with self.connect() as db:
            return Settings.model_validate_json(db.execute('SELECT config FROM settings WHERE id=1').fetchone()[0])

    def save_settings(self, settings):
        with self.connect() as db:
            db.execute('UPDATE settings SET config=? WHERE id=1', (settings.model_dump_json(),))

    def task(self, task_id):
        with self.connect() as db:
            row = db.execute('SELECT * FROM tasks WHERE id=?', (task_id,)).fetchone()
            return Task.model_validate_json(row['config']) if row else None

    def list_tasks(self):
        with self.connect() as db:
            rows = db.execute('SELECT * FROM tasks ORDER BY created_at, id').fetchall()
            result = []
            for row in rows:
                task = json.loads(row['config'])
                last = db.execute('SELECT id,status,message,created_at,finished_at FROM runs WHERE task_id=? ORDER BY created_at DESC LIMIT 1', (row['id'],)).fetchone()
                task.update(next_run=row['next_run'], last_run=dict(last) if last else None)
                result.append(task)
            return result

    def save_task(self, task, create=False):
        due = next_time(task.schedule) if task.enabled else None
        with self.connect() as db:
            if create:
                db.execute('INSERT INTO tasks VALUES(?,?,?,?)', (task.id, task.model_dump_json(), due, utc_now().isoformat()))
                db.execute('DELETE FROM removed_plugins WHERE id=?', (task.id,))
            else:
                db.execute('UPDATE tasks SET config=?,next_run=? WHERE id=?', (task.model_dump_json(), due, task.id))

    def delete_task(self, task_id):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            if db.execute("SELECT 1 FROM runs WHERE task_id=? AND status IN ('queued','running')", (task_id,)).fetchone():
                raise ValueError('任务仍在队列或执行中，请先停止执行')
            db.execute('DELETE FROM tasks WHERE id=?', (task_id,))
            db.execute('INSERT OR IGNORE INTO removed_plugins VALUES(?)', (task_id,))

    def removed(self, task_id):
        with self.connect() as db:
            return bool(db.execute('SELECT 1 FROM removed_plugins WHERE id=?', (task_id,)).fetchone())

    def enqueue(self, task_id, source='manual'):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT config FROM tasks WHERE id=?', (task_id,)).fetchone()
            if not row:
                raise KeyError(task_id)
            task = Task.model_validate_json(row[0])
            active = db.execute("SELECT id FROM runs WHERE task_id=? AND status IN ('queued','running')", (task_id,)).fetchone()
            if active:
                return {'id': active[0], 'status': 'already_running', 'task_id': task_id}
            day = local_day(task.schedule)
            if task.daily_once and db.execute("SELECT 1 FROM runs WHERE task_id=? AND day=? AND status IN ('success','already_completed')", (task_id, day)).fetchone():
                return {'status': 'already_completed', 'task_id': task_id}
            run_id = uuid4().hex
            db.execute('INSERT INTO runs(id,task_id,task_name,status,source,snapshot,day,created_at) VALUES(?,?,?,?,?,?,?,?)',
                (run_id, task.id, task.name, 'queued', source, task.model_dump_json(), day, utc_now().isoformat()))
            return {'id': run_id, 'status': 'queued', 'task_id': task_id}

    def due_tasks(self, catch_up=True):
        now = utc_now()
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            rows = db.execute('SELECT * FROM tasks WHERE next_run IS NOT NULL AND next_run<=?', (now.isoformat(),)).fetchall()
            result = []
            for row in rows:
                task = Task.model_validate_json(row['config'])
                db.execute('UPDATE tasks SET next_run=? WHERE id=?', (next_time(task.schedule, now) if task.enabled else None, task.id))
                # Catch up at most once. With catch_up disabled, tolerate only a normal scheduler tick.
                from datetime import datetime
                if task.enabled and (catch_up or (now - datetime.fromisoformat(row['next_run'])).total_seconds() < 5):
                    result.append(task.id)
            return result

    def runs(self, task_id=None, limit=100):
        with self.connect() as db:
            if task_id:
                rows = db.execute('SELECT * FROM runs WHERE task_id=? ORDER BY created_at DESC LIMIT ?', (task_id, limit)).fetchall()
            else:
                rows = db.execute('SELECT * FROM runs ORDER BY created_at DESC LIMIT ?', (limit,)).fetchall()
            return [self.decode_run(row) for row in rows]

    def run(self, run_id):
        with self.connect() as db:
            row = db.execute('SELECT * FROM runs WHERE id=?', (run_id,)).fetchone()
            return self.decode_run(row) if row else None

    @staticmethod
    def decode_run(row):
        result = dict(row)
        result['snapshot'] = json.loads(result['snapshot'])
        result['result'] = json.loads(result['result']) if result['result'] else None
        return result

    def queued(self):
        with self.connect() as db:
            return [self.decode_run(row) for row in db.execute("SELECT * FROM runs WHERE status='queued' ORDER BY created_at").fetchall()]

    def update_run(self, run_id, **values):
        allowed = {'status','started_at','finished_at','attempt','message','result','exit_code'}
        if not values.keys() <= allowed:
            raise ValueError('Invalid run update')
        if 'result' in values and values['result'] is not None:
            values['result'] = json.dumps(values['result'], ensure_ascii=False)
        with self.connect() as db:
            db.execute('UPDATE runs SET ' + ','.join(key+'=?' for key in values) + ' WHERE id=?', (*values.values(), run_id))

    def recover(self):
        with self.connect() as db:
            db.execute("UPDATE runs SET status='interrupted',finished_at=?,message='服务中断；下次执行将重新核验任务状态' WHERE status='running'", (utc_now().isoformat(),))
