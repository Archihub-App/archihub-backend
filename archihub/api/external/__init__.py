"""The two APIs other organisations' scripts call.

`/adminApi` and `/publicApi` are authenticated with **Fernet API tokens**, not
the browser's JWT, and their consumers are outside this repository entirely — a
change here is invisible to any audit of `upgrade_front` and breaks somebody
else's integration silently. Every deviation from the legacy wire contract in
this package is therefore deliberate, narrow, and listed in the module
docstrings for the operator release notes (PLAN_FASTAPI.md section 7).
"""
