"""Pytest configuration for Flyte v2 tests.

PINATA_JWT is stripped before any stargazer imports so default_client
resolves to LocalStorageClient.  Tests marked @pytest.mark.pinata get
the JWT injected from tests/.secrets/pinata_jwt at runtime.
"""

import os
import sys
from pathlib import Path

# Strip PINATA_JWT before importing stargazer so the lazy storage client
# resolves to a local-only LocalStorageClient. Pinata-marked tests inject
# the JWT at setup and reset the singleton so the next access re-resolves.
os.environ.pop("PINATA_JWT", None)

import flyte
import pytest

import stargazer.utils.local_storage as _storage_mod
from stargazer.utils.local_storage import LocalStorageClient, _LazyClient

# Add tests directory to Python path for config imports
sys.path.insert(0, str(Path(__file__).parent))

FIXTURES_DIR = Path(__file__).parent / "fixtures"
FIXTURES_DB = FIXTURES_DIR / "stargazer_local.json"
GENERAL_FIXTURES_DIR = FIXTURES_DIR / "general"
GATK_FIXTURES_DIR = FIXTURES_DIR / "gatk"
SCRNA_FIXTURES_DIR = FIXTURES_DIR / "scrna"
SECRETS_DIR = Path(__file__).parent / ".secrets"


@pytest.fixture(scope="session", autouse=True)
def init_flyte_context():
    """Initialize Flyte context for all tests."""
    flyte.init_from_config()
    yield


def _reset_default_client() -> None:
    """Drop the lazy singleton's cached client so the next access re-resolves
    against current os.environ (toggles PinataClient remote on/off)."""
    if isinstance(_storage_mod.default_client, _LazyClient):
        _storage_mod.default_client._instance = None


def pytest_runtest_setup(item):
    """Inject PINATA_JWT for tests marked @pytest.mark.pinata.

    Loads the JWT from tests/.secrets/pinata_jwt. If the file doesn't
    exist, the test is skipped.
    """
    if item.get_closest_marker("pinata"):
        jwt_file = SECRETS_DIR / "pinata_jwt"
        if not jwt_file.exists():
            pytest.skip(f"Pinata JWT not found — put your token in {jwt_file}")
        jwt = jwt_file.read_text().strip()
        if not jwt:
            pytest.skip(f"Pinata JWT file is empty: {jwt_file}")
        os.environ["PINATA_JWT"] = jwt
        _reset_default_client()


def pytest_runtest_teardown(item, nextitem):
    """Remove PINATA_JWT after pinata-marked tests."""
    if item.get_closest_marker("pinata"):
        os.environ.pop("PINATA_JWT", None)
        _reset_default_client()


@pytest.fixture
def fixtures_db(tmp_path):
    """Two-phase fixture: query inputs from fixtures, run tasks in clean tmp.

    Phase 1 (before calling checkout): default_client points to FIXTURES_DIR
    so tests can query the fixtures DB and build types with real CIDs/paths.

    Phase 2 (after calling checkout()): default_client switches to an empty
    tmp work dir so task outputs are isolated per test.
    """
    fixtures_client = LocalStorageClient(local_dir=FIXTURES_DIR)
    fixtures_client.local_db_path = FIXTURES_DB

    orig_client = _storage_mod.default_client
    _storage_mod.default_client = fixtures_client

    def checkout():
        """Switch default_client to an empty tmp dir for task outputs."""
        work_dir = tmp_path / "stargazer_work"
        work_dir.mkdir(parents=True, exist_ok=True)
        work_client = LocalStorageClient(local_dir=work_dir)
        _storage_mod.default_client = work_client

    yield checkout

    _storage_mod.default_client = orig_client
