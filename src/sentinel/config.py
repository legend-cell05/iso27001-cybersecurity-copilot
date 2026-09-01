"""Configuration.

One typed object, built from the environment and validated once at startup.
Two things here are load-bearing rather than decorative:

* the database password is a ``SecretStr``, so it cannot leak through the
  ``repr()`` that an unhandled exception prints or that a logging framework
  emits when somebody logs the settings to debug a connection;
* the schema name is validated against an allow-list pattern, because a schema
  name cannot be a bound parameter and therefore has to be interpolated into
  SQL. The validator is the control that makes that safe.

The risk appetite, the evidence ages and the gate thresholds are configuration
for a reason that matters to this project: they are business decisions. An
organisation's appetite for residual risk is something its management argues
about and signs; it is not a constant belonging in a scoring function.
"""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

from pydantic import Field, SecretStr, computed_field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[2]
IDENTIFIER = re.compile(r"^[a-z_][a-z0-9_]*$")


class Settings(BaseSettings):
    """Everything the application needs to know, from the environment."""

    model_config = SettingsConfigDict(
        env_prefix="SENTINEL_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- Database ----------------------------------------------------------
    db_host: str = "localhost"
    db_port: int = 5432
    db_name: str = "sentinel"
    db_user: str = "sentinel_app"
    db_password: SecretStr = SecretStr("change_me_local_only")
    schema_name: str = Field(default="isms", alias="SENTINEL_SCHEMA")

    # --- The organisation --------------------------------------------------
    organisation: str = "Meridian Logistics SAS"
    scope: str = (
        "The information systems supporting freight booking and customer data "
        "processing, at the Lyon and Lille sites."
    )

    # --- Risk method -------------------------------------------------------
    risk_appetite: int = Field(default=6, ge=1, le=25)
    risk_escalation: int = Field(default=15, ge=1, le=25)

    # --- Evidence ----------------------------------------------------------
    evidence_max_age_days: int = Field(default=365, ge=1)
    evidence_warn_days: int = Field(default=60, ge=0)

    # --- The readiness gate ------------------------------------------------
    min_implemented_ratio: float = Field(default=0.95, ge=0.0, le=1.0)
    max_controls_without_evidence: int = Field(default=0, ge=0)
    require_internal_audit: bool = True

    # --- Paths -------------------------------------------------------------
    data_dir: Path = Path("data")
    catalogue_dir: Path = Path("catalogue")

    # --- Demo data ---------------------------------------------------------
    random_seed: int = 20260927

    # --- Runtime -----------------------------------------------------------
    log_level: str = "INFO"
    log_format: str = "text"

    @field_validator("schema_name")
    @classmethod
    def _schema_is_an_identifier(cls, value: str) -> str:
        """Schema names are interpolated into SQL, so they are checked here.

        A bound parameter cannot carry an identifier, which leaves exactly two
        options: quote-and-hope, or refuse anything that is not a plain
        lowercase identifier. This is the second.
        """
        if not IDENTIFIER.match(value):
            raise ValueError(
                f"schema name {value!r} must match {IDENTIFIER.pattern} -- "
                "it is interpolated into SQL and cannot be a bound parameter"
            )
        return value

    @field_validator("log_level")
    @classmethod
    def _known_level(cls, value: str) -> str:
        level = value.upper()
        if level not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
            raise ValueError(f"unknown log level {value!r}")
        return level

    @field_validator("log_format")
    @classmethod
    def _known_format(cls, value: str) -> str:
        fmt = value.lower()
        if fmt not in {"text", "json"}:
            raise ValueError(f"log format must be 'text' or 'json', not {value!r}")
        return fmt

    @field_validator("organisation", "scope")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("must not be blank -- it appears on the Statement of Applicability")
        return value.strip()

    # --- Derived -----------------------------------------------------------

    @computed_field  # type: ignore[prop-decorator]
    @property
    def dsn(self) -> str:
        password = self.db_password.get_secret_value()
        return (
            f"postgresql+psycopg://{self.db_user}:{password}"
            f"@{self.db_host}:{self.db_port}/{self.db_name}"
        )

    @computed_field  # type: ignore[prop-decorator]
    @property
    def safe_dsn(self) -> str:
        """The DSN with the password removed, for logs and diagnostics."""
        return (
            f"postgresql+psycopg://{self.db_user}:***@{self.db_host}:{self.db_port}/{self.db_name}"
        )

    @property
    def project_root(self) -> Path:
        return PROJECT_ROOT

    @property
    def catalogue_path(self) -> Path:
        path = self.catalogue_dir
        return path if path.is_absolute() else self.project_root / path

    @property
    def data_path(self) -> Path:
        path = self.data_dir
        return path if path.is_absolute() else self.project_root / path

    @property
    def export_dir(self) -> Path:
        return self.data_path / "exports"

    @property
    def evidence_dir(self) -> Path:
        return self.data_path / "evidence"

    def model_post_init(self, __context: object) -> None:
        if self.risk_escalation <= self.risk_appetite:
            raise ValueError(
                f"the escalation level ({self.risk_escalation}) must be above the appetite "
                f"({self.risk_appetite}); otherwise every risk above appetite is also "
                "an escalation and the distinction does nothing"
            )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """The settings, read once."""
    return Settings()


def reset_settings() -> None:
    """Forget the cached settings. Tests use this; nothing else should."""
    get_settings.cache_clear()
