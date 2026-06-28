from __future__ import annotations

from pathlib import Path

import pytest

from tests import harness


@pytest.fixture
def data_dir(tmp_path: Path) -> Path:
    """Temporary data dir for scanner DB."""
    return tmp_path


@pytest.fixture
def client(tmp_path: Path):
    """TestClient wired with a temp data dir (reloads app.main)."""
    with harness.api_client(tmp_path) as c:
        yield c
