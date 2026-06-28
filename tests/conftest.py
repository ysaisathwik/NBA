import os

os.environ.setdefault("NBA_FORCE_OFFLINE", "1")  # deterministic tests, no network
os.environ["SUPABASE_URL"] = ""                   # force SQLite store in tests (no network)
os.environ["SUPABASE_KEY"] = ""

import pytest

from nba_platform.config import Settings, reset_settings
from nba_platform.runtime import Platform
from nba_platform.orchestrator import Orchestrator


@pytest.fixture()
def platform():
    reset_settings()
    return Platform(settings=Settings(db_path=":memory:", force_offline=True))


@pytest.fixture()
def orch(platform):
    return Orchestrator(platform)
