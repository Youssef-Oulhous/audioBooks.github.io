from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

import pytest

# The API tests use the placeholder engine and a throwaway data folder (set before audiobook.config loads).
os.environ.setdefault("AUDIOBOOK_ENGINE", "demo")
os.environ.setdefault("AUDIOBOOK_DEMO_DELAY", "0")
os.environ.setdefault("AUDIOBOOK_PRELOAD", "0")
os.environ["AUDIOBOOK_DATA"] = tempfile.mkdtemp(prefix="audiobook-test-")
os.environ["AUDIOBOOK_TOKEN"] = "test-token"

SERVER = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SERVER))
FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(scope="session", autouse=True)
def synthetic_fixtures():
    from tests import make_fixtures

    if not (FIXTURES / "synthetic_parts.pdf").exists():
        make_fixtures.fontsize_book()
        make_fixtures.plain_book()
        make_fixtures.chinese_book()
        make_fixtures.parts_book()
    return FIXTURES


def real_fixture(name: str) -> Path:
    """Real books downloaded by tests/get_real_fixtures.sh (classics from planetebook.com and
    Think Python 2, which has sections, an index and printed page numbers)."""
    path = FIXTURES / name
    if not path.exists():
        pytest.skip(f"{name} not downloaded (run tests/get_real_fixtures.sh)")
    return path
