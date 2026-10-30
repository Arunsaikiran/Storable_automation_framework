"""Connection settings the generator depends on. The connector class is replaced, so no connection is opened."""

import pytest
import yaml

from db import factory


@pytest.fixture
def recorded_postgres(monkeypatch, tmp_path):
    """Makes creds/qa_port.yaml with port 5433, and records the arguments get_database() gives the connector."""
    (tmp_path / "creds").mkdir()
    (tmp_path / "creds" / "qa_port.yaml").write_text(yaml.safe_dump({"postgres": {
        "dbname": "fms", "host": "localhost", "user": "u", "password": "p", "port": 5433}}), encoding="utf-8")
    calls = {}

    class Recorder:
        def __init__(self, **kwargs):
            calls.update(kwargs)

    monkeypatch.setattr(factory, "Postgres", Recorder)
    return tmp_path, calls


@pytest.mark.xfail(strict=True, reason="ISSUE-06: get_database() ignores the port in creds and always connects to 5432")
def test_the_port_in_the_creds_file_is_used(recorded_postgres):
    root, calls = recorded_postgres
    factory.get_database("postgres", str(root), "qa_port")
    assert calls["port"] == 5433
