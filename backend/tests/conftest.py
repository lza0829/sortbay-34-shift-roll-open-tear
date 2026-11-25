from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

# Use an isolated DB by default, but do not override a caller/hidden-suite DB.
# Include the PID so another local/background pytest run cannot delete this
# process's database while a test is in flight.
_TMP_DB = Path(tempfile.gettempdir()) / f"sortbay-pytest-{os.getpid()}.db"
os.environ.setdefault("SORTBAY_DB", str(_TMP_DB))


@pytest.fixture
def client():
    # Import after SORTBAY_DB has been resolved so hidden suites can override
    # the environment before importing the application themselves.
    from app.db import init_db

    db_path = Path(os.environ["SORTBAY_DB"])
    if db_path.exists():
        db_path.unlink()
    for suffix in ("-wal", "-shm"):
        side = Path(str(db_path) + suffix)
        if side.exists():
            side.unlink()
    init_db()
    # Re-seed after deleting the file so tests that use this fixture directly
    # see the same baseline on every run, not only during the first import.
    from app.seed import seed_if_empty

    seed_if_empty()

    from app.main import app

    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def world(client):
    """Create a compact known world after schema initialization."""
    from app.db import db

    with db() as conn:
        tables = [
            "route_events",
            "audit_events",
            "domain_events",
            "scans",
            "bags",
            "divert_batches",
            "route_rules",
            "chutes",
            "shifts",
            "lines",
            "settings",
        ]
        for table in tables:
            conn.execute(f"DELETE FROM {table}")
            conn.execute("DELETE FROM sqlite_sequence WHERE name=?", (table,))

        now = "2026-11-25 08:00:00"
        conn.execute("INSERT INTO lines(id, name, active) VALUES (1, '一号皮带', 1)")
        conn.execute(
            "INSERT INTO shifts(id, business_day, name, sealed, opened_at) "
            "VALUES (1, '2026-11-25', '早班', 0, ?)",
            (now,),
        )
        conn.execute(
            "INSERT INTO chutes(id, line_id, code, title, status, soft_cap) "
            "VALUES (1, 1, 'A01', '华北格', '口门开', 120)"
        )
        conn.execute(
            "INSERT INTO route_rules(id, dest_code, chute_id, note) VALUES (1, '110', 1, '')"
        )
    return client
