"""Authentication request models."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class LoginRequest(BaseModel):
    """Body of ``POST /auth/login``.

    Both fields are declared required, but note the invariant in
    ``services.archihub_login``: every rejection - absent user, wrong password,
    LDAP refusal - must produce an identical response, so that a caller cannot
    learn which usernames exist by comparing them.
    """

    model_config = ConfigDict(extra="allow")

    username: str
    password: str
