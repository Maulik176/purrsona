"""Shared test setup: never read or write the real key's memory log."""
import pytest


@pytest.fixture(autouse=True)
def isolated_key_memory(tmp_path, monkeypatch):
    monkeypatch.setenv("PURRSONA_KEYS_DIR", str(tmp_path / "keys"))
