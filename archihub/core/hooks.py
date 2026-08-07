"""The hook bus.

Port of ``app/utils/HookHandler.py``. This is the mechanism by which side
effects fan out without domain modules importing each other: creating a resource
fires ``resource_create``, and whatever registered against that name runs -
Elasticsearch indexing, Qdrant vectorisation, plugin post-processing.

The original is framework-agnostic (no Flask imports), so the logic is preserved
closely. What changed:

* ``register()`` swallowed every exception with ``print(str(e))``. A hook that
  failed to register did so silently, and the feature it powered simply never
  ran - with no error anywhere. Registration failures now raise, because a hook
  that is silently absent is indistinguishable from one that ran and did nothing.
* An ``IndexError`` was reachable when correlating Celery task ids back to task
  names (see ``_register_chain_tasks``).
* ``print`` replaced by module logging throughout.

TWO BEHAVIOURS THAT LOOK LIKE BUGS BUT ARE LOAD-BEARING, preserved deliberately:

1. ``queue`` orders callbacks ascending, and built-in indexing registers at 101
   (Elasticsearch) and 102 (Qdrant) so plugins can interpose *before* indexing
   happens. Changing the sort would silently reorder every side effect.
2. ``call()`` returns the return value of the LAST synchronous callback to run
   (in ``queue`` order), defaulting to ``additional_args[0]`` when no
   synchronous callback is registered. Note what this is NOT: callbacks are each
   handed the *original* arguments, so they do not chain into one another - a
   second callback does not see the first one's output. Callers such as
   ``validate_field`` rely on the last-writer-wins result, and on the untouched
   payload coming back when nothing is registered. Celery-task callbacks are
   fire-and-forget; their results never take part in this at all.
"""

from __future__ import annotations

import logging
from typing import Any, Callable

from celery import chain

logger = logging.getLogger(__name__)


def _is_celery_task(func: Any) -> bool:
    """Celery tasks expose ``.si()`` (immutable signature); plain callables do not."""
    return hasattr(func, "si")


def _same_callback(registered: Callable, candidate: Callable) -> bool:
    """Whether two callables are the same registration.

    Bound methods need special handling: ``obj.method == obj.method`` is False
    for two separately-created instances of the same class, so plugins that are
    re-instantiated (which ``filesProcessing`` does inside its Celery tasks)
    would register duplicate hooks and run their side effects twice.
    """
    reg_bound = hasattr(registered, "__self__") and hasattr(registered, "__func__")
    cand_bound = hasattr(candidate, "__self__") and hasattr(candidate, "__func__")

    if reg_bound and cand_bound:
        return registered.__func__ == candidate.__func__ and type(registered.__self__) is type(
            candidate.__self__
        )
    return registered == candidate


