#!/usr/bin/env python3
"""Create the MongoDB indexes ArchiHUB's queries need.

WHY RUN THIS SEPARATELY

The database ships with no indexes beyond the automatic `_id` one, so every
lookup by username, slug, parent or task id is a full collection scan. That is
unnoticeable on a small instance and dominant on a real archive.

The indexes are pure database state - they are not tied to the application
version. Running this against an instance still on the Flask backend gives it
the same speed-up immediately, with no code change and no redeploy. There is no
reason to wait for a migration to benefit.

USAGE

    python tools/create_indexes.py                 # create (idempotent)
    python tools/create_indexes.py --dry-run       # show what would be created
    python tools/create_indexes.py --explain       # show index usage per collection
    python tools/create_indexes.py --stats         # index sizes and usage counters

Connection settings are read from the environment / `.env`, exactly as the
application reads them - so run it from the project root, or inside the backend
container:

    docker exec -it <backend-container> python tools/create_indexes.py

SAFETY

* Idempotent: an index that already exists is left alone. Safe to re-run.
* All builds use `background=True`, so no collection is write-locked. Running
  this against a live production instance is safe; on a large collection the
  build simply takes a while and queries keep working meanwhile.
* Creates indexes only. Never drops, never modifies documents.
* A unique index cannot be built over data that already contains duplicates.
  That is reported per-collection with the offending field, and the rest still
  proceed.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Allow running as `python tools/create_indexes.py` from the project root.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from archihub.core.logging import configure_logging  # noqa: E402
from archihub.infra.indexes import INDEXES, ensure_indexes  # noqa: E402
from archihub.infra.mongo import get_mongo  # noqa: E402


def cmd_create(mongo, dry_run: bool) -> int:
    if dry_run:
        print("DRY RUN - nothing will be created\n")

    result = ensure_indexes(mongo, dry_run=dry_run)

    verb = "would create" if dry_run else "created"
    print(f"\n  {verb:14} {len(result['created'])}")
    for name in result["created"]:
        print(f"      + {name}")
    print(f"  already present {len(result['existing'])}")
    print(f"  failed          {len(result['failed'])}")
    for name in result["failed"]:
        print(f"      ! {name}")

    if result["failed"]:
        print(
            "\nSome indexes could not be created. The most common cause is a unique "
            "index over a collection that already contains duplicates - see the log "
            "lines above for the collection and field. Those lookups stay unindexed "
            "until the duplicates are resolved; everything else was applied."
        )
        return 1
    return 0


def cmd_explain(mongo) -> int:
    """Show which collections are still doing collection scans."""
    collections = sorted({spec.collection for spec in INDEXES})
    print(f"{'collection':16} {'docs':>10}  indexes")
    print("-" * 70)

    for name in collections:
        try:
            count = mongo.db[name].estimated_document_count()
            indexes = [idx["name"] for idx in mongo.db[name].list_indexes()]
        except Exception as exc:
            print(f"  {name:14} {'?':>10}  (unavailable: {exc})")
            continue

        expected = {spec.name for spec in INDEXES if spec.collection == name}
        missing = expected - set(indexes)
        marker = "OK " if not missing else "!! "
        print(f"{marker}{name:14} {count:>10}  {len(indexes)} present"
              + (f", MISSING {len(missing)}: {', '.join(sorted(missing))}" if missing else ""))

    return 0


def cmd_stats(mongo) -> int:
    """Index sizes and, where available, how often each has actually been used.

    `$indexStats` usage counters reset when the server restarts, so a zero here
    means "not used since the last restart", not "never used" - useful for
    spotting an index that is being paid for and not earning it, but only over a
    meaningful uptime.
    """
    collections = sorted({spec.collection for spec in INDEXES})

    for name in collections:
        try:
            stats = list(mongo.db[name].aggregate([{"$indexStats": {}}]))
        except Exception as exc:
            print(f"{name}: unavailable ({exc})")
            continue

        print(f"\n{name}")
        for entry in sorted(stats, key=lambda s: -s.get("accesses", {}).get("ops", 0)):
            ops = entry.get("accesses", {}).get("ops", 0)
            print(f"    {entry['name']:38} {ops:>10} ops since restart")

    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--dry-run", action="store_true", help="Show what would be created")
    group.add_argument("--explain", action="store_true", help="Show current index coverage")
    group.add_argument("--stats", action="store_true", help="Show index sizes and usage counters")
    args = parser.parse_args()

    configure_logging(level="INFO", json_output=False)

    try:
        mongo = get_mongo()
        mongo.ping()
    except Exception as exc:
        print(f"Cannot reach MongoDB: {exc}", file=sys.stderr)
        print(
            "Check MONGO_IP_SERVER / MONGO_DATABASE / credentials in the environment "
            "or .env, and that this is being run where the database is reachable "
            "(inside the backend container for a Docker deployment).",
            file=sys.stderr,
        )
        return 2

    print(f"Connected to database: {mongo.database_name}\n")

    if args.explain:
        return cmd_explain(mongo)
    if args.stats:
        return cmd_stats(mongo)
    return cmd_create(mongo, dry_run=args.dry_run)


if __name__ == "__main__":
    raise SystemExit(main())
