"""A variable a compose file forwards empty behaves as if it were unset."""

from __future__ import annotations

import importlib


def test_empty_mail_and_directory_settings_fall_back_to_defaults(monkeypatch):
    from archihub.api.auth import ldap_auth
    from archihub.api.email import services as email

    for name in ("EMAIL_PORT", "EMAIL_TIMEOUT", "EMAIL_HOST", "LDAP_HOST", "LDAP_TIMEOUT",
                 "LDAP_NETWORK_TIMEOUT", "LDAP_BASE_DN", "LDAP_TLS_REQUIRE_CERT"):
        monkeypatch.setenv(name, "")
    try:
        importlib.reload(email)
        importlib.reload(ldap_auth)

        assert email.EMAIL_PORT == 587
        assert email.EMAIL_HOST == "smtp.example.com"
        assert ldap_auth.LDAP_HOST is None
        assert ldap_auth.LDAP_TIMEOUT == 15
        assert ldap_auth.LDAP_TLS_REQUIRE_CERT == "demand"
    finally:
        monkeypatch.undo()
        importlib.reload(email)
        importlib.reload(ldap_auth)
