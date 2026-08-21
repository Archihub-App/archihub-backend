"""Elasticsearch access (raw HTTP).

DELIBERATELY raw HTTP via ``requests``, not ``elasticsearch-py``. Adopting the
real client is deferred to the Elasticsearch 7->8 server upgrade, because the
two are entangled: this code branches on the *error body* of a response
(``'error' in response`` with ``status == 404``) where ``elasticsearch-py``
raises ``NotFoundError``, so swapping the client silently inverts control flow
in ``regenerate_index`` and its neighbours.
That rework belongs with the version bump that motivates it.

THREE RULES, all consequences of one decision - an index name is produced in
exactly one place:

* **``resolve_index`` is the only way to name an index**, and every method takes
  a *suffix*, so a call site cannot omit the instance prefix. Pasting
  ``ELASTIC_INDEX_PREFIX + '-' + slug`` together at each of some thirty call
  sites means one of them will eventually be missing the prefix, and the result
  is an index nobody reads and a clear that silently does nothing.
* **Every request goes through ``_request``**, which carries authentication, TLS
  verification and a real timeout. Repeating that block per method lets the arms
  drift apart - and a wedged cluster with no timeout holds a worker until
  Celery's own ceiling, twelve hours later.
* **Failures raise.** Returning a raw ``requests.Response`` leaves each caller to
  remember its own status check, and the one that forgets produces a partial
  index and a success message.

``bulk_index`` exists because a full reindex of a real archive is dominated by
HTTP round trips rather than by Elasticsearch.
"""

from __future__ import annotations

import json
import logging

import requests
from requests.auth import HTTPBasicAuth

from archihub.core.settings import get_settings

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT = 30

#: Reindex, delete-by-query and bulk writes are proportional to the size of the
#: archive, not to one document. They still need a ceiling - the point of a
#: timeout is that a wedged cluster fails rather than pinning a worker until
#: Celery's 12h limit - but 30s would abandon a legitimate rebuild.
REINDEX_TIMEOUT = 3600


class SearchUnavailable(Exception):
    """Elasticsearch refused the query, or could not be reached."""


def _next_version(current_index: str, alias: str) -> int:
    """The version number after ``current_index``'s.

    Indices are named ``<alias>_<n>``, so the version is the segment after the
    LAST underscore. Taking the second segment of the whole name breaks on any
    instance whose index prefix itself contains one (``my_archive-resources_3``),
    and it breaks inside a Celery task, where it surfaces only as a failed job.
    """
    suffix = current_index[len(alias) :].lstrip("_")
    try:
        return int(suffix) + 1
    except ValueError:
        logger.warning("Cannot read a version from index %r; starting a new series", current_index)
        return 1


