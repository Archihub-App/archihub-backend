"""Coordinated restarts across every process of a deployment.

Some settings can only take effect when the process restarts: which plugins are
active decides what is mounted and what tasks are registered, and both are read
once, at startup. A deployment is not one process - it is a web container, one
or more Celery workers, and possibly several machines - so "restart" has to
reach all of them, and the request arrives at only one.

The mechanism is a revision counter in the ``system`` collection. Requesting a
restart increments it; every process polls it and stops when it observes a value
different from the one it started with. The database is the only thing all the
processes share, so it is what the signal travels through.

WHY THE PROCESS ONLY EVER *STOPS*. Nothing here starts anything. Each container
runs a supervisor (``start.sh``) whose job is to run the backend in a loop, so
exiting is the whole restart - the supervisor brings the replacement up with the
configuration re-read. That keeps this module out of the business of knowing how
to launch the application, which differs between DEV, PROD and a worker.

PID 1 IS ONLY SIGNALLED WHEN IT IS RECOGNISABLY THE SUPERVISOR - SIGHUP, which
the script traps and answers by running the child again. Anywhere else PID 1 is
something this application has no business signalling: in a container it is the
entrypoint, so stopping it stops everything; on a developer's machine it is the
system init, which will not restart anything. There the process stops only
itself, and whatever launched it decides what happens next.

A process that stops without a supervisor stays stopped, ON PURPOSE. Restarting
itself would detach the replacement from the terminal that started it, leaving
an orphan the operator cannot see or stop and a second worker competing for the
same queue under the same node name.
"""

from __future__ import annotations

import datetime
import logging
import os
import signal
import threading
import time

logger = logging.getLogger(__name__)

#: The ``system`` document holding the counter, and the field ids inside it.
#: These are the LEGACY names, kept verbatim: an instance upgrading in place
#: already has this document, and a renamed field would read as revision 0 and
#: restart every process once on first poll.
RESTART_CONTROL_OPTION = "runtime_control"
RESTART_REVISION_ID = "restart_revision"
RESTART_REQUESTED_AT_ID = "restart_requested_at"
RESTART_REASON_ID = "restart_reason"

#: Set by the monitor thread to the pid it belongs to, so a forked worker child
#: starts its own rather than inheriting a thread that is not running in it.
_monitor_pid: int | None = None
_monitor_lock = threading.Lock()


def get_restart_poll_interval() -> float:
    """Seconds between polls, floored at 1.

    The floor is not tidiness: this runs in every process of the deployment, so
    a mistyped 0 would turn a restart facility into a self-inflicted load
    generator against the settings collection.
    """
    try:
        interval = float(os.environ.get("ARCHIHUB_RESTART_POLL_INTERVAL", 5))
    except (TypeError, ValueError):
        interval = 5.0
    return max(1.0, interval)


def _restart_target_pid() -> int:
    try:
        return int(os.environ.get("ARCHIHUB_RESTART_SIGNAL_PID", "1"))
    except (TypeError, ValueError):
        return 1


def _process_cmdline(pid: int) -> list[str]:
    try:
        with open(f"/proc/{pid}/cmdline", "rb") as handle:
            return [part.decode("utf-8") for part in handle.read().split(b"\0") if part]
    except OSError:
        return []


def _is_supervisor(cmdline: list[str]) -> bool:
    command_text = " ".join(cmdline)
    return "start.sh" in command_text or "start_celery.sh" in command_text


def _restart_signal(target_pid: int) -> int:
    """SIGHUP to a supervisor that will restart the child, SIGTERM otherwise.

    Sending SIGHUP to something that is not the supervisor is not a restart -
    the default disposition terminates the process and nothing brings it back.
    """
    name = os.environ.get("ARCHIHUB_RESTART_SIGNAL", "").upper().strip()
    if name:
        return getattr(signal, f"SIG{name}", signal.SIGTERM)

    if target_pid == 1 and _is_supervisor(_process_cmdline(target_pid)):
        return signal.SIGHUP
    return signal.SIGTERM


def _field_value(record: dict | None, field_id: str, default=None):
    if not record:
        return default
    for item in record.get("data") or []:
        if isinstance(item, dict) and item.get("id") == field_id:
            return item.get("value", default)
    return default


def get_restart_revision() -> int:
    from archihub.infra.mongo import get_mongo

    record = get_mongo().get_record("system", {"name": RESTART_CONTROL_OPTION})
    try:
        return int(_field_value(record, RESTART_REVISION_ID, 0) or 0)
    except (TypeError, ValueError):
        return 0


