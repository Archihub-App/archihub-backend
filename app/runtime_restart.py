import datetime
import logging
import os
import signal
import subprocess
import threading
import time

from app.utils import DatabaseHandler


logger = logging.getLogger(__name__)

RESTART_CONTROL_OPTION = 'runtime_control'
RESTART_REVISION_ID = 'restart_revision'
RESTART_REQUESTED_AT_ID = 'restart_requested_at'
RESTART_REASON_ID = 'restart_reason'

_monitor_pid = None
_monitor_lock = threading.Lock()


def get_restart_poll_interval():
    try:
        interval = float(os.environ.get('ARCHIHUB_RESTART_POLL_INTERVAL', 5))
    except (TypeError, ValueError):
        interval = 5.0

    return max(1.0, interval)


def _get_restart_target_pid():
    try:
        return int(os.environ.get('ARCHIHUB_RESTART_SIGNAL_PID', '1'))
    except (TypeError, ValueError):
        return 1


def _get_process_cmdline(pid):
    try:
        with open(f'/proc/{pid}/cmdline', 'rb') as handle:
            return [part.decode('utf-8') for part in handle.read().split(b'\0') if part]
    except OSError:
        return []


def _is_restartable_shell_supervisor(cmdline):
    command_text = ' '.join(cmdline)
    return 'start.sh' in command_text or 'start_celery.sh' in command_text


def _get_restart_signal(target_pid):
    signal_name = os.environ.get('ARCHIHUB_RESTART_SIGNAL', '').upper().strip()
    if signal_name:
        return getattr(signal, f'SIG{signal_name}', signal.SIGTERM)

    cmdline = _get_process_cmdline(target_pid)
    if target_pid == 1 and _is_restartable_shell_supervisor(cmdline):
        return signal.SIGHUP

    return signal.SIGTERM


def _get_current_cmdline():
    try:
        with open('/proc/self/cmdline', 'rb') as handle:
            return [part.decode('utf-8') for part in handle.read().split(b'\0') if part]
    except OSError:
        return []


def _should_self_respawn():
    if os.environ.get('ARCHIHUB_DISABLE_SELF_RESPAWN', '').lower() in {'1', 'true', 'yes'}:
        return False

    cmdline = _get_current_cmdline()
    if not cmdline:
        return False

    command_text = ' '.join(cmdline)
    return 'celery' in command_text or 'flask' in command_text or 'gunicorn' in command_text


def _respawn_current_process():
    cmdline = _get_current_cmdline()
    if not cmdline:
        return False

    env = os.environ.copy()
    command_text = ' '.join(cmdline)

    if 'flask' in command_text:
        env.pop('WERKZEUG_RUN_MAIN', None)
        env.pop('WERKZEUG_SERVER_FD', None)

    try:
        subprocess.Popen(
            cmdline,
            cwd=os.getcwd(),
            env=env,
            start_new_session=True,
            close_fds=True,
        )
        logger.warning('Respawned current process with command: %s', ' '.join(cmdline))
        return True
    except Exception:
        logger.exception('Failed to respawn current process')
        return False


def _get_field_value(record, field_id, default=None):
    if not record:
        return default

    for item in record.get('data', []) or []:
        if item.get('id') == field_id:
            return item.get('value', default)

    return default


def get_restart_revision(mongodb=None):
    mongodb = mongodb or DatabaseHandler.DatabaseHandler()
    record = mongodb.get_record('system', {'name': RESTART_CONTROL_OPTION})

    try:
        return int(_get_field_value(record, RESTART_REVISION_ID, 0) or 0)
    except (TypeError, ValueError):
        return 0


def request_runtime_restart(reason='manual', mongodb=None):
    mongodb = mongodb or DatabaseHandler.DatabaseHandler()
    record = mongodb.get_record('system', {'name': RESTART_CONTROL_OPTION})
    revision = get_restart_revision(mongodb) + 1
    requested_at = datetime.datetime.utcnow().isoformat()

    data = record.get('data', []) if record else []
    field_map = {item.get('id'): item for item in data if isinstance(item, dict) and item.get('id')}

    field_map[RESTART_REVISION_ID] = {
        'id': RESTART_REVISION_ID,
        'value': revision,
    }
    field_map[RESTART_REQUESTED_AT_ID] = {
        'id': RESTART_REQUESTED_AT_ID,
        'value': requested_at,
    }
    field_map[RESTART_REASON_ID] = {
        'id': RESTART_REASON_ID,
        'value': reason,
    }

    payload = list(field_map.values())

    if record:
        mongodb.update_record_operator(
            'system',
            {'name': RESTART_CONTROL_OPTION},
            {'$set': {'data': payload}}
        )
    else:
        mongodb.insert_record(
            'system',
            {
                'name': RESTART_CONTROL_OPTION,
                'label': 'Runtime control',
                'data': payload,
            }
        )

    return revision


def terminate_runtime():
    target_pid = _get_restart_target_pid()
    target_cmdline = _get_process_cmdline(target_pid)
    restart_signal = _get_restart_signal(target_pid)

    if target_pid == 1 and not _is_restartable_shell_supervisor(target_cmdline) and _should_self_respawn():
        logger.warning('PID 1 is not an ArchiHub supervisor; attempting local process respawn instead')
        if _respawn_current_process():
            os.kill(os.getpid(), signal.SIGTERM)
            return

    if target_pid > 0:
        try:
            os.kill(target_pid, restart_signal)
            return
        except (PermissionError, ProcessLookupError, OSError):
            if _should_self_respawn():
                logger.warning('Failed to signal PID %s for runtime restart; attempting local process respawn instead', target_pid)
                if _respawn_current_process():
                    os.kill(os.getpid(), signal.SIGTERM)
                    return

            logger.warning('Failed to signal PID %s for runtime restart; terminating current process instead', target_pid)

    os.kill(os.getpid(), signal.SIGTERM)


def schedule_local_restart(delay=1.0):
    def _shutdown():
        time.sleep(delay)
        terminate_runtime()

    threading.Thread(target=_shutdown, daemon=True).start()


def start_runtime_restart_monitor():
    global _monitor_pid

    current_pid = os.getpid()
    with _monitor_lock:
        if _monitor_pid == current_pid:
            return
        _monitor_pid = current_pid

    mongodb = DatabaseHandler.DatabaseHandler()
    initial_revision = get_restart_revision(mongodb)
    poll_interval = get_restart_poll_interval()

    def _monitor():
        while True:
            time.sleep(poll_interval)

            try:
                current_revision = get_restart_revision(mongodb)
            except Exception:
                logger.exception('Failed to read runtime restart state')
                continue

            if current_revision != initial_revision:
                logger.warning('Runtime restart requested; stopping process %s', current_pid)
                terminate_runtime()
                return

    threading.Thread(target=_monitor, daemon=True).start()