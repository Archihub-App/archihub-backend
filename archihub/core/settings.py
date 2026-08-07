"""Application configuration.

Replaces the legacy ``config.py`` (``_require_env`` + ``DevelopmentConfig`` /
``ProductionConfig``) with a single Pydantic ``BaseSettings`` model.

Behaviour preserved from ``config.py``:

* ``SECRET_KEY``, ``JWT_SECRET_KEY`` and ``FERNET_KEY`` are **required** - the
  process refuses to start if any is missing. There are deliberately no
  fallback values for secrets.
* ``TEST_SECRET_HEADER_KEY`` stays optional-with-no-fallback: leaving it unset
  simply means ``/health/test-control/*`` authentication can never succeed,
  which is the safe default for a feature that must stay off outside
  disposable instances.

Deliberate improvement: ``_require_env`` raised on the *first* missing
variable, so an operator with three unset secrets had to run the container
three times to discover them all. Pydantic validates every field before
raising, so a single startup failure now lists all of them at once.

Deliberate change, called out in PLAN_FASTAPI.md: the legacy
``MongoConector`` carried a hardcoded fallback password
(``os.environ.get(MONGO_ADMIN, '7bOS9*NkX41M')``). That literal is NOT
reproduced here. ``mongo_password`` defaults to an empty string instead, so a
deployment that fails to set it gets a normal Mongo authentication error
rather than silently attempting a well-known credential that is published in
the source tree.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        # The .env file carries many variables consumed directly by plugins and
        # third-party SDKs (OPENAI_API_KEY, HF_TOKEN, IMAP_*, ...). Ignore them
        # here instead of failing - this model owns core configuration only.
        extra="ignore",
    )

    # ------------------------------------------------------------------
    # Secrets - required, no fallbacks
    # ------------------------------------------------------------------
    secret_key: str = Field(validation_alias="SECRET_KEY")
    jwt_secret_key: str = Field(validation_alias="JWT_SECRET_KEY")
    fernet_key: str = Field(validation_alias="FERNET_KEY")

    # Optional by design - see module docstring.
    test_secret_header_key: str | None = Field(
        default=None, validation_alias="TEST_SECRET_HEADER_KEY"
    )
    archihub_test_mode: bool = Field(default=False, validation_alias="ARCHIHUB_TEST_MODE")

    # ------------------------------------------------------------------
    # Runtime environment
    # ------------------------------------------------------------------
    # Legacy name was FLASK_ENV. Both are accepted during the migration so a
    # deployment can be cut over without a simultaneous .env edit; ENVIRONMENT
    # wins when both are present. See PLAN_FASTAPI.md section 11.
    environment: Literal["DEV", "PROD"] = Field(default="PROD", validation_alias="ENVIRONMENT")
    flask_env: str | None = Field(default=None, validation_alias="FLASK_ENV")

    environment_name: str = Field(default="prod", validation_alias="ENVIRONMENT_NAME")

    # Legacy name was FLASK_RUN_PORT (compose passes BACKEND_PORT_FLASK into it).
    backend_port: int = Field(default=5000, validation_alias="BACKEND_PORT")
    flask_run_port: int | None = Field(default=None, validation_alias="FLASK_RUN_PORT")

    gunicorn_workers: int = Field(default=4, validation_alias="GUNICORN_WORKERS")

    # JWT lifetime in seconds. Note the /auth/login route deliberately overrides
    # this with a 1-day expiry; this value is the framework-level default only.
    jwt_access_token_expires: int = 18000

    # ------------------------------------------------------------------
    # MongoDB
    # ------------------------------------------------------------------
    mongo_ip_server: str = Field(default="localhost", validation_alias="MONGO_IP_SERVER")
    mongo_port: str = Field(default="27017", validation_alias="MONGO_PORT")
    mongo_user: str = Field(default="admin", validation_alias="MONGO_INITDB_ROOT_USERNAME")
    mongo_password: str = Field(default="", validation_alias="MONGO_INITDB_ROOT_PASSWORD")
    mongo_database: str = Field(default="archihub-prod", validation_alias="MONGO_DATABASE")
    mongo_rs: str = Field(default="rs0", validation_alias="MONGO_RS")
    # How long an operation waits for a reachable server before giving up.
    # The legacy URI set socketTimeoutMS/connectTimeoutMS to 300000 (5 minutes)
    # but never set this, leaving pymongo's 30s default. Made explicit and
    # configurable so an unreachable database surfaces as a prompt error
    # instead of a request that appears to hang.
    mongo_server_selection_timeout_ms: int = Field(
        default=10000, validation_alias="MONGO_SERVER_SELECTION_TIMEOUT_MS"
    )

    # ------------------------------------------------------------------
    # Redis / Celery
    # ------------------------------------------------------------------
    celery_broker_url: str = Field(
        default="redis://localhost", validation_alias="CELERY_BROKER_URL"
    )
    celery_broker_host: str = Field(default="localhost", validation_alias="CELERY_BROKER_HOST")
    celeryd_concurrency: int = Field(default=1, validation_alias="CELERYD_CONCURRENCY")
    celery_worker_pool: str | None = Field(default=None, validation_alias="CELERY_WORKER_POOL")
    celery_beat_refresh_interval: int = Field(
        default=60, validation_alias="CELERY_BEAT_REFRESH_INTERVAL"
    )
    # Set by start_celery.sh so plugin activate_settings() only runs in workers.
    celery_worker: bool = Field(default=False, validation_alias="CELERY_WORKER")

    # ------------------------------------------------------------------
    # Elasticsearch
    # ------------------------------------------------------------------
    # Stays on the raw-HTTP client for this migration - the elasticsearch-py
    # adoption is deferred with the 7->8 server upgrade. See decision 3.
    elastic_domain: str = Field(default="http://localhost", validation_alias="ELASTIC_DOMAIN")
    elastic_port: str = Field(default="9200", validation_alias="ELASTIC_PORT")
    elastic_user: str = Field(default="elastic", validation_alias="ELASTIC_USER")
    elastic_password: str = Field(default="", validation_alias="ELASTIC_PASSWORD")
    elastic_index_prefix: str = Field(default="archihub", validation_alias="ELASTIC_INDEX_PREFIX")
    elastic_cert: str | None = Field(default=None, validation_alias="ELASTIC_CERT")

    # ------------------------------------------------------------------
    # Qdrant
    # ------------------------------------------------------------------
    vector_host: str = Field(default="localhost", validation_alias="VECTOR_HOST")
    vector_port: int = Field(default=6333, validation_alias="VECTOR_PORT")
    # Legacy read this straight from os.environ, so it was a *str* whenever the
    # variable was set and an int otherwise - then handed to Qdrant's
    # VectorParams(size=...), which requires an int. Typed properly here.
    vector_size: int = Field(default=768, validation_alias="VECTOR_SIZE")

    # ------------------------------------------------------------------
    # File storage
    # ------------------------------------------------------------------
    user_files_path: str = Field(default="", validation_alias="USER_FILES_PATH")
    web_files_path: str = Field(default="", validation_alias="WEB_FILES_PATH")
    original_files_path: str = Field(default="", validation_alias="ORIGINAL_FILES_PATH")
    temporal_files_path: str = Field(default="", validation_alias="TEMPORAL_FILES_PATH")

    # Upload ceiling. Flask had no MAX_CONTENT_LENGTH at all, so uploads were
    # effectively unbounded; inheriting "unbounded" by accident is worse than
    # choosing a number. See PLAN_FASTAPI.md section 6.
    max_upload_bytes: int = Field(default=5 * 1024 * 1024 * 1024, validation_alias="MAX_UPLOAD_BYTES")

    # ------------------------------------------------------------------
    # Networking / nodes
    # ------------------------------------------------------------------
    url_frontend: str | None = Field(default=None, validation_alias="URL_FRONTEND")
    master_host: str = Field(default="", validation_alias="MASTER_HOST")
    node_token: str = Field(default="", validation_alias="NODE_TOKEN")

    # ------------------------------------------------------------------
    # Derived values
    # ------------------------------------------------------------------
    @field_validator("archihub_test_mode", "celery_worker", mode="before")
    @classmethod
    def _parse_loose_bool(cls, value: object) -> object:
        """Accept the shell-ish truthy spellings the legacy code checked for.

        ``TestControlAuth`` compared ``os.environ.get(...).lower() != 'true'``
        and ``register_plugin`` used a bare ``os.environ.get('CELERY_WORKER', False)``
        truthiness check (so ``CELERY_WORKER=0`` counted as *enabled*). Pydantic's
        stricter bool parsing is used instead, which is the safer reading for a
        flag that gates destructive test-control routes.
        """
        if isinstance(value, str):
            return value.strip().lower() in {"1", "true", "yes", "on"}
        return value

    @property
    def is_dev(self) -> bool:
        """DEV mode, honouring the legacy FLASK_ENV spelling as a fallback."""
        if self.flask_env is not None and self.flask_env.upper() == "DEV":
            return True
        return self.environment == "DEV"

    @property
    def effective_port(self) -> int:
        return self.flask_run_port or self.backend_port

    @property
    def cors_origins(self) -> list[str] | str:
        """Legacy CORS behaviour, preserved exactly.

        ``/adminApi/*`` and ``/publicApi/*`` are always ``*`` (they are consumed
        by other organisations' scripts, not just this repo's frontend); every
        other path is restricted to URL_FRONTEND when it is set. This wildcard
        is intentional - see the CORS note in CLAUDE.md before "fixing" it.
        """
        if self.url_frontend:
            return [origin.strip() for origin in self.url_frontend.split(",") if origin.strip()]
        return "*"

    def mongo_uri(self) -> str:
        """Build the Mongo connection URI.

        Port of ``MongoConector.getMongoURI()``, preserving its exact URI shape
        (including the ``ssl=false`` and 300s timeout parameters) so connection
        behaviour is unchanged.
        """
        hosts = [host.strip() for host in self.mongo_ip_server.split(",") if host.strip()]
        if not hosts:
            hosts = ["localhost"]

        credentials = f"{self.mongo_user or 'admin'}:{self.mongo_password}"
        authority = ",".join(f"{host}:{self.mongo_port}" for host in hosts)
        # socketTimeoutMS/connectTimeoutMS keep their legacy values - some
        # queries and bulk operations here genuinely run for minutes.
        # serverSelectionTimeoutMS is added (see the field docstring).
        timeout = (
            "&socketTimeoutMS=300000&connectTimeoutMS=300000"
            f"&serverSelectionTimeoutMS={self.mongo_server_selection_timeout_ms}"
        )

        if len(hosts) > 1:
            options = (
                f"?authSource=admin&readPreference=primary&retryWrites=true"
                f"&w=majority&replicaSet={self.mongo_rs}&ssl=false{timeout}"
            )
        else:
            options = f"?authSource=admin&readPreference=primary&ssl=false{timeout}"

        return f"mongodb://{credentials}@{authority}/{self.mongo_database}{options}"

    @property
    def elastic_base_url(self) -> str:
        return f"{self.elastic_domain}:{self.elastic_port}"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the process-wide settings singleton.

    Cached so the ``.env`` file is parsed once. Call ``get_settings.cache_clear()``
    in tests that need to re-read the environment.
    """
    return Settings()  # type: ignore[call-arg]  # values come from env/.env
