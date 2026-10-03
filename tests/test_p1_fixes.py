"""Tests for the four P1 fixes (beads mychesspairings-pi8, -c4t, -0tr, -xo8)."""
import socket

import pytest

from tests.conftest import make_admin, login


# ---------------------------------------------------------------------------
# mychesspairings-pi8: rate limiting / lockout on login and OTP verification
# ---------------------------------------------------------------------------

def test_login_locks_out_after_repeated_failures(client, app_module):
    make_admin(app_module, "alice", "correct-horse")

    for _ in range(5):
        resp = login(client, "alice", "wrong-password")
        assert resp.status_code == 200

    # 6th attempt — even with the CORRECT password — should be throttled.
    resp = login(client, "alice", "correct-horse")
    assert resp.status_code == 429


def test_login_succeeds_normally_under_the_threshold(client, app_module):
    make_admin(app_module, "bob", "correct-horse")
    for _ in range(2):
        login(client, "bob", "wrong-password")
    resp = login(client, "bob", "correct-horse")
    assert resp.status_code in (303,)


def test_successful_login_clears_the_failure_counter(client, app_module):
    make_admin(app_module, "carol", "correct-horse")
    for _ in range(4):
        login(client, "carol", "wrong-password")
    resp = login(client, "carol", "correct-horse")
    assert resp.status_code == 303  # succeeded, counter reset

    for _ in range(4):
        login(client, "carol", "wrong-password")
    resp = login(client, "carol", "correct-horse")
    assert resp.status_code == 303  # not locked out — previous success cleared it


def test_otp_verification_locks_out_after_repeated_failures(client, app_module):
    uid = app_module.create_pending_user("dana", "password123", email="dana@example.com")
    # Logging in as a "pending" user naturally seeds the session with
    # pending_user_id/contact/channel, same as the real /register -> /verify flow.
    resp = login(client, "dana", "password123")
    assert resp.status_code == 303
    assert resp.headers["location"] == "/verify"

    real_code = app_module.create_verification_token(uid, "email", "dana@example.com")

    for _ in range(5):
        r = client.post("/verify", data={"code": "000000"})
        assert r.status_code == 200

    # 6th attempt, even with the correct code, should be throttled.
    r = client.post("/verify", data={"code": real_code})
    assert r.status_code == 429


# ---------------------------------------------------------------------------
# mychesspairings-c4t: SSRF via admin fetch-URL helper
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("url", [
    "http://127.0.0.1/",
    "http://localhost/",
    "http://169.254.169.254/latest/meta-data/",
    "http://10.0.0.5/",
    "http://192.168.1.1/",
    "ftp://example.com/",
    "file:///etc/passwd",
])
def test_is_safe_external_url_blocks_internal_and_non_http(app_module, url):
    assert app_module._is_safe_external_url(url) is False


def test_is_safe_external_url_allows_public_host(app_module, monkeypatch):
    def fake_getaddrinfo(host, port):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 0))]
    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)
    assert app_module._is_safe_external_url("https://example.com/page") is True


def test_is_safe_external_url_blocks_dns_rebinding_to_private_ip(app_module, monkeypatch):
    """A public-looking hostname that resolves to a private IP must still be blocked."""
    def fake_getaddrinfo(host, port):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 0))]
    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)
    assert app_module._is_safe_external_url("http://evil.example.com/") is False


