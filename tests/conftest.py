"""Test setup: everything runs offline — fake LLM, SQLite, in-memory Qdrant."""
import os
import tempfile

_tmp = tempfile.mkdtemp(prefix="campaignpilot-test-")
os.environ.update({
    "LLM_PROVIDER": "fake",
    "GOOGLE_API_KEY": "",
    "TOOLS_MODE": "direct",
    "DATABASE_URL": f"sqlite:///{_tmp}/test.db",
    "QDRANT_URL": "memory",
    "CHECKPOINT_DB": f"{_tmp}/checkpoints.sqlite",
    "RAW_DATA_DIR": f"{_tmp}/raw",
    "OUTBOX_DIR": f"{_tmp}/outbox",
})

import pytest  # noqa: E402

from campaignpilot.pipeline.run import run_once  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def warehouse():
    """Generate data and load the warehouse once for the whole test session."""
    return run_once(seed=7)


@pytest.fixture
def tmp_dir():
    return _tmp
