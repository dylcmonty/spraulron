import json
from pathlib import Path

import pytest

FIX = Path(__file__).parent / "fixtures"


@pytest.fixture
def fixture_text():
    def _load(name: str) -> str:
        return (FIX / name).read_text(encoding="utf-8")
    return _load


@pytest.fixture
def fixture_json():
    def _load(name: str):
        return json.loads((FIX / name).read_text(encoding="utf-8"))
    return _load


@pytest.fixture
def settings():
    from spraulron.config import load_settings
    return load_settings()