def test_admin_fetch_url_requires_admin(client, app_module):
    # The app's own 401 handler redirects anonymous requests to /login rather
    # than returning a bare 401 — that's existing, intended behavior.
    resp = client.get("/admin/fetch-url?url=https://example.com", follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"].startswith("/login")


def test_admin_fetch_url_rejects_internal_target(client, app_module):
    make_admin(app_module, "admin1", "adminpass123")
    login(client, "admin1", "adminpass123")
    resp = client.get("/admin/fetch-url?url=http://127.0.0.1:22/")
    assert resp.status_code == 400


# ---------------------------------------------------------------------------
# mychesspairings-0tr: IDOR on registration success page
# ---------------------------------------------------------------------------

def test_register_success_requires_token(client, app_module):
    tid = app_module.create_tournament("Open", rounds=5, entry_fee=0)
    player_id = app_module.register_player_public(
        tid, "Frank", None, 0, "f@example.com", "555-0100", None, None,
        requested_byes=[], payment_status="waived",
    )
    # Old exploit shape: just the raw, guessable player_id, no token.
    resp = client.get(f"/tournament/{tid}/register/success?player_id={player_id}")
    assert resp.status_code == 404


def test_register_success_rejects_token_for_a_different_player(client, app_module):
    tid = app_module.create_tournament("Open", rounds=5, entry_fee=0)
    pid_a = app_module.register_player_public(
        tid, "Grace", None, 0, "g@example.com", None, None, None,
        requested_byes=[], payment_status="waived",
    )
    pid_b = app_module.register_player_public(
        tid, "Heidi", None, 0, "h@example.com", None, None, None,
        requested_byes=[], payment_status="waived",
    )
    token_for_a = app_module._make_view_token(pid_a)
    resp = client.get(f"/tournament/{tid}/register/success?player_id={pid_b}&token={token_for_a}")
    assert resp.status_code == 404


def test_register_success_allows_matching_token(client, app_module):
    tid = app_module.create_tournament("Open", rounds=5, entry_fee=0)
    player_id = app_module.register_player_public(
        tid, "Ivan", None, 0, "i@example.com", None, None, None,
        requested_byes=[], payment_status="waived",
    )
    token = app_module._make_view_token(player_id)
    resp = client.get(f"/tournament/{tid}/register/success?player_id={player_id}&token={token}")
    assert resp.status_code == 200


def test_full_registration_flow_issues_a_working_token(client, app_module):
    """End-to-end: submitting the public form (free tournament) redirects
    straight to a success page the submitter can actually view."""
    tid = app_module.create_tournament("Free Open", rounds=5, entry_fee=0)
    resp = client.post(f"/tournament/{tid}/register", data={"name": "Judy", "email": "j@example.com"},
                        follow_redirects=False)
    assert resp.status_code == 303
    location = resp.headers["location"]
    assert "token=" in location

    resp2 = client.get(location)
    assert resp2.status_code == 200


# ---------------------------------------------------------------------------
# mychesspairings-xo8: input validation on public tournament registration
# ---------------------------------------------------------------------------

def test_registration_rejects_blank_name(client, app_module):
    tid = app_module.create_tournament("Open", rounds=5, entry_fee=0)
    resp = client.post(f"/tournament/{tid}/register", data={"name": "   "})
    assert resp.status_code == 400


def test_registration_rejects_malformed_email(client, app_module):
    tid = app_module.create_tournament("Open", rounds=5, entry_fee=0)
    resp = client.post(f"/tournament/{tid}/register", data={"name": "Kevin", "email": "not-an-email"})
    assert resp.status_code == 400


def test_registration_rejects_overlong_name(client, app_module):
    tid = app_module.create_tournament("Open", rounds=5, entry_fee=0)
    resp = client.post(f"/tournament/{tid}/register", data={"name": "X" * 200})
    assert resp.status_code == 400


def test_registration_clamps_out_of_range_byes(client, app_module):
    tid = app_module.create_tournament("Open", rounds=5, entry_fee=0)
    resp = client.post(
        f"/tournament/{tid}/register",
        data={"name": "Liam", "requested_byes": ["2", "9999"]},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    location = resp.headers["location"]
    player_id = int(location.split("player_id=")[1].split("&")[0])
    player = app_module.get_player(player_id)
    import json
    byes = json.loads(player["requested_byes"])
    assert byes == [2]


def test_registration_accepts_valid_input(client, app_module):
    tid = app_module.create_tournament("Open", rounds=5, entry_fee=0)
    resp = client.post(f"/tournament/{tid}/register",
                        data={"name": "Mallory", "email": "mallory@example.com", "phone": "555-0199"},
                        follow_redirects=False)
    assert resp.status_code == 303
