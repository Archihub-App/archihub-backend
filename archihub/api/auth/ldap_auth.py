"""LDAP directory authentication.

Enabled only when ``LDAP_HOST`` is configured; otherwise every function here is
inert and login falls through to the local password check.

INPUT HANDLING INVARIANT - the important part of this module:

A username arrives from an unauthenticated request body and is used to build two
LDAP constructs, a **distinguished name** and a **search filter**. Neither is a
string with no syntax: both have metacharacters (``*``, ``(``, ``)``, ``\\``,
NUL, ``,``, ``+``, ``"``, ``<``, ``>``, ``;``, ``=``) that change what the
expression means. Interpolating raw input into either is the LDAP equivalent of
building SQL by concatenation.

So every value that reaches a DN goes through :func:`escape_dn_chars` and every
value that reaches a filter goes through :func:`escape_filter_chars`, both from
``python-ldap``. **Do not build a DN or a filter with an f-string.** If a new
lookup is added here, escape its inputs the same way.

Also note the package: this imports ``ldap`` (python-ldap). ``ldap3`` is a
different library with a different API and is not interchangeable.
"""

from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)

LDAP_HOST = os.environ.get("LDAP_HOST") or None
LDAP_BASE_DN = os.environ.get("LDAP_BASE_DN", "dc=example,dc=com")
LDAP_USER_DN = os.environ.get("LDAP_USER_DN", "ou=users")
LDAP_GROUP_DN = os.environ.get("LDAP_GROUP_DN", "ou=groups")
LDAP_TLS_CACERTFILE = os.environ.get("LDAP_TLS_CACERTFILE") or None
LDAP_TLS_REQUIRE_CERT = os.environ.get("LDAP_TLS_REQUIRE_CERT", "demand")

# Timeouts: an unresponsive directory must not hold a request thread open.
LDAP_NETWORK_TIMEOUT = float(os.environ.get("LDAP_NETWORK_TIMEOUT", 10))
LDAP_TIMEOUT = float(os.environ.get("LDAP_TIMEOUT", 15))


def is_enabled() -> bool:
    return bool(LDAP_HOST)


def _import_ldap():
    """Import python-ldap, or explain clearly why it is missing."""
    try:
        import ldap  # noqa: PLC0415

        return ldap
    except ImportError as exc:
        raise RuntimeError(
            "LDAP login is configured (LDAP_HOST is set) but python-ldap is not "
            "installed. Note this is 'python-ldap', which also needs the system "
            "packages libldap2-dev and libsasl2-dev - not 'ldap3', which is a "
            "different library with a different API."
        ) from exc


def _configure_tls(client, ldap) -> None:
    """Apply TLS options for an ldaps:// connection."""
    if not (LDAP_HOST or "").lower().startswith("ldaps://"):
        return

    if LDAP_TLS_CACERTFILE:
        client.set_option(ldap.OPT_X_TLS_CACERTFILE, LDAP_TLS_CACERTFILE)

    cert_options = {
        "never": ldap.OPT_X_TLS_NEVER,
        "allow": ldap.OPT_X_TLS_ALLOW,
        "try": ldap.OPT_X_TLS_TRY,
        "demand": ldap.OPT_X_TLS_DEMAND,
    }
    requested = (LDAP_TLS_REQUIRE_CERT or "demand").lower()
    if requested not in cert_options:
        logger.warning(
            "Unrecognised LDAP_TLS_REQUIRE_CERT %r; falling back to 'demand'", LDAP_TLS_REQUIRE_CERT
        )
    # Default to the strictest option, never the most permissive: an
    # unrecognised value must not silently disable certificate verification.
    client.set_option(ldap.OPT_X_TLS_REQUIRE_CERT, cert_options.get(requested, ldap.OPT_X_TLS_DEMAND))
    client.set_option(ldap.OPT_X_TLS_NEWCTX, 0)


def build_user_dn(username: str) -> str:
    """Build the bind DN for ``username``, escaping it as a DN component."""
    ldap = _import_ldap()
    from ldap.dn import escape_dn_chars

    return f"uid={escape_dn_chars(username)},{LDAP_USER_DN},{LDAP_BASE_DN}"


def build_user_filter(username: str) -> str:
    """Build the search filter for ``username``, escaping it as filter data."""
    _import_ldap()
    from ldap.filter import escape_filter_chars

    return f"(uid={escape_filter_chars(username)})"


def authenticate(username: str, password: str) -> dict | None:
    """Bind as ``username`` and return their directory attributes, or None.

    An empty password is refused before contacting the directory: many servers
    treat a zero-length password as an *anonymous* bind and return success,
    which would otherwise read as a valid login.
    """
    if not is_enabled():
        return None
    if not username or not password:
        return None

    ldap = _import_ldap()
    client = None

    try:
        client = ldap.initialize(LDAP_HOST)
        client.set_option(ldap.OPT_NETWORK_TIMEOUT, LDAP_NETWORK_TIMEOUT)
        client.set_option(ldap.OPT_TIMEOUT, LDAP_TIMEOUT)
        _configure_tls(client, ldap)
        client.set_option(ldap.OPT_REFERRALS, 0)

        client.simple_bind_s(build_user_dn(username), password)

        result = client.search_s(
            LDAP_BASE_DN, ldap.SCOPE_SUBTREE, build_user_filter(username), ["cn", "mail"]
        )
        if not result:
            return None

        attributes = result[0][1]
        return {
            "mail": _first_value(attributes.get("mail")),
            "cn": _first_value(attributes.get("cn")),
        }
    except ldap.INVALID_CREDENTIALS:
        return None
    except Exception:
        # Directory faults are logged server-side only. The caller reports the
        # same generic failure it reports for a wrong password, so a
        # misconfigured or unreachable directory does not become a way to
        # distinguish real accounts from invented ones.
        logger.warning("LDAP authentication error", exc_info=True)
        return None
    finally:
        if client is not None:
            try:
                client.unbind()
            except Exception:
                pass


def _first_value(values) -> str:
    """LDAP attributes are lists of bytes; take the first, decoded."""
    if not values:
        return ""
    value = values[0]
    return value.decode("utf-8", errors="replace") if isinstance(value, bytes) else str(value)
