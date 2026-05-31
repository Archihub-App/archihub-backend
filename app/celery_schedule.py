from datetime import timedelta
import logging


logger = logging.getLogger(__name__)

DYNAMIC_SCHEDULE_PREFIX = 'plugin-schedule:'


def parse_execution_time(hour_execution):
    if hour_execution is None:
        return 0, 0

    if isinstance(hour_execution, str):
        hour_execution = hour_execution.strip()

    if isinstance(hour_execution, str) and ':' in hour_execution:
        try:
            return map(int, hour_execution.split(':', 1))
        except (ValueError, AttributeError):
            return 0, 0

    try:
        return int(hour_execution), 0
    except (TypeError, ValueError):
        return 0, 0


def get_schedule(task_config):
    periodicity = task_config.get('periodicity')
    interval_value = task_config.get('interval_value')

    if periodicity in {'every_x_minutes', 'every_x_hours'}:
        try:
            interval_value = int(interval_value)
        except (TypeError, ValueError):
            return None

        if interval_value <= 0:
            return None

        if periodicity == 'every_x_minutes':
            return timedelta(minutes=interval_value)

        return timedelta(hours=interval_value)

    hour, minute = parse_execution_time(task_config.get('hour_execution'))

    if periodicity == 'once_a_day':
        from celery.schedules import crontab
        return crontab(hour=hour, minute=minute)
    elif periodicity == 'once_a_week':
        from celery.schedules import crontab
        return crontab(hour=hour, minute=minute, day_of_week='0')
    elif periodicity == 'once_a_month':
        from celery.schedules import crontab
        return crontab(hour=hour, minute=minute, day_of_month='1')
    elif periodicity == 'once_a_year':
        from celery.schedules import crontab
        return crontab(hour=hour, minute=minute, day_of_month='1', month_of_year='1')

    return None


def build_schedule_name(plugin_name, task_config):
    periodicity = task_config.get('periodicity', '')
    execution_label = task_config.get('hour_execution', '')
    if periodicity in {'every_x_minutes', 'every_x_hours'}:
        execution_label = task_config.get('interval_value', '')

    return f"{DYNAMIC_SCHEDULE_PREFIX}{plugin_name}:{task_config.get('task', '')}:{periodicity}:{execution_label}"


def build_schedule_entry(task_config):
    task_name = task_config.get('task')
    if not task_name:
        return None

    schedule = get_schedule(task_config)
    if schedule is None:
        return None

    return {
        'task': task_name,
        'schedule': schedule,
        'args': (),
        'kwargs': {},
        'options': {}
    }


def build_plugin_beat_schedule(mongodb):
    plugins_record = mongodb.get_record(
        'system',
        {'name': 'active_plugins'},
        fields={'data': 1, 'plugins_settings': 1}
    ) or {}

    active_plugins = plugins_record.get('data', []) or []
    plugin_settings = plugins_record.get('plugins_settings', {}) or {}
    schedule = {}

    for plugin_name in active_plugins:
        try:
            plugin_module = __import__(
                f'app.plugins.{plugin_name}',
                fromlist=['plugin_info']
            )
        except Exception as exc:
            logger.warning('Failed to import plugin %s for scheduler sync: %s', plugin_name, exc)
            continue

        capabilities = getattr(plugin_module, 'plugin_info', {}).get('capabilities') or []
        if 'scheduler' not in capabilities:
            continue

        current = plugin_settings.get(plugin_name) or {}
        for task_config in current.get('schedule_tasks', []):
            entry = build_schedule_entry(task_config)
            if entry is None:
                continue

            schedule[build_schedule_name(plugin_name, task_config)] = entry

    return schedule