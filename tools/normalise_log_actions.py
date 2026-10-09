"""Store every audit entry's action under its canonical value.

THE INVARIANT ---------------
Every `action` in the `logs` collection is a VALUE of
`archihub.core.log_actions.log_actions` (`"USER_UPDATE"`), never a key
(`"user_update"`). The audit screen filters by exact match on the value, so an
entry stored under the key never appears in its results. The activity feed
reads both spellings and is unaffected.

An entry ends up under the key when its action was written before the action
was added to the vocabulary: `normalize_action` stores an unknown action
verbatim. This rewrites those entries to the value. It touches only the
`action` field, and only for keys the vocabulary now names.

USAGE
-----
Dry run first; it changes nothing and prints what it would do::

    cd development
    PYTHONPATH=. python tools/normalise_log_actions.py

Then::

    PYTHONPATH=. python tools/normalise_log_actions.py --apply

Idempotent: once rewritten, an entry no longer matches, so re-running reports
nothing to do.
"""

from __future__ import annotations

import argparse
import sys


def plan(mongo, vocabulary: dict[str, str]) -> list[tuple[str, str, int]]:
    """``(stored key, canonical value, entries)`` for every key in use."""
    pending = []
    for key, value in sorted(vocabulary.items()):
        if key == value:
            continue
        count = mongo.count("logs", {"action": key})
        if count:
            pending.append((key, value, count))
    return pending


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument(
        "--apply", action="store_true", help="write the changes (default: dry run)"
    )
    args = parser.parse_args()

    from archihub.core.log_actions import log_actions
    from archihub.infra.mongo import get_mongo

    mongo = get_mongo()
    pending = plan(mongo, log_actions)

    if not pending:
        print("Nothing to do: every audit entry already uses the canonical action.")
        return 0

    for key, value, count in pending:
        print(f"  {count:>7} entr{'y' if count == 1 else 'ies'}  {key} -> {value}")
        if args.apply:
            mongo.update_records("logs", {"action": key}, {"action": value})

    total = sum(count for _key, _value, count in pending)
    print()
    if args.apply:
        print(f"Rewrote {total} entr{'y' if total == 1 else 'ies'}.")
    else:
        print(f"{total} entr{'y' if total == 1 else 'ies'} would change. Re-run with --apply.")
        print("Nothing has been modified.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