class HookHandler:
    """Process-wide singleton event bus."""

    _instance: HookHandler | None = None
    _is_initialized = False

    def __new__(cls) -> HookHandler:
        if not isinstance(cls._instance, cls):
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self) -> None:
        if not self._is_initialized:
            self._is_initialized = True
            # hook name -> list of (queue, func, args, kwargs)
            self.hooks: dict[str, list[tuple[int, Callable, list, dict]]] = {}

    # -- registration ---------------------------------------------------
    def register(
        self,
        hook_name: str,
        func: Callable,
        args: Any = None,
        kwargs: dict | None = None,
        queue: int = 0,
    ) -> None:
        """Register ``func`` to run when ``hook_name`` fires.

        Lower ``queue`` runs first. Duplicate registrations are ignored so that
        re-importing or re-instantiating a plugin does not double its effects.

        Unlike the original, a failure here raises rather than being printed and
        swallowed: a hook that fails to register produces a feature that silently
        never runs, which is far harder to diagnose than a loud startup error.
        """
        registrations = self.hooks.setdefault(hook_name, [])

        reg_args = list(args) if isinstance(args, (list, tuple)) else ([] if args is None else [args])
        reg_kwargs = dict(kwargs) if kwargs else {}

        for _, existing_func, existing_args, existing_kwargs in registrations:
            if (
                _same_callback(existing_func, func)
                and existing_args == reg_args
                and existing_kwargs == reg_kwargs
            ):
                logger.debug("Hook %s already registered for %r", hook_name, func)
                return

        registrations.append((queue, func, reg_args, reg_kwargs))
        logger.debug("Registered hook %s -> %r (queue=%s)", hook_name, func, queue)

    def unregister_all(self, hook_name: str | None = None) -> None:
        """Drop registrations. Used by tests and by settings reloads."""
        if hook_name is None:
            self.hooks.clear()
        else:
            self.hooks.pop(hook_name, None)

    # -- dispatch -------------------------------------------------------
    def call(self, hook_name: str, *additional_args: Any, **additional_kwargs: Any) -> Any:
        """Fire ``hook_name``.

        Synchronous callbacks run inline in ``queue`` order, each receiving the
        SAME original arguments - they do not feed into one another. Celery-task
        callbacks are collected and dispatched as a single chain at the end.

        Returns the last synchronous callback's return value, or
        ``additional_args[0]`` unchanged when no synchronous callback ran.
        """
        registrations = self.hooks.get(hook_name)
        if not registrations:
            return additional_args[0] if additional_args else None

        sync_return_value = additional_args[0] if additional_args else None
        task_signatures = []
        task_names: list[str] = []

        for _, func, reg_args, reg_kwargs in sorted(registrations, key=lambda item: item[0]):
            final_args = list(reg_args) + list(additional_args)
            final_kwargs = {**reg_kwargs, **additional_kwargs}

            if _is_celery_task(func):
                task_signatures.append(func.si(*final_args, **final_kwargs))
                task_names.append(func.name)
            else:
                try:
                    sync_return_value = func(*final_args, **final_kwargs)
                except Exception:
                    # A failing sync hook must not be silently skipped: it is
                    # part of the caller's own request path.
                    logger.exception("Hook %s failed in %r", hook_name, func)
                    raise

        if task_signatures:
            self._dispatch_chain(hook_name, task_signatures, task_names)

        return sync_return_value

    def _dispatch_chain(self, hook_name: str, signatures: list, names: list[str]) -> None:
        """Send the collected Celery signatures as a chain and track them."""
        try:
            result = chain(*signatures).apply_async()
        except Exception:
            # Broker down: the request itself should not fail because a
            # background side effect could not be queued.
            logger.exception("Could not dispatch Celery chain for hook %s", hook_name)
            return

        self._register_chain_tasks(result, names)

    @staticmethod
    def _register_chain_tasks(result: Any, names: list[str]) -> None:
        """Record each queued task so /tasks can report on it.

        The original zipped task ids against names positionally
        (``add_task(task_id, names[x], ...)``) after de-duplicating the ids,
        which misaligns the pairing as soon as one id repeats and raises
        IndexError if the chain yields more ids than names. Pairing is done with
        ``zip`` here, which simply stops at the shorter sequence.
        """
        from archihub.api.tasks.services import add_task

        task_ids = HookHandler.get_task_ids(result)

        seen: set[str] = set()
        for task_id, task_name in zip(task_ids, names):
            if task_id in seen:
                continue
            seen.add(task_id)
            try:
                add_task(task_id, task_name, "automatic", "hook")
            except Exception:
                logger.warning("Could not record hook task %s (%s)", task_id, task_name, exc_info=True)

    @staticmethod
    def get_task_ids(result: Any) -> list[str]:
        """Walk a chain result back through its parents, oldest first."""
        ids: list[str] = []
        while result is not None:
            ids.append(result.id)
            result = getattr(result, "parent", None)
        return list(reversed(ids))


def get_hook_handler() -> HookHandler:
    """Return the process-wide hook bus."""
    return HookHandler()
