from datetime import time
from typing import Literal
from zoneinfo import ZoneInfo

from croniter import croniter
from pydantic import BaseModel, Field, field_validator, model_validator


class Schedule(BaseModel):
    kind: Literal['manual', 'daily', 'weekly', 'interval', 'cron'] = 'manual'
    time: str = '09:00'
    weekdays: list[int] = Field(default_factory=lambda: [0])
    interval_minutes: int = Field(default=1440, ge=1, le=525600)
    cron: str = '0 9 * * *'
    timezone: str = 'Asia/Shanghai'

    @field_validator('timezone')
    @classmethod
    def timezone_valid(cls, value):
        try:
            ZoneInfo(value)
        except (KeyError, ValueError):
            raise ValueError('无效的时区')
        return value

    @field_validator('time')
    @classmethod
    def time_valid(cls, value):
        try:
            time.fromisoformat(value)
            if len(value) != 5:
                raise ValueError()
        except ValueError:
            raise ValueError('时间格式应为 HH:MM')
        return value

    @model_validator(mode='after')
    def check_schedule(self):
        if self.kind == 'weekly' and (not self.weekdays or any(x < 0 or x > 6 for x in self.weekdays)):
            raise ValueError('每周任务至少选择一天，0 为周一，6 为周日')
        if self.kind == 'cron' and (len(self.cron.split()) != 5 or not croniter.is_valid(self.cron)):
            raise ValueError('Cron 必须是有效的五段表达式')
        return self


class Task(BaseModel):
    id: str = Field(pattern=r'^[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}$')
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default='', max_length=1000)
    enabled: bool = False
    command: list[str] = Field(min_length=1, max_length=64)
    cwd: str = 'tasks/example'
    schedule: Schedule = Field(default_factory=Schedule)
    timeout_seconds: int = Field(default=120, ge=1, le=86400)
    max_attempts: int = Field(default=1, ge=1, le=5)
    resource_group: str = Field(default='', max_length=120)
    daily_once: bool = False
    env_names: list[str] = Field(default_factory=list)

    @field_validator('command')
    @classmethod
    def valid_command(cls, value):
        if any(not part or '\x00' in part for part in value):
            raise ValueError('启动命令含空参数或无效字符')
        return value

    @field_validator('env_names')
    @classmethod
    def valid_env(cls, value):
        import re
        if any(not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', name) for name in value):
            raise ValueError('环境变量名称无效')
        if any(name.upper() in {'GITHUB_TOKEN', 'GH_TOKEN', 'AUTOMATION_API_TOKEN'} for name in value):
            raise ValueError('管理与发布密钥不传给任务进程')
        return value


class Settings(BaseModel):
    max_parallel: int = Field(default=2, ge=1, le=16)
    timezone: str = 'Asia/Shanghai'
    scheduler_enabled: bool = True
    catch_up: bool = True
    retry_delay_seconds: int = Field(default=10, ge=1, le=3600)
    log_retention_days: int = Field(default=30, ge=1, le=3650)
    timezone_valid = field_validator('timezone')(Schedule.timezone_valid.__func__)


class BatchRequest(BaseModel):
    ids: list[str] = Field(min_length=1, max_length=100)
    action: Literal['run', 'enable', 'disable'] = 'run'


class EnvironmentUpdate(BaseModel):
    values: dict[str, str | None]
