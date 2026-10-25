"""Receive-only tournament addresses at our domain: Resend's email.received
webhook is signature-checked, the owner is found from the recipient address,
the body and PGN attachments are fetched, processed like IMAP mail, and a copy
is forwarded to the user's real email.
"""
import base64
import json
import time

from tests.conftest import login
from tests.test_my_tournaments import CHESSNOTER_PGN, _setup_user

SECRET = "whsec_" + base64.b64encode(b"0123456789abcdef0123456789abcdef").decode()
DOMAIN = "inbox.mychessrating.com"


def _configure(app_module, monkeypatch, secret=SECRET):
    monkeypatch.setattr(app_module, "INBOUND_EMAIL_DOMAIN", DOMAIN)
    monkeypatch.setattr(app_module, "RESEND_INBOUND_WEBHOOK_SECRET", secret)


def _post_event(client, app_module, data, secret=SECRET, ts=None, msg_id="msg_1", event_type="email.received"):
    body = json.dumps({"type": event_type, "created_at": "2026-11-07T20:00:00.000Z", "data": data}).encode()
    ts = str(int(ts if ts is not None else time.time()))
    sig = app_module.im.sign(secret, msg_id, ts, body)
    return client.post("/webhooks/resend-inbound", content=body,
                       headers={"svix-id": msg_id, "svix-timestamp": ts, "svix-signature": sig,
                                "content-type": "application/json"})


def _fake_resend(app_module, monkeypatch, address, pgn=CHESSNOTER_PGN, message_id="<g1@chessnoter>"):
    async def fetch_email(api_key, email_id):
        return {"id": email_id, "from": "ChessNoteR <games@chessnoter.com>", "to": [address],
                "subject": "Your game from round 1", "text": "Your game is attached.", "html": "<p>Your game is attached.</p>",
                "created_at": "2026-11-07T20:00:00.000Z", "message_id": message_id}

    async def fetch_attachments(api_key, email_id):
        return [{"filename": "game.pgn", "content_type": "application/x-chess-pgn", "data": pgn.encode()}]

    forwarded = []

    async def forward(to, original_from, subject, text, html, attachments):
        forwarded.append({"to": to, "from": original_from, "subject": subject, "attachments": [a["filename"] for a in attachments]})

    monkeypatch.setattr(app_module.im, "fetch_received_email", fetch_email)
    monkeypatch.setattr(app_module.im, "fetch_attachments", fetch_attachments)
    monkeypatch.setattr(app_module._notify, "send_forward_email", forward)
    return forwarded


def test_signature_verification(app_module):
    im = app_module.im
    body = b'{"type":"email.received"}'
    now = 1_800_000_000
    sig = im.sign(SECRET, "msg_1", str(now), body)
    assert im.verify_signature(SECRET, "msg_1", str(now), sig, body, now=now)
    assert im.verify_signature(SECRET, "msg_1", str(now), "v1,bogus " + sig, body, now=now)  # several signatures
    assert not im.verify_signature(SECRET, "msg_1", str(now), sig, body + b" ", body and now)  # tampered body
    assert not im.verify_signature(SECRET, "msg_2", str(now), sig, body, now=now)                # other id
    assert not im.verify_signature(SECRET, "msg_1", str(now), sig, body, now=now + 600)          # stale
    other = "whsec_" + base64.b64encode(b"fedcba9876543210fedcba9876543210").decode()
    assert not im.verify_signature(other, "msg_1", str(now), sig, body, now=now)
    assert not im.verify_signature("", "msg_1", str(now), sig, body, now=now)

    assert im.local_part_of(["Nak <nak-ab12@inbox.mychessrating.com>", "x@gmail.com"], DOMAIN) == "nak-ab12"
    assert im.local_part_of(["x@gmail.com"], DOMAIN) is None
    assert im.make_local_part("Nakshwin Mehta!", "ab12") == "nakshwin-mehta-ab12"
    assert im.uid_from_email_id("em_1") == im.uid_from_email_id("em_1") > 0


