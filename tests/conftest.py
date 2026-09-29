"""Keep test workspaces inside this project and isolate every test invocation."""

import shutil
import tempfile
from pathlib import Path

import pytest

RUNTIME_TEST_ROOT = Path(__file__).resolve().parents[1] / ".runtime" / "pytest-cases"


@pytest.fixture
def tmp_path():
    """Avoid pytest's shared Windows temp root, which may belong to another account."""
    RUNTIME_TEST_ROOT.mkdir(parents=True, exist_ok=True)
    directory = Path(tempfile.mkdtemp(prefix="case-", dir=RUNTIME_TEST_ROOT))
    try:
        yield directory
    finally:
        shutil.rmtree(directory, ignore_errors=True)
