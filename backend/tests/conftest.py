"""Shared pytest fixtures: an isolated in-memory test database and a
FastAPI TestClient wired to it (so tests never touch the real documents.db)."""
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

from app.core.database import Base, get_db  # noqa: E402
from app.main import app  # noqa: E402

TEST_ENGINE = create_engine(
    "sqlite:///:memory:",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
TestingSessionLocal = sessionmaker(bind=TEST_ENGINE, autoflush=False, autocommit=False)


def _override_get_db():
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()


@pytest.fixture(scope="session", autouse=True)
def _create_test_schema():
    from app.models import document  # noqa: F401

    Base.metadata.create_all(bind=TEST_ENGINE)
    yield
    Base.metadata.drop_all(bind=TEST_ENGINE)


@pytest.fixture()
def client():
    app.dependency_overrides[get_db] = _override_get_db
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


PROJECT_ROOT = BACKEND_DIR.parent
DATASET_DIR = PROJECT_ROOT / "dataset"


def dataset_file(*parts: str) -> Path:
    return DATASET_DIR.joinpath(*parts)


DATASET_AVAILABLE = DATASET_DIR.exists()

skip_if_no_dataset = pytest.mark.skipif(
    not DATASET_AVAILABLE, reason="dataset/ directory not present in this checkout"
)
