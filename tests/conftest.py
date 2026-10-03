import sys

import pytest

_APP_MODULES = ["main", "database", "auth", "trf_builder", "fide", "notify", "pgn_harvester"]


def _fresh_import(monkeypatch, tmp_path, *, secret_key="test-secret-key", extra_env=None):
    """Import main.py (and the database module it initializes) against a fresh,
    throwaway SQLite file so tests never touch the real app data."""
    db_file = tmp_path / "test.db"
    monkeypatch.setenv("DB_FILE", str(db_file))
    monkeypatch.delenv("FLY_APP_NAME", raising=False)
    monkeypatch.delenv("STRIPE_SECRET_KEY", raising=False)
    monkeypatch.delenv("STRIPE_WEBHOOK_SECRET", raising=False)
    if secret_key is None:
        monkeypatch.delenv("SECRET_KEY", raising=False)
    else:
        monkeypatch.setenv("SECRET_KEY", secret_key)
    for key, value in (extra_env or {}).items():
        monkeypatch.setenv(key, value)

    for name in _APP_MODULES:
        sys.modules.pop(name, None)

    import main as main_module
    return main_module


@pytest.fixture()
def app_module(monkeypatch, tmp_path):
    module = _fresh_import(monkeypatch, tmp_path)
    yield module
    for name in _APP_MODULES:
        sys.modules.pop(name, None)


@pytest.fixture()
def client(app_module):
    from starlette.testclient import TestClient
    # Plain instantiation (no `with`) deliberately skips lifespan/startup events,
    # so the background PGN harvest loop never spins up during tests.
    return TestClient(app_module.app)


@pytest.fixture()
def db(app_module):
    return app_module  # database.* functions are re-imported fresh inside main_module's namespace


def make_admin(app_module, username="admin", password="hunter22"):
    uid = app_module.create_user(username, password, role="admin", status="active")
    return uid


def login(client, username, password):
    return client.post("/login", data={"username": username, "password": password}, follow_redirects=False)
