import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("IT_OPS_DB", str(tmp_path / "test.db"))
    monkeypatch.setenv("IT_OPS_API_KEY", "test-key")
    from app import main

    monkeypatch.setattr(main, "DB_PATH", tmp_path / "test.db")
    monkeypatch.setattr(main, "API_KEY", "test-key")
    main.requests_by_key.clear()
    with TestClient(main.app) as test_client:
        yield test_client


@pytest.fixture()
def headers():
    return {"X-IT-Ops-Key": "test-key", "X-Request-ID": "test-trace"}