class SearchClient:
    """Minimal Elasticsearch HTTP client.

Constructing this performs NO network call and creates NO index. Index
    bootstrapping is an explicit call, so that reaching for the client - in a
    health check, say - cannot mutate cluster state as a side effect.
    """

    def __init__(self) -> None:
        settings = get_settings()
        self.base_url = settings.elastic_base_url
        self.index_prefix = settings.elastic_index_prefix
        self._auth = HTTPBasicAuth(settings.elastic_user, settings.elastic_password)
        self._verify: str | bool = settings.elastic_cert or True

    # -- transport ------------------------------------------------------

    def _request(
        self,
        method: str,
        path: str,
        *,
        json_body: dict | None = None,
        data: str | None = None,
        headers: dict | None = None,
        timeout: int = DEFAULT_TIMEOUT,
    ) -> requests.Response:
        """One place where an HTTP call to the cluster is made.

        Every call carries authentication, TLS verification and a timeout.
        Deciding those per method lets the branches disagree - an instance with
        no certificate configured ends up on a different verification policy from
        one that has, and neither reports the difference.
        """
        return requests.request(
            method,
            f"{self.base_url}{path}",
            json=json_body,
            data=data,
            headers=headers,
            auth=self._auth,
            verify=self._verify,
            timeout=timeout,
        )

    def _get(self, path: str) -> requests.Response:
        return self._request("GET", path)

    @staticmethod
    def _reason(response: requests.Response) -> str:
        """What the cluster said, for a log line - never for a client."""
        try:
            body = response.json()
        except ValueError:
            return response.text[:300]
        error = body.get("error")
        if isinstance(error, dict):
            return error.get("reason") or error.get("type") or ""
        return str(error or "")[:300]

    def _expect_ok(self, response: requests.Response, what: str) -> dict:
        """Parse a response, raising ``SearchUnavailable`` if it failed."""
        if response.status_code >= 400:
            reason = self._reason(response)
            logger.warning("Elasticsearch refused %s: %s", what, reason)
            raise SearchUnavailable(reason or f"HTTP {response.status_code}")
        try:
            return response.json()
        except ValueError:
            return {}

    def get_aliases(self) -> dict:
        return self._get("/_aliases").json()

    def cluster_health(self) -> dict:
        return self._get("/_cluster/health").json()

    def search(self, suffix: str, query: dict) -> dict:
        """Run a query against one of this instance's indices.

        Takes the index *suffix*, like every other method here - see
        ``resolve_index``.

        A malformed query or a missing index RAISES. Returning the cluster's
        error body as an ordinary result turns a stray bracket in a search box
        into "no results", which is the one answer a user will not question.
        """
        index = self.resolve_index(suffix)
        return self._expect_ok(
            self._request("POST", f"/{index}/_search", json_body=query), f"a query on {index}"
        )

    def resolve_index(self, suffix: str) -> str:
        """The alias for one of this instance's indices.

        THE ONLY PLACE AN INDEX NAME IS BUILT. Everything below takes a *suffix*
        (``"resources"``, ``"shapes"``) and resolves it here, so no method can be
        called with a name that is missing this instance's prefix.
        """
        return f"{self.index_prefix}-{suffix}"

    # -- index lifecycle ------------------------------------------------

    def get_alias_indexes(self, suffix: str) -> dict:
        """The concrete indices behind an alias, or ``{}`` when it has none.

        Returns an empty mapping rather than the cluster's 404 error body, which
        the caller would otherwise have to recognise by inspecting
        ``response['status'] == 404``.
        """
        response = self._get(f"/_alias/{self.resolve_index(suffix)}")
        if response.status_code == 404:
            return {}
        return self._expect_ok(response, f"alias lookup for {suffix}")

    def create_index(self, name: str, *, mapping: dict | None = None, settings: dict | None = None) -> dict:
        """Create a concrete index. ``name`` is already fully qualified."""
        from archihub.infra.index_settings import SPANISH_SETTINGS
        from archihub.core.hooks import get_hook_handler

        body: dict = {"settings": settings if settings is not None else SPANISH_SETTINGS}
        if mapping:
            body["mappings"] = mapping

        # Plugins may extend the index definition (e.g. to add a vector field).
        extended = get_hook_handler().call("index_create", body)
        if extended:
            body = extended

        return self._expect_ok(self._request("PUT", f"/{name}", json_body=body), f"index create {name}")

    def delete_index(self, name: str) -> dict:
        """Delete a concrete index. A missing index is not an error."""
        response = self._request("DELETE", f"/{name}")
        if response.status_code == 404:
            return {"acknowledged": True}
        return self._expect_ok(response, f"index delete {name}")

    def add_to_alias(self, suffix: str, index: str) -> dict:
        return self._alias_action("add", suffix, index)

    def remove_from_alias(self, suffix: str, index: str) -> dict:
        return self._alias_action("remove", suffix, index)

    def _alias_action(self, action: str, suffix: str, index: str) -> dict:
        body = {"actions": [{action: {"index": index, "alias": self.resolve_index(suffix)}}]}
        return self._expect_ok(
            self._request("POST", "/_aliases", json_body=body), f"alias {action} {index}"
        )

    def reindex(self, source: str, dest: str) -> dict:
        """Copy one concrete index into another.

        ``wait_for_completion`` is left at its default (true), so the caller
        blocks until the copy finishes. This runs inside a Celery task, so the
        request that started it has long since returned.
        """
        body = {"source": {"index": source}, "dest": {"index": dest}}
        return self._expect_ok(
            self._request("POST", "/_reindex", json_body=body, timeout=REINDEX_TIMEOUT),
            f"reindex {source} -> {dest}",
        )

    def create_versioned_index(self, suffix: str, mapping: dict | None = None, version: int = 1) -> str:
        """Create ``<prefix>-<suffix>_<version>`` and point the alias at it."""
        name = f"{self.resolve_index(suffix)}_{version}"
        self.create_index(name, mapping=mapping)
        self.add_to_alias(suffix, name)
        return name

    def regenerate_index(self, suffix: str, mapping: dict) -> tuple[str, bool]:
        """Rebuild an index under a new mapping, keeping the alias stable.

        Returns ``(index name, was_created)`` - created when there was nothing
        to rebuild, updated when an existing index was copied into a new one.
        The caller reports one of two different messages for those two cases.

        Elasticsearch cannot change an existing field's mapping in place, so a
        new numbered index is created, the alias is pointed at it, the old
        contents are copied across and the old index is dropped.

        ORDER MATTERS. The alias is switched over in a single atomic
        ``_aliases`` call once the copy is complete. Adding the new index to the
        alias *before* reindexing into it leaves the alias resolving to two
        indices for the whole duration of the copy - one full, one filling up -
        and every search in that window serves duplicate hits. Here a
        search either sees the old index or the new one, never both.
        """
        existing = self.get_alias_indexes(suffix)
        alias = self.resolve_index(suffix)

        if not existing:
            # No alias: clear anything left behind under those names and start
            # from scratch. Two deletes, because a previous run may have left a
            # concrete index without its alias.
            self.delete_index(alias)
            self.delete_index(f"{alias}_1")
            name = self.create_versioned_index(suffix, mapping)
            logger.info("Created search index %s", name)
            return name, True

        if len(existing) != 1:
            # More than one index behind the alias means a previous regeneration
            # was interrupted. Refuse rather than guess which one is current:
            # creating a third leaves the operator with more to untangle, not
            # less.
            raise SearchUnavailable(
                f"Alias {alias} resolves to {len(existing)} indices; "
                "resolve this manually before regenerating"
            )

        current = next(iter(existing))
        new_name = f"{alias}_{_next_version(current, alias)}"

        self.create_index(new_name, mapping=mapping)
        self.reindex(current, new_name)
        # Atomic swap: remove and add in one action list.
        self._expect_ok(
            self._request(
                "POST",
                "/_aliases",
                json_body={
                    "actions": [
                        {"remove": {"index": current, "alias": alias}},
                        {"add": {"index": new_name, "alias": alias}},
                    ]
                },
            ),
            f"alias swap {current} -> {new_name}",
        )
        self.delete_index(current)
        logger.info("Regenerated search index %s -> %s", current, new_name)
        return new_name, False

    # -- documents ------------------------------------------------------

    def index_document(self, suffix: str, doc_id: str, document: dict) -> dict:
        """Write one document, raising if the cluster refused it."""
        return self._expect_ok(
            self._request("PUT", f"/{self.resolve_index(suffix)}/_doc/{doc_id}", json_body=document),
            f"index document {doc_id}",
        )

    def delete_document(self, suffix: str, doc_id: str) -> dict:
        """Remove one document. Already absent counts as success."""
        response = self._request("DELETE", f"/{self.resolve_index(suffix)}/_doc/{doc_id}")
        if response.status_code == 404:
            return {"result": "not_found"}
        return self._expect_ok(response, f"delete document {doc_id}")

    def delete_all_documents(self, suffix: str, query: dict | None = None) -> dict:
        """Empty an index (or the part of it a query selects)."""
        body = {"query": query if query is not None else {"match_all": {}}}
        response = self._request(
            "POST",
            f"/{self.resolve_index(suffix)}/_delete_by_query",
            json_body=body,
            timeout=REINDEX_TIMEOUT,
        )
        if response.status_code == 404:
            # Nothing to empty. Not an error: a first-ever indexing run reaches
            # here before the index exists.
            return {"deleted": 0}
        return self._expect_ok(response, f"delete_by_query on {suffix}")

    def bulk_index(self, suffix: str, documents: list[tuple[str, dict]]) -> list[tuple[str, str]]:
        """Write many documents in one round trip.

        Returns the ids that FAILED, each with the cluster's reason, rather than
        raising: a single malformed resource must not abandon the other 999 in
        the batch. The caller decides what a partial failure means, and can find
        out that one happened - which a loop discarding each response cannot.
        """
        if not documents:
            return []

        index = self.resolve_index(suffix)
        lines: list[str] = []
        for doc_id, document in documents:
            lines.append(json.dumps({"index": {"_index": index, "_id": doc_id}}))
            lines.append(json.dumps(document, default=str))
        payload = "\n".join(lines) + "\n"

        response = self._request(
            "POST",
            "/_bulk",
            data=payload,
            headers={"Content-Type": "application/x-ndjson"},
            timeout=REINDEX_TIMEOUT,
        )
        body = self._expect_ok(response, f"bulk index into {suffix}")

        if not body.get("errors"):
            return []

        failures: list[tuple[str, str]] = []
        for item in body.get("items", []):
            outcome = item.get("index") or item.get("create") or {}
            if outcome.get("status", 200) >= 400:
                reason = (outcome.get("error") or {}).get("reason", "")
                failures.append((outcome.get("_id", "?"), reason))
        return failures

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
