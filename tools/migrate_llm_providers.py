"""Bring pre-rewrite `llm_models` rows up to the shape the port expects.

WHY THIS EXISTS
---------------
The legacy aiservices module described a provider by a **vendor name** - one of a
fixed set hardcoded in the source - plus an optional `endpoint`:

    {"name": "OpenAI", "provider": "OpenAI", "key": "...", "endpoint": ""}

The rewrite splits that into two independent things (see the aiservices section
of CLAUDE.md): a *dialect* is a wire protocol and lives in code, a *provider* is
an endpoint that speaks one and is a row here:

    {"name": "OpenAI", "dialect": "openai-compatible",
     "base_url": "https://api.openai.com/v1", "key": "...", "enabled": true}

A row still carrying the old shape reads back with `dialect: null`, which means
the provider cannot be called at all and the configuration screen shows it with
an empty protocol. Nothing in the application rewrites these automatically -
guessing a vendor's protocol during a request would be a silent, unauditable
data change - so it happens here, once, deliberately, and visibly.

The credential is NEVER touched. It stays encrypted under the same `FERNET_KEY`
and is not read, decrypted, logged or re-encrypted by this script.

USAGE
-----
Dry run first; it changes nothing and prints exactly what it would do::

    cd development
    PYTHONPATH=. python tools/migrate_llm_providers.py

Then, having read that output::

    PYTHONPATH=. python tools/migrate_llm_providers.py --apply

Idempotent: a row that already has a `dialect` is left alone, so re-running is
safe and reports "already migrated".
"""

from __future__ import annotations

import argparse
import sys

# The legacy vendor vocabulary, mapped to the protocol each vendor actually
# speaks. This table is the whole judgement call in the script, which is why it
# is small, explicit, and printed rather than applied silently.
#
# `base_url` is left to the dialect's own default unless the legacy row carried
# an explicit `endpoint`, which always wins - an operator who pointed a provider
# at a private deployment meant it.
VENDOR_TO_DIALECT = {
    "openai": "openai-compatible",
    "azure": "openai-compatible",
    "azureopenai": "openai-compatible",
    "ollama": "ollama",
    "google": "google",
    "gemini": "google",
    "anthropic": "anthropic",
    "claude": "anthropic",
}


def plan_for(row: dict) -> tuple[dict | None, str]:
    """The `$set` this row needs, and a human-readable reason."""
    from archihub.api.aiservices.dialects import DIALECTS

    name = row.get("name") or "(unnamed)"

    if row.get("dialect"):
        return None, f"{name}: already migrated (dialect={row['dialect']!r})"

    vendor = (row.get("provider") or "").strip().lower()
    if not vendor:
        return None, f"{name}: SKIPPED - no legacy `provider` field to migrate from"

    dialect = VENDOR_TO_DIALECT.get(vendor)
    if dialect is None:
        return None, (
            f"{name}: SKIPPED - unknown legacy vendor {row.get('provider')!r}. "
            f"Set its dialect by hand; known vendors are {sorted(VENDOR_TO_DIALECT)}."
        )

    adapter = DIALECTS[dialect]
    endpoint = (row.get("endpoint") or "").strip()
    base_url = endpoint or getattr(adapter, "default_base_url", None)

    update = {"dialect": dialect, "enabled": row.get("enabled", True)}
    if base_url:
        update["base_url"] = base_url

    source = "its own endpoint" if endpoint else "the dialect default"
    return update, f"{name}: {row.get('provider')!r} -> dialect={dialect!r}, base_url={base_url!r} ({source})"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply", action="store_true",
        help="actually write. Without it the script only reports what it would do.",
    )
    args = parser.parse_args()

    from archihub.infra.mongo import get_mongo

    mongo = get_mongo()
    rows = list(mongo.get_all_records("llm_models", {}))
    if not rows:
        print("No providers configured; nothing to do.")
        return 0

    print(f"{len(rows)} provider row(s) in `llm_models`.\n")

    planned = 0
    for row in rows:
        update, reason = plan_for(row)
        print(f"  {reason}")
        if update is None:
            continue
        planned += 1
        if args.apply:
            mongo.update_record("llm_models", {"_id": row["_id"]}, update)

    print()
    if not planned:
        print("Nothing to migrate.")
    elif args.apply:
        print(f"Applied to {planned} row(s). Credentials were not read or modified.")
    else:
        print(f"{planned} row(s) would change. Re-run with --apply to write them.")
        print("Nothing has been modified.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
