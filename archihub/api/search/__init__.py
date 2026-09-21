"""Full-text and faceted search over the resource index.

Always mounted; availability is decided per request by
``services.indexing_enabled``. The rule that shapes this domain is in ``query.py``:
**a public caller does not choose what to search.**
"""
