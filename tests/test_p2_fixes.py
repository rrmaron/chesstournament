"""Tests for the P2 fixes (beads mychesspairings-n1b, -nh3, -xf2, -weq, -zlx, -859)."""
import sqlite3

from tests.conftest import make_admin, login


# ---------------------------------------------------------------------------
# mychesspairings-n1b: SQLite WAL mode
# ---------------------------------------------------------------------------

def test_database_uses_wal_journal_mode(app_module):
    import database
    conn = sqlite3.connect(database.DB_FILE)
    mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
    conn.close()
    assert mode.lower() == "wal"


# ---------------------------------------------------------------------------
# mychesspairings-nh3: USCF lookups over HTTPS, not HTTP
# ---------------------------------------------------------------------------

def test_no_plaintext_http_uscf_calls_remain():
    for path in ("main.py", "database.py"):
        text = open(path, encoding="utf-8").read()
        assert "http://www.uschess.org" not in text, f"{path} still has a plaintext HTTP uschess.org call"
        assert "http://uschess.org" not in text


# ---------------------------------------------------------------------------
# mychesspairings-xf2: server-side cap on round generation
# ---------------------------------------------------------------------------

def test_next_round_refuses_past_final_round(client, app_module):
    make_admin(app_module, "td1", "tdpassword1")
    login(client, "td1", "tdpassword1")

    tid = app_module.create_tournament("Short Open", rounds=1, entry_fee=0)
    app_module.update_current_round(tid, 1)  # already at (and past) the only round

    resp = client.post(f"/tournament/{tid}/next-round")
    assert resp.status_code == 200
    assert "already at its final round" in resp.text
    assert app_module.get_tournament(tid)["current_round"] == 1


# ---------------------------------------------------------------------------
# mychesspairings-weq: CSV import no longer silently fuzzy-binds names
# ---------------------------------------------------------------------------

def test_csv_import_flags_ambiguous_name_only_rows(client, app_module, monkeypatch):
    make_admin(app_module, "td2", "tdpassword2")
    login(client, "td2", "tdpassword2")
    tid = app_module.create_tournament("Open", rounds=5, entry_fee=0)

    # No local USCF db entries, so search_uscf_members returns nothing and the
    # row must be added unresolved rather than left out or mismatched.
    monkeypatch.setattr(app_module, "search_uscf_members", lambda name, limit=5: [])

    resp = client.post(
        f"/tournament/{tid}/import-players",
        files={"file": ("players.csv", b"Smith\n", "text/csv")},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert "unresolved=Smith" in resp.headers["location"]
    players = app_module.get_players(tid)
    assert len(players) == 1
    assert players[0]["name"] == "Smith"
    assert players[0]["uscf_id"] is None


def test_csv_import_auto_binds_unique_exact_match(client, app_module, monkeypatch):
    make_admin(app_module, "td3", "tdpassword3")
    login(client, "td3", "tdpassword3")
    tid = app_module.create_tournament("Open", rounds=5, entry_fee=0)

    def fake_search(name, limit=5):
        return [{"uscf_id": "12345678", "name": "DOE, JANE", "rating": 1500, "fide_id": None}]
    monkeypatch.setattr(app_module, "search_uscf_members", fake_search)
    monkeypatch.setattr(app_module, "_format_uscf_name", lambda n: "Jane Doe")

    resp = client.post(
        f"/tournament/{tid}/import-players",
        files={"file": ("players.csv", b"Jane Doe\n", "text/csv")},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert "unresolved" not in resp.headers["location"]
    players = app_module.get_players(tid)
    assert players[0]["uscf_id"] == "12345678"


# ---------------------------------------------------------------------------
# mychesspairings-zlx: CSRF Origin/Referer check on state-changing requests
# ---------------------------------------------------------------------------

def test_csrf_blocks_cross_site_origin(client, app_module):
    make_admin(app_module, "td4", "tdpassword4")
    login(client, "td4", "tdpassword4")
    resp = client.post("/logout", headers={"Origin": "https://evil.example.com"})
    assert resp.status_code == 403


def test_csrf_allows_same_origin_request(client, app_module):
    make_admin(app_module, "td5", "tdpassword5")
    login(client, "td5", "tdpassword5")
    resp = client.post("/logout", headers={"Origin": "http://testserver"}, follow_redirects=False)
    assert resp.status_code == 303


def test_csrf_allows_requests_with_no_origin_header(client, app_module):
    """Plain non-browser clients that send neither Origin nor Referer aren't blocked."""
    make_admin(app_module, "td6", "tdpassword6")
    resp = login(client, "td6", "tdpassword6")
    assert resp.status_code == 303


def test_csrf_exempts_stripe_webhook(client, app_module, monkeypatch):
    monkeypatch.setattr(app_module, "STRIPE_WEBHOOK_SECRET", "")
    resp = client.post("/stripe/webhook", content=b"{}",
                        headers={"stripe-signature": "t=1,v1=x", "Origin": "https://evil.example.com"})
    # Not 403 from the CSRF layer — falls through to the (expected) 500 for
    # an unconfigured webhook secret, proving the exemption, not a bypass.
    assert resp.status_code == 500


# ---------------------------------------------------------------------------
# mychesspairings-859 (partial): the pairing subprocess no longer blocks the
# event loop thread directly — exercised indirectly via to_thread usage.
# ---------------------------------------------------------------------------

def test_generate_next_round_uses_to_thread_for_subprocess(app_module):
    import inspect
    source = inspect.getsource(app_module.generate_next_round)
    assert "asyncio.to_thread" in source
