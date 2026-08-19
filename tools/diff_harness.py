#!/usr/bin/env python3
"""Compare the legacy Flask backend against the FastAPI rewrite, response by response.

WHY THIS EXISTS
---------------
``ArchiHUBTestRunner`` is the project's end-to-end suite, but a survey during
migration planning found it has real behavioural coverage for only about a
quarter of the core routes. ``records`` (24 routes), ``aiservices`` (17),
``views``, ``usertasks``, ``geosystem``, ``tasks``, ``snaps``, ``adminApi``,
``publicApi``, all 80 plugin routes and all 39 Celery tasks have no behavioural
tests at all - only route-existence checking via the ``swagger-inventory`` suite.

Porting those blind would mean discovering regressions in production. So this
harness is the regression gate for everything the runner does not cover: it
fires identical requests at both backends, pointed at the same database, and
diffs the status code and the full response body field by field.

Field-by-field matters. A test that asserts ``response.ok`` and reads two fields
passes happily while a third field silently disappears - which is exactly the
failure mode FastAPI's ``response_model`` filtering introduces, and why
PLAN_FASTAPI.md section 7 forbids enabling response models before a route has
been diffed.

USAGE
-----
Start both backends against the same MongoDB, then::

    python tools/diff_harness.py \\
        --legacy http://localhost:5000 \\
        --next   http://localhost:5001 \\
        --cases  tools/diff_cases.json

Exits non-zero if any case differs, so it can gate a phase in CI.

Options worth knowing:

  --mint-token USER       authenticate as USER without a password (see below)
  --only users            run only cases whose name or path contains this string
  --reset                 reset+reseed through the LEGACY backend before running
  --show-equal            print passing cases too, not just failures
  --update-baseline FILE  record current responses for later comparison

WHAT IT ACTUALLY NEEDS
----------------------
Two running backends pointed at the same MongoDB. **Nothing has to be installed
into this environment** - the legacy stack is queried over HTTP, never imported.
An earlier note in PLAN_FASTAPI.md concluded the harness was blocked behind a
multi-gigabyte ``torch`` install because ``import torch`` is line 1 of
``app/__init__.py``; that only matters if you import the legacy app, which this
does not do.

AUTHENTICATION
--------------
Both stacks share ``JWT_SECRET_KEY``, so a token minted by either is accepted by
the other - which is itself a property worth checking, and is what makes an
in-place cutover possible without logging every user out.

``--mint-token USERNAME`` exploits that: it signs a token locally from the
configured secret, so **no account password is needed** and no real credential
ends up on a command line or in shell history. ``--username``/``--password``
still work, logging in against the legacy backend.

Either way one token is used for both backends, so a difference in response
bodies is never just a difference in identity.

TWO KINDS OF CASE
-----------------
**Parity cases** fire at both backends and diff the result. That is the right
gate wherever the port intends to match what it replaces.

**Contract cases** (any of ``expect_status``, ``expect_absent``,
``expect_present``) assert the port directly and never query legacy. They exist
because parity is the wrong gate in two places:

  - ``aiservices`` was rewritten rather than ported. Diffing it against legacy
    reports the rewrite itself as a wall of failures, burying anything real.
  - the security fixes are exactly where legacy is **wrong**. ``filepath``
    leaving the records API, drafts in a public search, a provider's API key in
    a response - demanding parity there demands the bug back. ``expect_absent``
    states the invariant instead, and keeps stating it after legacy is deleted
    at cutover, when parity cases stop being runnable at all.

FIXTURES, AND WHY A HARDCODED id IS WORSE THAN NO CASE
------------------------------------------------------
Most interesting routes need a real identifier - a resource, a record, a view.
Writing one into the case file makes the case silently useless the moment the
database changes, and this harness has a ``--reset`` flag that changes it on
purpose: after a reseed the id is gone, both backends answer 404, the bodies
match, and the case **passes** while exercising nothing. Green, and meaningless.

So ids are discovered at run time. ``diff_cases.json`` carries a ``fixtures``
list - each one a request fired at the legacy backend plus an ``extract`` path
naming the value to pull out of the response - and cases refer to the results as
``${resource_id}``. A fixture that does not resolve does not fall back to
anything: every case referring to it is reported as **SKIPPED**, counted apart
from passes, and named in the summary. Coverage you do not have is stated rather
than implied.

EXPECTED DIFFERENCES
--------------------
Not every difference is a regression. ``diff_cases.json`` carries an
``_expected_differences`` block listing the ones that are deliberate - places
where the legacy backend returns 500 or a wrongly-200 body and the port returns
the correct code, fields removed on purpose, and one case whose result depends
on run order because the port rate-limits logins and the legacy does not. Read
that before treating a failure as news.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

try:
    import httpx
except ImportError:  # pragma: no cover
    sys.exit("httpx is required: pip install httpx")

try:
    from deepdiff import DeepDiff
except ImportError:  # pragma: no cover
    sys.exit("deepdiff is required: pip install deepdiff")


# Values that legitimately differ between two runs or two processes. Compared
# for presence and type, never for equality.
DEFAULT_VOLATILE_KEYS = (
    "_id",
    "id",
    "task_id",
    "taskId",
    "createdAt",
    "updatedAt",
    "created_at",
    "updated_at",
    "date",
    "timestamp",
    "token",
    "access_token",
    "exp",
    "iat",
    "jti",
    "run_id",
    "completed_at",
)

RESET_PATH = "/health/test-control/reset"


@dataclass
class Case:
    """One request to fire at both backends."""

    name: str
    method: str = "GET"
    path: str = "/"
    body: Any = None
    form: dict | None = None
    query: dict | None = None
    headers: dict = field(default_factory=dict)
    auth: bool = True
    # Extra dotted paths to ignore for this case only, e.g. "root['checks']['celery']".
    ignore: list[str] = field(default_factory=list)

    # -- contract cases -------------------------------------------------
    # Setting any of the three below turns this into a CONTRACT case: the port
    # is asserted against a stated expectation and the legacy backend is not
    # queried at all.
    #
    # Parity is the right gate only where the port intends to match. It is the
    # wrong gate in two places, and both need covering:
    #   - `aiservices` was rewritten rather than ported, so a diff against
    #     legacy reports the rewrite as a wall of failures and hides anything
    #     real inside it.
    #   - the security fixes are precisely where legacy is WRONG. `filepath`
    #     leaving the records API, drafts in a public search, a provider's API
    #     key in a response - a parity assertion there would demand the bug
    #     back. `expect_absent` states the invariant directly instead.
    expect_status: int | None = None
    #: Key names that must appear nowhere in the response, at any depth.
    expect_absent: list[str] = field(default_factory=list)
    #: Key names that must appear somewhere in the response.
    expect_present: list[str] = field(default_factory=list)

    @property
    def is_contract(self) -> bool:
        return bool(self.expect_status or self.expect_absent or self.expect_present)

    @classmethod
    def from_dict(cls, raw: dict) -> Case:
        # Keys starting with "_" are free-form comments (JSON has none of its
        # own). Everything else must be a real field, so a typo like "quey"
        # fails loudly instead of being silently dropped.
        payload = {key: value for key, value in raw.items() if not key.startswith("_")}
        unknown = set(payload) - set(cls.__dataclass_fields__)
        if unknown:
            raise ValueError(f"Unknown keys in case {raw.get('name')!r}: {sorted(unknown)}")
        return cls(**payload)


@dataclass
class Fixture:
    """A value discovered from the running instance, not written into the file.

    Fired at the legacy backend by default - both stacks share one database, so
    the id it returns is equally valid against the port, and reading it from the
    stack being *replaced* keeps the port out of its own test setup.

    ``source: "next"`` fires it at the PORT instead. That is not a shortcut, it
    is the only thing that works for a domain that was **rewritten rather than
    ported**: `aiservices` answers `GET /providers` with a bare array of vendor
    NAMES on legacy and a list of provider objects on the port, so no `extract`
    can read an id out of both. A rewritten domain has no parity cases by
    definition - diffing it would report the rewrite as a wall of failures - so
    its fixtures feed contract cases only, and a contract case never queries
    legacy either. `_assert_next_fixtures_feed_contract_cases` enforces that,
    because a parity case fed from the port would take one side's answer as the
    question.
    """

    name: str
    path: str
    extract: str
    method: str = "GET"
    body: Any = None
    query: dict | None = None
    auth: bool = True
    #: "legacy" (default) or "next". See the note above before choosing "next".
    source: str = "legacy"
    #: What is untestable without it, printed when it fails to resolve.
    covers: str = ""

    @classmethod
    def from_dict(cls, raw: dict) -> Fixture:
        payload = {key: value for key, value in raw.items() if not key.startswith("_")}
        unknown = set(payload) - set(cls.__dataclass_fields__)
        if unknown:
            raise ValueError(f"Unknown keys in fixture {raw.get('name')!r}: {sorted(unknown)}")
        return cls(**payload)


def extract_path(payload: Any, path: str) -> Any:
    """Pull ``a.0.b`` out of a parsed response, or return None.

    A ``[]`` segment maps the rest of the path over a list instead of indexing
    into it, so ``[].slug`` binds EVERY slug rather than one. That exists for
    the same reason the fixtures themselves do: a case that names one content
    type is pinned to one instance's seed data, and when that type is absent
    the case does not fail loudly - the request 500s on legacy, the fixture
    reading it goes unresolved, and every case depending on it is skipped.
    Binding the whole set asks the instance what it has.

    An empty result is None, not ``[]``. A bound empty list would make its
    cases run against nothing and pass, which is the outcome the skip
    accounting exists to prevent.

    Deliberately total: a fixture that cannot be resolved is an ordinary
    outcome on an instance without that kind of data, and it is reported as a
    skip rather than crashing the run.
    """
    current = payload
    segments = path.split(".")
    for position, segment in enumerate(segments):
        if segment == "[]":
            if not isinstance(current, list):
                return None
            rest = ".".join(segments[position + 1 :])
            collected = [
                item
                for item in (extract_path(entry, rest) if rest else entry for entry in current)
                if item is not None
            ]
            return collected or None
        if isinstance(current, list):
            if not segment.isdigit() or int(segment) >= len(current):
                return None
            current = current[int(segment)]
        elif isinstance(current, dict):
            if segment not in current:
                return None
            current = current[segment]
        else:
            return None
    return current


#: ``${name}`` rather than ``{name}``: a request body may legitimately contain
#: braces, and an unresolvable token must be an error rather than a guess.
PLACEHOLDER = re.compile(r"\$\{([a-zA-Z0-9_]+)\}")


def placeholders_in(value: Any) -> set[str]:
    if isinstance(value, str):
        return set(PLACEHOLDER.findall(value))
    if isinstance(value, dict):
        return set().union(*(placeholders_in(v) for v in value.values())) if value else set()
    if isinstance(value, list):
        return set().union(*(placeholders_in(v) for v in value)) if value else set()
    return set()


def keys_in(value: Any) -> set[str]:
    """Every key name appearing anywhere in a parsed body, at any depth.

    Depth matters for what this is used for: ``filepath`` nested three levels
    inside a record's parent list has still left the API.
    """
    found: set[str] = set()
    if isinstance(value, dict):
        for key, inner in value.items():
            found.add(key)
            found |= keys_in(inner)
    elif isinstance(value, list):
        for item in value:
            found |= keys_in(item)
    return found


def substitute(value: Any, bindings: dict[str, Any]) -> Any:
    if isinstance(value, str):
        # A lone "${x}" keeps the bound value's type - an int stays an int in a
        # JSON body - while text around it forces a string, as in a URL path.
        whole = PLACEHOLDER.fullmatch(value)
        if whole:
            return bindings[whole.group(1)]
        return PLACEHOLDER.sub(lambda m: str(bindings[m.group(1)]), value)
    if isinstance(value, dict):
        return {key: substitute(item, bindings) for key, item in value.items()}
    if isinstance(value, list):
        return [substitute(item, bindings) for item in value]
    return value


@dataclass
class Result:
    case: Case
    legacy_status: int
    next_status: int
    diff: dict | None
    error: str | None = None
    #: Set when the case never ran, e.g. an unresolved fixture. Never a pass.
    skipped: str | None = None

    @property
    def ok(self) -> bool:
        if self.skipped:
            return False
        return self.error is None and self.legacy_status == self.next_status and not self.diff


def _normalise(value: Any, volatile_keys: tuple[str, ...]) -> Any:
    """Replace volatile values with a type marker so they compare equal.

    A differing ``_id`` between two runs is noise; an ``_id`` that is a string on
    one backend and an object on the other is a real regression - so the type is
    preserved while the value is not.
    """
    if isinstance(value, dict):
        return {
            key: (f"<{type(inner).__name__}>" if key in volatile_keys else _normalise(inner, volatile_keys))
            for key, inner in value.items()
        }
    if isinstance(value, list):
        return [_normalise(item, volatile_keys) for item in value]
    return value


def _parse_body(response: httpx.Response) -> Any:
    content_type = response.headers.get("content-type", "")
    if "application/json" in content_type:
        try:
            return response.json()
        except ValueError:
            return {"__unparseable_json__": response.text[:500]}
    if content_type.startswith(("image/", "video/", "audio/", "application/octet-stream")):
        # Binary payloads: compare the type and size, not the bytes.
        return {"__binary__": content_type, "__bytes__": len(response.content)}
    return {"__text__": response.text[:2000]}


class DiffHarness:
    def __init__(
        self,
        legacy_url: str,
        next_url: str,
        *,
        timeout: float = 60.0,
        volatile_keys: tuple[str, ...] = DEFAULT_VOLATILE_KEYS,
        test_secret: str | None = None,
    ) -> None:
        self.legacy_url = legacy_url.rstrip("/")
        self.next_url = next_url.rstrip("/")
        self.volatile_keys = volatile_keys
        self.test_secret = test_secret
        self.client = httpx.Client(timeout=timeout, follow_redirects=False)
        self.token: str | None = None
        self.bindings: dict[str, Any] = {}

    # -- setup ----------------------------------------------------------
    def use_token(self, token: str) -> None:
        """Use a token minted elsewhere, against both backends.

        Both stacks read the same ``JWT_SECRET_KEY``, so a token signed by
        either is accepted by both - the property that makes an in-place cutover
        possible without logging every user out. That means the harness does not
        actually need an account password to exercise authenticated routes: a
        token minted directly from the configured secret does the job, and
        avoids putting a real credential on a command line.
        """
        self.token = token

    def login(self, username: str, password: str) -> None:
        """Mint a token on the legacy backend and use it against both."""
        response = self.client.post(
            f"{self.legacy_url}/auth/login", json={"username": username, "password": password}
        )
        if response.status_code != 200:
            raise SystemExit(
                f"Login failed against legacy backend: {response.status_code} {response.text[:300]}"
            )
        payload = response.json()
        token = payload.get("access_token") or payload.get("token")
        if not token:
            raise SystemExit(f"No token in login response: {sorted(payload)}")
        self.token = token

    def reset(self) -> None:
        """Reset and reseed through the legacy backend.

        Deliberately always the legacy side: both backends share one database,
        and the FastAPI reset flow is not ported until Phase 3/4 (it depends on
        the `system` domain's seeding path). Resetting through the stack that
        actually implements it keeps the comparison honest.
        """
        if not self.test_secret:
            raise SystemExit("--reset needs --test-secret (X-ArchiHUB-Test-Secret)")

        response = self.client.post(
            f"{self.legacy_url}{RESET_PATH}",
            headers={"X-ArchiHUB-Test-Secret": self.test_secret},
        )
        if response.status_code == 404:
            raise SystemExit(
                "Reset returned 404: ARCHIHUB_TEST_MODE is not reaching the legacy container."
            )
        if response.status_code == 403:
            raise SystemExit(
                "Reset returned 403: the 'test_mode_active' marker document is missing from "
                "the `system` collection. Insert it by hand - the app never creates it."
            )
        if response.status_code not in (200, 202):
            raise SystemExit(f"Reset failed: {response.status_code} {response.text[:300]}")
        print(f"  reset accepted ({response.status_code})")

    # -- fixtures -------------------------------------------------------
    def resolve_fixtures(self, fixtures: list[Fixture]) -> list[tuple[Fixture, str]]:
        """Bind what the instance actually has. Returns the ones that failed.

        Each is fired at the legacy backend only. Anything already bound - by
        ``--bind`` on the command line - is left alone, so an operator can pin a
        specific document without editing the case file.

        A fixture's own request is substituted against the bindings resolved
        BEFORE it, so one fixture can be expressed in terms of another - the
        resource listing has to name content types, and which types exist is
        itself a question for the instance. Fixtures are resolved in file
        order, so a fixture may only refer to one declared above it; a forward
        reference leaves the placeholder unresolved and is reported as such
        rather than being sent as the literal text ``${name}``, which reaches
        the backend as a nonexistent value and comes back as somebody else's
        error.
        """
        unresolved: list[tuple[Fixture, str]] = []

        for fixture in fixtures:
            if fixture.name in self.bindings:
                print(f"  {fixture.name} = {self.bindings[fixture.name]} (given)")
                continue

            headers = {}
            if fixture.auth and self.token:
                headers["Authorization"] = f"Bearer {self.token}"

            needed = (
                placeholders_in(fixture.path)
                | placeholders_in(fixture.body)
                | placeholders_in(fixture.query)
            )
            missing = sorted(name for name in needed if name not in self.bindings)
            if missing:
                unresolved.append(
                    (fixture, f"depends on unresolved fixture(s): {', '.join(missing)}")
                )
                continue

            if fixture.source not in ("legacy", "next"):
                raise SystemExit(
                    f"Fixture {fixture.name!r}: source must be 'legacy' or 'next', "
                    f"not {fixture.source!r}"
                )
            base = self.legacy_url if fixture.source == "legacy" else self.next_url

            try:
                response = self.client.request(
                    fixture.method.upper(),
                    f"{base}{substitute(fixture.path, self.bindings)}",
                    headers=headers,
                    params=substitute(fixture.query, self.bindings),
                    json=substitute(fixture.body, self.bindings),
                )
            except httpx.RequestError as exc:
                unresolved.append((fixture, f"request failed: {exc}"))
                continue

            if response.status_code != 200:
                unresolved.append((fixture, f"{fixture.method} {fixture.path} -> {response.status_code}"))
                continue

            try:
                payload = response.json()
            except ValueError:
                unresolved.append((fixture, "response was not JSON"))
                continue

            value = extract_path(payload, fixture.extract)
            if value is None or value == "":
                unresolved.append((fixture, f"no {fixture.extract!r} in the response - the instance has none"))
                continue

            self.bindings[fixture.name] = value
            marker = "" if fixture.source == "legacy" else "  (from the port)"
            print(f"  {fixture.name} = {value}{marker}")

        return unresolved

    # -- execution ------------------------------------------------------
    def _request(self, base_url: str, case: Case) -> httpx.Response:
        headers = dict(case.headers)
        if case.auth and self.token:
            headers.setdefault("Authorization", f"Bearer {self.token}")

        kwargs: dict[str, Any] = {"headers": headers, "params": case.query}
        if case.form is not None:
            kwargs["data"] = case.form
        elif case.body is not None:
            kwargs["json"] = case.body

        return self.client.request(case.method.upper(), f"{base_url}{case.path}", **kwargs)

    def run_case(self, case: Case) -> Result:
        needed = placeholders_in([case.path, case.body, case.form, case.query])
        missing = sorted(needed - set(self.bindings))
        if missing:
            # Firing it anyway would put a literal "${resource_id}" in the URL,
            # 404 on both backends and report a pass. Say nothing rather than
            # something false.
            return Result(case, 0, 0, None, skipped=f"unresolved fixture(s): {', '.join(missing)}")

        if needed:
            case = replace(
                case,
                path=substitute(case.path, self.bindings),
                body=substitute(case.body, self.bindings),
                form=substitute(case.form, self.bindings),
                query=substitute(case.query, self.bindings),
            )

        if case.is_contract:
            return self._run_contract(case)

        try:
            legacy = self._request(self.legacy_url, case)
            nxt = self._request(self.next_url, case)
        except httpx.RequestError as exc:
            return Result(case, 0, 0, None, error=f"request failed: {exc}")

        legacy_body = _normalise(_parse_body(legacy), self.volatile_keys)
        next_body = _normalise(_parse_body(nxt), self.volatile_keys)

        diff = DeepDiff(
            legacy_body,
            next_body,
            ignore_order=True,
            exclude_paths=case.ignore or None,
            verbose_level=2,
        )
        return Result(case, legacy.status_code, nxt.status_code, diff.to_dict() or None)

    def _run_contract(self, case: Case) -> Result:
        """Assert the port against a stated expectation, ignoring legacy."""
        try:
            response = self._request(self.next_url, case)
        except httpx.RequestError as exc:
            return Result(case, 0, 0, None, error=f"request failed: {exc}")

        body = _parse_body(response)
        problems: list[str] = []

        if case.expect_status and response.status_code != case.expect_status:
            problems.append(f"expected status {case.expect_status}, got {response.status_code}")

        present = keys_in(body)
        for key in case.expect_absent:
            if key in present:
                problems.append(f"{key!r} must not appear in this response, and does")
        for key in case.expect_present:
            if key not in present:
                problems.append(f"{key!r} must appear in this response, and does not")

        return Result(
            case,
            case.expect_status or response.status_code,
            response.status_code,
            None,
            error="; ".join(problems) or None,
        )


def load_cases(path: Path) -> tuple[list[Case], list[Fixture]]:
    """Load enabled cases and the fixtures they draw identifiers from.

    Entries with no ``name``, or whose name starts with ``_``, are inactive
    templates for domains not yet ported - firing those before their route
    exists would only report 404-vs-200 and bury real differences in noise.
    """
    raw = json.loads(path.read_text(encoding="utf-8"))
    entries = raw["cases"] if isinstance(raw, dict) else raw
    cases = [
        Case.from_dict(entry)
        for entry in entries
        if entry.get("name") and not entry["name"].startswith("_")
    ]
    fixtures = [
        Fixture.from_dict(entry)
        for entry in (raw.get("fixtures") or [] if isinstance(raw, dict) else [])
        if entry.get("name") and not entry["name"].startswith("_")
    ]
    _assert_next_fixtures_feed_contract_cases(cases, fixtures)
    return cases, fixtures


def _assert_next_fixtures_feed_contract_cases(
    cases: list[Case], fixtures: list[Fixture]
) -> None:
    """A fixture read from the PORT may only feed cases that assert the port.

    A parity case exists to ask whether the two stacks agree. Seeding it with a
    value the port chose makes one side supply the question as well as half the
    answer - the case would then pass on a port that returns a *consistent*
    wrong id, which is exactly the failure it is there to catch. Contract cases
    never query legacy, so the same value is unobjectionable there.

    Checked at load time rather than left as a comment because `source: "next"`
    reads as a convenience and will be reached for the moment a fixture is
    awkward to resolve.
    """
    from_next = {f.name for f in fixtures if f.source == "next"}
    if not from_next:
        return

    offenders = []
    for case in cases:
        if case.is_contract:
            continue
        used = (
            placeholders_in(case.path)
            | placeholders_in(case.body)
            | placeholders_in(case.query)
            | placeholders_in(case.form)
        )
        borrowed = sorted(used & from_next)
        if borrowed:
            offenders.append(f"  {case.name!r} uses {', '.join(borrowed)}")

    if offenders:
        raise SystemExit(
            "Parity cases cannot use a fixture resolved from the port - they would\n"
            "be asking the port to supply the question as well as the answer:\n"
            + "\n".join(offenders)
        )


def report(results: list[Result], *, show_equal: bool) -> int:
    skipped = [r for r in results if r.skipped]
    failures = [r for r in results if not r.ok and not r.skipped]

    for result in results:
        if result.skipped:
            continue
        if result.ok and not show_equal:
            continue
        marker = "PASS" if result.ok else "FAIL"
        kind = "contract" if result.case.is_contract else "parity"
        print(f"\n[{marker}] {result.case.name}  ({result.case.method} {result.case.path}) [{kind}]")

        if result.error:
            print(f"    {result.error}")
            continue

        if result.case.is_contract:
            print(f"    status: {result.next_status} (as required)")
            continue

        if result.legacy_status != result.next_status:
            print(f"    status: legacy={result.legacy_status}  next={result.next_status}")
        else:
            print(f"    status: {result.legacy_status} (match)")

        if result.diff:
            rendered = json.dumps(result.diff, indent=6, default=str)
            if len(rendered) > 4000:
                rendered = rendered[:4000] + "\n      ... (truncated)"
            print(f"    body diff (legacy -> next):\n{rendered}")

    ran = [r for r in results if not r.skipped]
    parity = [r for r in ran if not r.case.is_contract]
    contract = [r for r in ran if r.case.is_contract]

    print(f"\n{'=' * 60}")
    print(f"{sum(r.ok for r in parity)}/{len(parity)} parity cases identical to legacy")
    print(f"{sum(r.ok for r in contract)}/{len(contract)} contract cases met (port asserted directly)")
    if skipped:
        print(f"{len(skipped)} skipped")

    if failures:
        print("\nDiffering cases:")
        for result in failures:
            reason = result.error or (
                "status" if result.legacy_status != result.next_status else "body"
            )
            print(f"  - {result.case.name} ({reason})")

    if skipped:
        # Named rather than merely counted: a skip is missing coverage, and the
        # reason usually says exactly what the instance would need to have.
        print("\nSkipped (NOT passes - these routes went untested):")
        for result in skipped:
            print(f"  - {result.case.name}: {result.skipped}")

    return 1 if failures else 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--legacy", default="http://localhost:5000", help="Flask backend base URL")
    parser.add_argument("--next", dest="next_url", default="http://localhost:5001", help="FastAPI backend base URL")
    parser.add_argument("--cases", type=Path, default=Path(__file__).parent / "diff_cases.json")
    parser.add_argument("--only", help="Run only cases whose name or path contains this substring")
    parser.add_argument("--username", help="Username to log in with (enables authenticated cases)")
    parser.add_argument("--password", help="Password to log in with")
    parser.add_argument(
        "--token",
        help="Use this JWT instead of logging in. Both stacks share JWT_SECRET_KEY, "
             "so a locally minted token works against both and no password is needed.",
    )
    parser.add_argument(
        "--mint-token",
        metavar="USERNAME",
        help="Mint a token for USERNAME from the configured JWT_SECRET_KEY and use it.",
    )
    parser.add_argument("--test-secret", help="X-ArchiHUB-Test-Secret, needed by --reset")
    parser.add_argument("--reset", action="store_true", help="Reset+reseed via the legacy backend first")
    parser.add_argument("--show-equal", action="store_true", help="Also print passing cases")
    parser.add_argument(
        "--bind",
        action="append",
        metavar="NAME=VALUE",
        default=[],
        help="Pin a fixture instead of discovering it, e.g. --bind resource_id=6a70b833...",
    )
    parser.add_argument(
        "--strict-fixtures",
        action="store_true",
        help="Fail the run if any fixture does not resolve, instead of skipping its cases",
    )
    args = parser.parse_args()

    cases, fixtures = load_cases(args.cases)
    if args.only:
        needle = args.only.lower()
        cases = [c for c in cases if needle in c.name.lower() or needle in c.path.lower()]
    if not cases:
        print("No cases selected.")
        return 0

    harness = DiffHarness(args.legacy, args.next_url, test_secret=args.test_secret)

    print(f"legacy : {harness.legacy_url}")
    print(f"next   : {harness.next_url}")

    if args.reset:
        print("\nResetting instance via legacy backend...")
        harness.reset()

    if args.mint_token:
        from archihub.core.security.tokens import create_access_token

        harness.use_token(create_access_token(args.mint_token))
        print(f"  authenticated as {args.mint_token} (token minted from JWT_SECRET_KEY)")
    elif args.token:
        harness.use_token(args.token)
        print("  authenticated (token supplied)")
    elif args.username and args.password:
        harness.login(args.username, args.password)
        print("  authenticated (token minted on legacy, used against both)")
    elif any(c.auth for c in cases):
        print("  no credentials given - authenticated cases will run unauthenticated")

    for binding in args.bind:
        name, _, value = binding.partition("=")
        if not value:
            raise SystemExit(f"--bind expects NAME=VALUE, got {binding!r}")
        harness.bindings[name] = value

    # Only resolve what the selected cases actually refer to: --only records
    # should not fail on a view that this instance happens not to have.
    wanted = set().union(*(placeholders_in([c.path, c.body, c.form, c.query]) for c in cases))
    needed = [f for f in fixtures if f.name in wanted]

    if needed:
        print(f"\nResolving {len(needed)} fixture(s) against the legacy backend...")
        unresolved = harness.resolve_fixtures(needed)
        for fixture, reason in unresolved:
            print(f"  {fixture.name}: UNRESOLVED - {reason}")
            if fixture.covers:
                print(f"      leaves untested: {fixture.covers}")
        if unresolved and args.strict_fixtures:
            return 2

    print(f"\nRunning {len(cases)} case(s)...")
    results = [harness.run_case(case) for case in cases]
    return report(results, show_equal=args.show_equal)


if __name__ == "__main__":
    raise SystemExit(main())
