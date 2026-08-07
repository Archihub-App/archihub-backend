"""User request models."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

# Collections a favourite may legitimately point at.
#
# This is an allowlist rather than free text because the value selects the
# MongoDB collection to read. A parameter that names a collection is a parameter
# that chooses what the server discloses, so it must be constrained to a fixed
# set at the type level - not validated later, where a new code path can bypass
# the check.
FavoriteType = Literal["resources", "records", "snaps"]


class UserListRequest(BaseModel):
    """Body of ``POST /users`` (a filtered listing, despite the verb)."""

    model_config = ConfigDict(extra="allow")

    page: int = 0
    # Free-form only in shape: the service applies a field allowlist before this
    # reaches a query.
    filters: dict = Field(default_factory=dict)


class TokenRequest(BaseModel):
    model_config = ConfigDict(extra="allow")

    password: str


class AdminTokenRequest(BaseModel):
    model_config = ConfigDict(extra="allow")

    password: str
    # Days. `false` means "no expiry" in the legacy contract.
    duration: int | bool = 2


class FavoriteRequest(BaseModel):
    model_config = ConfigDict(extra="allow")

    type: FavoriteType
    id: str
    view: str | None = None


class FavoriteListRequest(BaseModel):
    model_config = ConfigDict(extra="allow")

    type: FavoriteType
    page: int = 0


class SnapListRequest(BaseModel):
    model_config = ConfigDict(extra="allow")

    type: str
    page: int = 0


class UpdateMeRequest(BaseModel):
    """Self-service profile update.

    Deliberately narrow. The admin update accepts roles and access rights; this
    one must not, or a user could grant themselves privileges by adding fields to
    their own profile request. Anything not declared here is ignored.
    """

    model_config = ConfigDict(extra="ignore")

    name: str | None = None
    password: str | None = None
    new_password: str | None = None


class RegisterRequest(BaseModel):
    """Administrative account creation."""

    model_config = ConfigDict(extra="allow")

    username: str
    name: str | None = None
    password: str = ""
    roles: list[dict] = Field(default_factory=list)
    accessRights: list[dict] = Field(default_factory=list)


class RegisterMeRequest(BaseModel):
    """Self-service registration.

    Deliberately does NOT accept roles or accessRights. `extra="ignore"` rather
    than "allow": a self-registering caller must not be able to influence their
    own privileges by adding fields, and ignoring is safer than relying on the
    service to strip them.
    """

    model_config = ConfigDict(extra="ignore")

    username: str
    name: str | None = None
    password: str = ""


class ForgotPasswordRequest(BaseModel):
    model_config = ConfigDict(extra="allow")

    username: str


class UpdateUserRequest(BaseModel):
    """Administrative update of another account."""

    model_config = ConfigDict(extra="allow")

    id: str = Field(validation_alias="_id")
    username: str | None = None
    name: str | None = None
    roles: list[dict] = Field(default_factory=list)
    accessRights: list[dict] = Field(default_factory=list)


class DeleteUserRequest(BaseModel):
    model_config = ConfigDict(extra="allow")

    username: str


class SelfUpdateRequest(BaseModel):
    """Self-service profile update.

    The current password is required even to change a display name, because this
    same endpoint can change the password.
    """

    model_config = ConfigDict(extra="ignore")

    password: str
    name: str | None = None
    new_password: str | None = None
    new_password_confirmation: str | None = None


class ApiKeyRequest(BaseModel):
    model_config = ConfigDict(extra="allow")

    password: str
    name: str | None = None


class AdminApiKeyRequest(ApiKeyRequest):
    # Days. `false` means "no expiry", matching the legacy contract.
    duration: int | bool = 2
