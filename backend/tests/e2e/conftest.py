"""Skip W5 E2E skeletons unless the dedicated runner enables them."""

import os

import pytest


@pytest.fixture(autouse=True)
def _require_w5_e2e():
    if os.environ.get("W5_E2E") != "1":
        pytest.skip("run via scripts/ci/run_e2e.py")
