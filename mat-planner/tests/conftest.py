import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker, Session as SASession
from fastapi.testclient import TestClient

from app.core.database import Base
import app.models.orm  # noqa: F401 — ensure all ORM models are registered
from app.api.main import app

# 测试用内存 SQLite — 使用 file-based 共享 URI 以支持多 session
TEST_DATABASE_URL = "sqlite:///./test_mat_planner.db"


@pytest.fixture(scope="session")
def engine():
    eng = create_engine(TEST_DATABASE_URL, connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=eng)
    yield eng
    Base.metadata.drop_all(bind=eng)
    eng.dispose()
    # clean up file
    import os
    try:
        os.remove("./test_mat_planner.db")
    except FileNotFoundError:
        pass


@pytest.fixture
def db(engine):
    """Each test gets a fresh session; we use BEGIN SAVEPOINT to wrap the test
    so that commit() calls inside services don't bleed into other tests."""
    connection = engine.connect()
    transaction = connection.begin()
    session = SASession(bind=connection)

    # Patch session.commit to use SAVEPOINT instead of real commit
    session.begin_nested()

    @event.listens_for(session, "after_transaction_end")
    def restart_savepoint(sess, trans):
        if trans.nested and not trans._parent.nested:
            sess.begin_nested()

    try:
        yield session
    finally:
        session.close()
        transaction.rollback()
        connection.close()


@pytest.fixture
def client(db):
    from app.core.database import get_db

    def override_get_db():
        yield db

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


@pytest.fixture
def sample_md_file(tmp_path):
    content = """# Test Document

## Abstract

HTPB/AP composite propellant study. RDX has a density of 1.82 g/cm³.

## 1. Introduction

Ammonium perchlorate (AP) is used as oxidizer. The burning rate of HTPB/AP/Al propellant reached 8.5 mm/s at 7 MPa.

## 2. Results

### 2.1 Burning Rate

| AP Size (μm) | Burning Rate (mm/s) |
|---|---|
| 200 | 5.2 |
| 20  | 11.3 |

The detonation velocity of HMX is 9100 m/s.
"""
    f = tmp_path / "test_sample.md"
    f.write_text(content)
    return f
