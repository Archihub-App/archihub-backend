"""Elasticsearch access (raw HTTP).

Port of the connection layer of ``app/utils/IndexHandler.py``.

DELIBERATELY still raw HTTP via ``requests``, not ``elasticsearch-py``.
Adopting the real client is deferred together with the Elasticsearch 7->8 server
upgrade (PLAN_FASTAPI.md decision 3), because the two are entangled: the current
code branches on the *error body* of a response (``'error' in response`` with
``status == 404``) whereas ``elasticsearch-py`` raises ``NotFoundError`` instead,
so swapping the client silently inverts control flow in ``regenerate_index`` and
friends. That rework belongs with the version bump that motivates it.

Only the pieces ``/health/ready`` needs exist here so far. The full port of the
~14 index/document methods lands with the ``search`` domain in Phase 3.
"""

from __future__ import annotations

import logging

import requests
from requests.auth import HTTPBasicAuth

from archihub.core.settings import get_settings

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT = 30


class SearchClient:
    """Minimal Elasticsearch HTTP client.

    Unlike the legacy ``IndexHandler``, constructing this does NOT perform any
    network call or create any index. ``IndexHandler.__new__`` called
    ``start()``, which created a brand-new index whenever none existed - meaning
    a mere health check could mutate cluster state. Index bootstrapping is an
    explicit call here instead.
    """

    def __init__(self) -> None:
        settings = get_settings()
        self.base_url = settings.elastic_base_url
        self.index_prefix = settings.elastic_index_prefix
        self._auth = HTTPBasicAuth(settings.elastic_user, settings.elastic_password)
        self._verify: str | bool = settings.elastic_cert or True

    def _get(self, path: str) -> requests.Response:
        return requests.get(
            f"{self.base_url}{path}",
            auth=self._auth,
            verify=self._verify,
            timeout=DEFAULT_TIMEOUT,
        )

    def get_aliases(self) -> dict:
        return self._get("/_aliases").json()

    def cluster_health(self) -> dict:
        return self._get("/_cluster/health").json()

    def ping(self) -> None:
        """Raise if the cluster is unreachable. Used by /health/ready."""
        response = self._get("/_cluster/health")
        response.raise_for_status()


_search: SearchClient | None = None


def get_search() -> SearchClient:
    global _search
    if _search is None:
        _search = SearchClient()
    return _search


def reset_search() -> None:
    """Drop the cached client (tests)."""
    global _search
    _search = None
