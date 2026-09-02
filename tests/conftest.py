"""Shared fixtures.

The integration tests use a real PostgreSQL and their own schema, because what
is worth testing there is the constraints (a residual score above its inherent
one, two acceptances in force for one risk) and a mock would simply agree with
whatever the code did.
"""

from __future__ import annotations

import datetime as dt
import os
from collections.abc import Iterator
from pathlib import Path

import pytest

from sentinel.catalogue import Catalogue, load_catalogue
from sentinel.config import Settings
from sentinel.db.engine import check_connection, dispose_engines
from sentinel.db.schema import drop_schema, initialise_database

REPO_ROOT = Path(__file__).resolve().parents[1]
TODAY = dt.date(2026, 9, 27)

requires_database = pytest.mark.skipif(
    os.environ.get("SENTINEL_RUN_INTEGRATION") != "1",
    reason="set SENTINEL_RUN_INTEGRATION=1 and provide a PostgreSQL to run these",
)


@pytest.fixture(scope="session")
def catalogue() -> Catalogue:
    """The real catalogue. Loading it in the tests is itself a check."""
    return load_catalogue(REPO_ROOT / "catalogue")


@pytest.fixture(scope="session")
def integration_settings() -> Iterator[Settings]:
    """An isolated ISMS schema, created and dropped around the session."""
    settings = Settings(SENTINEL_SCHEMA="test_isms", data_dir=REPO_ROOT / "data")
    if not check_connection(settings, retries=2):
        pytest.skip("no PostgreSQL available")
    drop_schema(settings)
    initialise_database(settings)
    yield settings
    drop_schema(settings)
    dispose_engines()
