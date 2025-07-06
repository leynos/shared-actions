import pytest
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT))

from shellstub import StubManager

@pytest.fixture
def shell_stubs(tmp_path, monkeypatch) -> StubManager:
    dir_ = tmp_path / "stubs"
    mgr = StubManager(dir_)
    monkeypatch.setenv("PATH", f"{dir_}{os.pathsep}{os.getenv('PATH')}")
    monkeypatch.setenv("PYTHONPATH", f"{ROOT}{os.pathsep}{os.getenv('PYTHONPATH','')}")
    yield mgr