def request_runtime_restart(reason: str = "manual") -> int:
    """Record that every process should restart, and return the new revision."""
    from archihub.infra.mongo import get_mongo

    mongo = get_mongo()
    record = mongo.get_record("system", {"name": RESTART_CONTROL_OPTION})
    revision = get_restart_revision() + 1

    data = (record or {}).get("data") or []
    fields = {i.get("id"): i for i in data if isinstance(i, dict) and i.get("id")}
    fields[RESTART_REVISION_ID] = {"id": RESTART_REVISION_ID, "value": revision}
    fields[RESTART_REQUESTED_AT_ID] = {
        "id": RESTART_REQUESTED_AT_ID,
        "value": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    }
    fields[RESTART_REASON_ID] = {"id": RESTART_REASON_ID, "value": reason}
    payload = list(fields.values())

    if record:
        mongo.update_record_operator(
            "system", {"name": RESTART_CONTROL_OPTION}, {"$set": {"data": payload}}
        )
    else:
        mongo.insert_record(
            "system",
            {"name": RESTART_CONTROL_OPTION, "label": "Runtime control", "data": payload},
        )

    logger.warning("Runtime restart requested (%s); revision is now %d", reason, revision)
    return revision


def terminate_runtime() -> None:
    """Stop, so that whatever supervises this process starts a replacement.

    NOTHING HERE STARTS ANYTHING, and that is deliberate. Every deployment has
    something whose job is to run the backend again: ``start.sh`` and
    ``start_celery.sh`` are loops, and the containers carry ``restart: always``.
    Exiting is therefore the whole restart, and the process is spared having to
    know how it is launched - which differs between DEV, PROD and a worker.

    PID 1 IS ONLY EVER SIGNALLED WHEN IT IS RECOGNISABLY THE SUPERVISOR. It is
    the one process that must not be signalled speculatively: in a container it
    is the entrypoint, so a SIGTERM there stops everything rather than
    restarting one part of it, and the default disposition of SIGHUP would
    terminate a PID 1 that has no handler for it. Where PID 1 is something else,
    this process stops itself and lets its own supervisor react.

    ``ARCHIHUB_RESTART_SIGNAL_PID`` overrides the target for a deployment whose
    supervisor is not PID 1; an explicitly named pid is trusted, because naming
    it is a statement about that deployment's process tree.
    """
    explicit = "ARCHIHUB_RESTART_SIGNAL_PID" in os.environ
    target_pid = _restart_target_pid()

    if target_pid > 0 and target_pid != os.getpid():
        if explicit or _is_supervisor(_process_cmdline(target_pid)):
            try:
                os.kill(target_pid, _restart_signal(target_pid))
                return
            except (PermissionError, ProcessLookupError, OSError):
                logger.warning(
                    "Could not signal the supervisor at pid %s; stopping this process instead",
                    target_pid,
                )

    logger.warning("Stopping process %s so it can be started again", os.getpid())
    os.kill(os.getpid(), signal.SIGTERM)


def schedule_local_restart(delay: float = 1.0) -> None:
    """Stop shortly, so the response reaches the client first.

    Without the delay the process would die inside the request that asked for
    the restart, and the operator would see a dropped connection rather than the
    confirmation that their request was accepted.
    """

    def _shutdown() -> None:
        time.sleep(delay)
        terminate_runtime()

    threading.Thread(target=_shutdown, daemon=True).start()


def start_runtime_restart_monitor() -> None:
    """Watch the revision counter and stop this process when it changes."""
    global _monitor_pid

    current_pid = os.getpid()
    with _monitor_lock:
        # Keyed on the pid, not a bare flag: a forked Celery child inherits the
        # parent's module state but not its threads, so a flag alone would leave
        # the child believing it was already watching when nothing was.
        if _monitor_pid == current_pid:
            return
        _monitor_pid = current_pid

    poll_interval = get_restart_poll_interval()

    def _monitor() -> None:
        # The baseline is read INSIDE the thread. Reading it in the caller would
        # put a database round trip on the startup path of every process, to
        # set up a facility none of them needs in order to serve a request -
        # and where the database is unreachable, startup would block for the
        # whole connection timeout before continuing.
        baseline: int | None = None

        while True:
            try:
                current = get_restart_revision()
            except Exception:
                # A database blip must not restart the deployment, so an
                # unreadable revision is skipped rather than treated as changed.
                logger.debug("Could not read the restart revision", exc_info=True)
                time.sleep(poll_interval)
                continue

            if baseline is None:
                baseline = current
            elif current != baseline:
                logger.warning("Restart requested; stopping process %s", current_pid)
                terminate_runtime()
                return

            time.sleep(poll_interval)

    threading.Thread(target=_monitor, daemon=True).start()
