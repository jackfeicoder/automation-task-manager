from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from croniter import croniter
from backend.app.models import Schedule


def utc_now():
    return datetime.now(timezone.utc)


def next_time(schedule: Schedule, after: datetime | None = None):
    after = after or utc_now()
    if schedule.kind == 'manual':
        return None
    if schedule.kind == 'interval':
        return (after + timedelta(minutes=schedule.interval_minutes)).isoformat()
    local = after.astimezone(ZoneInfo(schedule.timezone))
    if schedule.kind == 'cron':
        return croniter(schedule.cron, local).get_next(datetime).astimezone(timezone.utc).isoformat()
    hour, minute = map(int, schedule.time.split(':'))
    for offset in range(8):
        day = local + timedelta(days=offset)
        candidate = day.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if candidate <= local:
            continue
        if schedule.kind == 'daily' or candidate.weekday() in schedule.weekdays:
            return candidate.astimezone(timezone.utc).isoformat()
    raise ValueError('找不到下次执行时间')


def local_day(schedule: Schedule):
    return utc_now().astimezone(ZoneInfo(schedule.timezone)).date().isoformat()
