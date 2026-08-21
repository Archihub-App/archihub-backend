"""The two APIs other organisations' scripts call.

`/adminApi` and `/publicApi` are authenticated with **Fernet API tokens**, not
the browser's JWT, and their consumers live outside this repository entirely — a
change here is invisible to any audit of `upgrade_front` and breaks somebody
else's integration silently. Paths, methods and response shapes are therefore
treated as fixed, and any deviation is deliberate, narrow, and listed in the
module docstrings for the operator release notes.
"""