def test_address_lifecycle_and_webhook_delivery(client, app_module, monkeypatch):
    uid = _setup_user(app_module, client)
    app_module.update_user_contact(uid, "nak@example.com", None)
    tid, _ = app_module.create_tracked_tournament(uid, "Dallas Fall Open", "2026-11-06", "2026-11-08", "U2000")

    # Not configured: nothing works and the page shows no address section.
    assert "tournament address" not in client.get("/my-tournaments").text.lower()
    assert client.post("/webhooks/resend-inbound", content=b"{}").status_code == 503

    _configure(app_module, monkeypatch)
    page = client.get("/my-tournaments").text
    assert "Create my tournament address" in page and DOMAIN in page

    resp = client.post("/my-tournaments/inbound/enable", follow_redirects=False)
    row = app_module.get_inbound_address(uid)
    assert row and row["local_part"].startswith("nak-") and row["forward"] == 1
    address = f"{row['local_part']}@{DOMAIN}"
    assert address in resp.headers["location"].replace("%40", "@") and address in client.get("/my-tournaments").text

    forwarded = _fake_resend(app_module, monkeypatch, address)
    event = {"email_id": "em_1", "to": [address], "received_for": [], "cc": [], "bcc": [],
             "from": "games@chessnoter.com", "subject": "Your game from round 1", "message_id": "<g1@chessnoter>",
             "attachments": [{"id": "att_1", "filename": "game.pgn", "content_type": "application/x-chess-pgn"}]}

    resp = _post_event(client, app_module, event)
    assert resp.status_code == 200 and resp.json() == {"ok": True, "kind": "pgn"}
    games = app_module.list_tracked_games(tid)
    assert len(games) == 1 and games[0]["opponent_name"] == "Smith, John" and games[0]["my_color"] == "white"
    assert forwarded == [{"to": "nak@example.com", "from": "games@chessnoter.com", "subject": "Your game from round 1",
                          "attachments": ["game.pgn"]}]
    assert app_module.get_inbound_address(uid)["received"] == 1
    logged = app_module.list_email_messages(uid)
    assert logged[0]["kind"] == "pgn" and logged[0]["from_addr"] == "games@chessnoter.com"

    # Resend retries the same event: nothing is duplicated or re-forwarded.
    resp = _post_event(client, app_module, event, msg_id="msg_retry")
    assert resp.json()["kind"] == "duplicate"
    assert len(app_module.list_tracked_games(tid)) == 1 and len(forwarded) == 1

    # Bad signature, stale timestamp, other event types, unknown recipients.
    assert _post_event(client, app_module, event, secret="whsec_" + base64.b64encode(b"x" * 32).decode()).status_code == 403
    assert _post_event(client, app_module, event, ts=time.time() - 3600).status_code == 403
    assert _post_event(client, app_module, event, event_type="email.sent").json() == {"ignored": "email.sent"}
    assert _post_event(client, app_module, {**event, "to": ["nobody-zz99@" + DOMAIN]}).json() == {"ignored": "unknown recipient"}

    # Forwarding off: processed, not forwarded.
    client.post("/my-tournaments/inbound/forward", data={"enabled": "0"})
    _fake_resend(app_module, monkeypatch, address, message_id="<g2@chessnoter>")
    resp = _post_event(client, app_module, {**event, "email_id": "em_2", "message_id": "<g2@chessnoter>"})
    assert resp.json()["kind"] == "pgn" and len(forwarded) == 1

    # Removing the address stops delivery.
    client.post("/my-tournaments/inbound/disable")
    assert app_module.get_inbound_address(uid) is None
    assert _post_event(client, app_module, {**event, "email_id": "em_3"}).json() == {"ignored": "unknown recipient"}


def test_to_message_reads_inline_pgn_and_html(app_module):
    im = app_module.im
    msg = im.to_message({"from": "Someone <a@b.c>", "subject": "Game", "text": "", "html": "<p>See below</p>" + CHESSNOTER_PGN.replace("\n", "<br>"),
                         "created_at": "2026-11-07T20:00:00Z", "message_id": "<m@x>"}, [], 5)
    assert msg["uid"] == 5 and msg["from_addr"] == "a@b.c" and msg["received_at"] == "2026-11-07 20:00:00"
    assert len(msg["pgns"]) == 1 and '[White "Mehta, Nakshwin"]' in msg["pgns"][0]


def test_receiving_key_overrides_sending_key(client, app_module, monkeypatch):
    """Resend sending-only keys get 401 on the receiving API, so a separate
    full-access key can be configured; without it the sending key is used."""
    uid = _setup_user(app_module, client)
    _configure(app_module, monkeypatch)
    client.post("/my-tournaments/inbound/enable")
    address = f"{app_module.get_inbound_address(uid)['local_part']}@{DOMAIN}"
    _fake_resend(app_module, monkeypatch, address)
    used = []
    orig_email, orig_att = app_module.im.fetch_received_email, app_module.im.fetch_attachments

    async def spy_email(api_key, email_id):
        used.append(api_key)
        return await orig_email(api_key, email_id)

    async def spy_att(api_key, email_id):
        used.append(api_key)
        return await orig_att(api_key, email_id)

    monkeypatch.setattr(app_module.im, "fetch_received_email", spy_email)
    monkeypatch.setattr(app_module.im, "fetch_attachments", spy_att)
    monkeypatch.setattr(app_module._notify, "RESEND_API_KEY", "re_send_only")
    event = {"email_id": "em_k1", "to": [address], "message_id": "<k1@x>"}

    monkeypatch.setattr(app_module, "RESEND_RECEIVING_API_KEY", "")
    assert _post_event(client, app_module, event).status_code == 200
    assert used == ["re_send_only", "re_send_only"]

    used.clear()
    monkeypatch.setattr(app_module, "RESEND_RECEIVING_API_KEY", "re_full_access")
    assert _post_event(client, app_module, {**event, "email_id": "em_k2", "message_id": "<k2@x>"}).status_code == 200
    assert used == ["re_full_access", "re_full_access"]
