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

  --only users            run only cases whose name or path contains this string
  --reset                 reset+reseed through the LEGACY backend before running
  --show-equal            print passing cases too, not just failures
  --update-baseline FILE  record current responses for later comparison

AUTHENTICATION
--------------
Both stacks share ``JWT_SECRET_KEY``, so a token minted by either is accepted by
the other - which is itself a property worth checking, and is what makes an
in-place cutover possible without logging every user out. The harness logs in
once against the legacy backend and reuses that token for both, so a difference
in response bodies is never just a difference in identity.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
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
    expect_status: int | None = None

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
class Result:
    case: Case
    legacy_status: int
    next_status: int
    diff: dict | None
    error: str | None = None

    @property
    def ok(self) -> bool:
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


def load_cases(path: Path) -> list[Case]:
    """Load enabled cases.

    Entries with no ``name``, or whose name starts with ``_``, are inactive
    templates for domains not yet ported - firing those before their route
    exists would only report 404-vs-200 and bury real differences in noise.
    """
    raw = json.loads(path.read_text(encoding="utf-8"))
    entries = raw["cases"] if isinstance(raw, dict) else raw
    return [
        Case.from_dict(entry)
        for entry in entries
        if entry.get("name") and not entry["name"].startswith("_")
    ]


def report(results: list[Result], *, show_equal: bool) -> int:
    failures = [r for r in results if not r.ok]

    for result in results:
        if result.ok and not show_equal:
            continue
        marker = "PASS" if result.ok else "FAIL"
        print(f"\n[{marker}] {result.case.name}  ({result.case.method} {result.case.path})")

        if result.error:
            print(f"    error: {result.error}")
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

    total = len(results)
    print(f"\n{'=' * 60}")
    print(f"{total - len(failures)}/{total} cases identical")
    if failures:
        print("\nDiffering cases:")
        for result in failures:
            reason = result.error or (
                "status" if result.legacy_status != result.next_status else "body"
            )
            print(f"  - {result.case.name} ({reason})")
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
    args = parser.parse_args()

    cases = load_cases(args.cases)
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

    print(f"\nRunning {len(cases)} case(s)...")
    results = [harness.run_case(case) for case in cases]
    return report(results, show_equal=args.show_equal)


if __name__ == "__main__":
    raise SystemExit(main())
