"""Aislamiento: los tests usan una DB SQLite propia en /tmp (no tocan suitelans.db)."""
import os

TEST_DB = "/tmp/opencode/test_suitelans.db"
os.environ["DATABASE_URL"] = f"sqlite:///{TEST_DB}"
for suffix in ("", "-journal", "-wal", "-shm"):
    p = TEST_DB + suffix
    if os.path.exists(p):
        os.remove(p)

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.core import security  # noqa: E402
from app.core.database import Base, SessionLocal, engine  # noqa: E402
from app.main import app  # noqa: E402
from app.models.user import User  # noqa: E402

Base.metadata.create_all(bind=engine)
s = SessionLocal()
if not s.query(User).filter(User.email == "admin@suitelans.mx").first():
    s.add(User(email="admin@suitelans.mx", full_name="Administrador",
               hashed_password=security.hash_password("admin123"), role="admin",
               is_active=True))
    s.commit()
s.close()


@pytest.fixture()
def client():
    return TestClient(app)


@pytest.fixture()
def auth_cookies(client):
    r = client.post("/auth/login",
                    data={"username": "admin@suitelans.mx", "password": "admin123"},
                    follow_redirects=False)
    assert r.status_code == 303
    return {"suitelans_token": r.cookies.get("suitelans_token")}


@pytest.fixture()
def api_token(client):
    r = client.post("/api/v1/auth/token",
                    data={"username": "admin@suitelans.mx", "password": "admin123"})
    assert r.status_code == 200
    return r.json()["access_token"]
