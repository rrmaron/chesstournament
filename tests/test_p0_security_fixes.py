"""Regression tests for the three P0 fixes (beads mychesspairings-5j9, -9w3, -a1p)."""
import sys
import time

import pytest


def test_secret_key_fails_fast_in_production_without_one(monkeypatch, tmp_path):
    """FLY_APP_NAME set (we're "in production") + no SECRET_KEY must refuse to start,
    instead of silently signing cookies with the known public dev default."""
    from tests.conftest import _fresh_import
    with pytest.raises(RuntimeError, match="SECRET_KEY"):
        _fresh_import(monkeypatch, tmp_path, secret_key=None, extra_env={"FLY_APP_NAME": "mychessrating"})


def test_secret_key_falls_back_locally_with_warning(monkeypatch, tmp_path, caplog):
    """No FLY_APP_NAME (local/dev) + no SECRET_KEY must still boot, using the dev default."""
    from tests.conftest import _fresh_import
    with caplog.at_level("WARNING"):
        module = _fresh_import(monkeypatch, tmp_path, secret_key=None)
    assert module.SECRET_KEY == "dev-secret-key-change-in-production"
    assert any("SECRET_KEY not set" in r.message for r in caplog.records)


def test_secret_key_explicit_value_used_in_production(monkeypatch, tmp_path):
    from tests.conftest import _fresh_import
    module = _fresh_import(monkeypatch, tmp_path, secret_key="a-real-secret", extra_env={"FLY_APP_NAME": "mychessrating"})
    assert module.SECRET_KEY == "a-real-secret"


def test_cancel_link_requires_valid_token(app_module):
    pid = app_module.create_tournament("Open", rounds=5, entry_fee=10)
    player_id = app_module.register_player_public(
        pid, "Alice", None, 0, "a@example.com", None, None, None,
        requested_byes=[], payment_status="pending",
    )
    assert app_module.get_player(player_id) is not None

    token = app_module._make_cancel_token(player_id)
    assert app_module._verify_cancel_token(token) == player_id


def test_cancel_route_rejects_forged_player_id(client, app_module):
    """The old vulnerable behavior: hitting the cancel URL with a guessed/forged
    player_id (and no valid token) must NOT delete the registration."""
    tid = app_module.create_tournament("Open", rounds=5, entry_fee=10)
    player_id = app_module.register_player_public(
        tid, "Bob", None, 0, "b@example.com", None, None, None,
        requested_byes=[], payment_status="pending",
    )

    # Old exploit shape: raw player_id as a query param, no signature at all.
    resp = client.get(f"/tournament/{tid}/register/cancel?player_id={player_id}", follow_redirects=False)
    assert resp.status_code in (303, 404, 422)
    assert app_module.get_player(player_id) is not None, "registration must survive an unsigned cancel request"


def test_cancel_route_deletes_with_valid_token_for_pending_registration(client, app_module):
    tid = app_module.create_tournament("Open", rounds=5, entry_fee=10)
    player_id = app_module.register_player_public(
        tid, "Carol", None, 0, "c@example.com", None, None, None,
        requested_byes=[], payment_status="pending",
    )
    token = app_module._make_cancel_token(player_id)

    resp = client.get(f"/tournament/{tid}/register/cancel?token={token}", follow_redirects=False)
    assert resp.status_code == 303
    assert app_module.get_player(player_id) is None


def test_cancel_route_refuses_to_delete_already_paid_registration(client, app_module):
    """A valid signature for an already-paid registration must not delete it —
    state may have moved on since the token was issued."""
    tid = app_module.create_tournament("Open", rounds=5, entry_fee=10)
    player_id = app_module.register_player_public(
        tid, "Dana", None, 0, "d@example.com", None, None, None,
        requested_byes=[], payment_status="pending",
    )
    app_module.update_player_payment(player_id, "paid", "cs_test_abc")
    token = app_module._make_cancel_token(player_id)

    client.get(f"/tournament/{tid}/register/cancel?token={token}", follow_redirects=False)
    assert app_module.get_player(player_id) is not None


def test_stripe_webhook_rejects_bad_signature(client, app_module, monkeypatch):
    monkeypatch.setattr(app_module, "STRIPE_WEBHOOK_SECRET", "whsec_test123")
    resp = client.post("/stripe/webhook", content=b'{"type": "checkout.session.completed"}',
                        headers={"stripe-signature": "t=1,v1=bad"})
    assert resp.status_code == 400


def test_stripe_webhook_rejects_when_unconfigured(client, app_module, monkeypatch):
    monkeypatch.setattr(app_module, "STRIPE_WEBHOOK_SECRET", "")
    resp = client.post("/stripe/webhook", content=b"{}", headers={"stripe-signature": "t=1,v1=x"})
    assert resp.status_code == 500


def test_stripe_webhook_marks_player_paid_on_valid_event(client, app_module, monkeypatch):
    import stripe, json

    tid = app_module.create_tournament("Open", rounds=5, entry_fee=10)
    player_id = app_module.register_player_public(
        tid, "Eve", None, 0, "e@example.com", None, None, None,
        requested_byes=[], payment_status="pending",
    )

    webhook_secret = "whsec_test123"
    monkeypatch.setattr(app_module, "STRIPE_WEBHOOK_SECRET", webhook_secret)

    payload = json.dumps({
        "id": "evt_test",
        "type": "checkout.session.completed",
        "data": {"object": {
            "id": "cs_test_123",
            "payment_status": "paid",
            "metadata": {"player_id": str(player_id), "tournament_id": str(tid)},
        }},
    }).encode()
    ts = int(time.time())
    sig = stripe.WebhookSignature._compute_signature(f"{ts}.{payload.decode()}", webhook_secret)
    header = f"t={ts},v1={sig}"

    resp = client.post("/stripe/webhook", content=payload, headers={"stripe-signature": header})
    assert resp.status_code == 200
    player = app_module.get_player(player_id)
    assert player["payment_status"] == "paid"
    assert player["payment_intent_id"] == "cs_test_123"
