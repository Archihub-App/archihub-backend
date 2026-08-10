"""Full-text and faceted search over the resource index.

Mounted only when the instance has indexing switched on — see
``core/app_factory.py``. The rule that shapes this domain is in ``query.py``:
**a public caller does not choose what to search.**
"""
