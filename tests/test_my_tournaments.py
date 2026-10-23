"""My Tournaments: a connected mailbox (IMAP, app password, encrypted at rest)
is polled; registration confirmations auto-create tracked tournaments; the
entry list is imported to predict pairings; PGNs arriving by email or upload
attach to the matching tournament's games.
"""
from email.message import EmailMessage
from urllib.parse import unquote_plus

from tests.conftest import login

CHESSNOTER_PGN = """[Event "Dallas Fall Open"]
[Site "Dallas, TX USA"]
[Date "2026.11.07"]
[Round "1"]
[White "Mehta, Nakshwin"]
[Black "Smith, John"]
[WhiteElo "1850"]
[BlackElo "1702"]
[Result "1-0"]

1. e4 e5 2. Nf3 Nc6 3. Bc4 Bc5 4. c3 Nf6 5. d4 exd4 6. cxd4 Bb4+ 7. Nc3 1-0
"""

STRAY_PGN = """[Event "Club Blitz"]
[Site "Online"]
[Date "2026.09.02"]
[Round "3"]
[White "Jones, Amy"]
[Black "Mehta, Nakshwin"]
[Result "0-1"]

1. d4 d5 2. c4 e6 0-1
"""

REGISTRATION = {
    "is_registration": True, "tournament_name": "Dallas Fall Open", "start_date": "2026-11-06",
    "end_date": "2026-11-08", "section": "U2000", "venue": "Dallas, TX", "organizer": "DCC", "rounds": 5,
    "time_control": "G/90+30", "info_url": "https://dallaschess.example/fall-open",
    "entry_list_url": "https://dallaschess.example/fall-open/entries", "player_name": "Nakshwin Mehta",
}


def _raw(subject, body, sender="noreply@dallaschess.example", pgn_attachment=None, message_id="<m1@x>", html=False):
    msg = EmailMessage()
    msg["From"] = f"{'Dallas Chess' if 'dallaschess' in sender else 'Someone'} <{sender}>"
    msg["To"] = "nakshwin@example.com"
    msg["Subject"] = subject
    msg["Date"] = "Sat, 01 Nov 2026 10:00:00 -0500"
    msg["Message-ID"] = message_id
    if html:
        msg.set_content(body, subtype="html")
    else:
        msg.set_content(body)
    if pgn_attachment:
        msg.add_attachment(pgn_attachment.encode(), maintype="application", subtype="x-chess-pgn", filename="game.pgn")
    return msg.as_bytes()


def _loc(resp):
    return unquote_plus(resp.headers["location"])


def _setup_user(app_module, client, username="nak"):
    uid = app_module.create_user(username, "correct-horse", role="viewer", status="active")
    app_module.update_user_profile(uid, "31625896", None, 1850, None, None, None, None, None, "Mehta, Nakshwin")
    login(client, username, "correct-horse")
    return uid


def _connect(app_module, client, monkeypatch, messages):
    monkeypatch.setattr(app_module.mr, "test_connection", lambda *a: "")
    monkeypatch.setattr(app_module, "_fetch_new_messages", lambda account: [m for m in messages if m["uid"] > account["last_uid"]])
    monkeypatch.setattr(app_module, "_extract_registration",
                        lambda subject, sender, text: dict(REGISTRATION) if "registration" in subject.lower() else None)
    return client.post("/my-tournaments/email/connect",
                       data={"email_address": "nakshwin@gmail.com", "password": "abcd efgh ijkl mnop"}, follow_redirects=False)


# --- mail_reader ---------------------------------------------------------------

def test_mail_reader_parses_text_html_and_pgn_attachments(app_module):
    mr = app_module.mr
    parsed = mr.parse_message(_raw("Your ChessNoteR game", "Attached is your game.", sender="games@chessnoter.com",
                                   pgn_attachment=CHESSNOTER_PGN), uid=7)
    assert parsed["uid"] == 7 and parsed["from_addr"] == "games@chessnoter.com"
    assert parsed["subject"] == "Your ChessNoteR game" and parsed["received_at"] == "2026-11-01 15:00:00"
    assert len(parsed["pgns"]) == 1 and '[White "Mehta, Nakshwin"]' in parsed["pgns"][0]
    assert parsed["attachments"] == ["game.pgn"] and mr.looks_chess_related(parsed)

    inline = mr.parse_message(_raw("Game record", "Here is the game:\n\n" + CHESSNOTER_PGN), uid=8)
    assert len(inline["pgns"]) == 1 and inline["pgns"][0].startswith('[Event "Dallas Fall Open"]')

    html = mr.parse_message(_raw("Registration confirmed", "<html><body><p>You are <b>registered</b> for the Dallas Fall Open.</p><p>Section: U2000</p></body></html>", html=True), uid=9)
    assert "registered for the Dallas Fall Open" in html["text"] and "Section: U2000" in html["text"]

    boring = mr.parse_message(_raw("Your package shipped", "Track your order at example.com", sender="shop@example.com"), uid=10)
    assert not mr.looks_chess_related(boring)

    assert mr.guess_imap("x@gmail.com") == ("imap.gmail.com", 993)
    assert mr.guess_imap("x@unknown-host.org") == (None, None)
    assert not mr.password_imap_supported("x@outlook.com") and mr.password_imap_supported("x@yahoo.com")

    token = mr.encrypt_secret("k1", "app-pass")
    assert token != "app-pass" and mr.decrypt_secret("k1", token) == "app-pass"
    try:
        mr.decrypt_secret("other-key", token)
        assert False, "wrong key must not decrypt"
    except ValueError:
        pass


# --- connect / poll ------------------------------------------------------------

def test_connect_mailbox_stores_encrypted_password_and_polls(client, app_module, monkeypatch):
    uid = _setup_user(app_module, client)
    anon = app_module  # noqa: F841  (fixture shape guard)
    messages = [
        app_module.mr.parse_message(_raw("Registration confirmation - Dallas Fall Open",
                                         "You are registered in the U2000 section. Entries: https://dallaschess.example/fall-open/entries",
                                         message_id="<reg@x>"), uid=101),
        app_module.mr.parse_message(_raw("Your ChessNoteR game", "Game attached.", sender="games@chessnoter.com",
                                         pgn_attachment=CHESSNOTER_PGN, message_id="<pgn@x>"), uid=102),
        app_module.mr.parse_message(_raw("Weekend plans", "Dinner at 7?", sender="friend@example.com", message_id="<p@x>"), uid=103),
        app_module.mr.parse_message(_raw("Blitz game", "Stray game.", sender="games@chessnoter.com",
                                         pgn_attachment=STRAY_PGN, message_id="<stray@x>"), uid=104),
    ]
    resp = _connect(app_module, client, monkeypatch, messages)

    assert resp.status_code == 303 and "Mailbox connected" in _loc(resp)
    account = app_module.get_email_account(uid)
    assert account["imap_host"] == "imap.gmail.com" and account["last_uid"] == 104 and account["messages_seen"] == 4
    assert account["password_enc"] != "abcd efgh ijkl mnop"
    assert app_module.mr.decrypt_secret("test-secret-key", account["password_enc"]) == "abcd efgh ijkl mnop"

    tracked = app_module.list_tracked_tournaments(uid)
    assert len(tracked) == 1
    t = tracked[0]
    assert (t["name"], t["start_date"], t["end_date"], t["section"]) == ("Dallas Fall Open", "2026-11-06", "2026-11-08", "U2000")
    assert t["entry_list_url"] == REGISTRATION["entry_list_url"] and t["rounds"] == 5

    games = app_module.list_tracked_games(t["id"])
    assert len(games) == 1
    g = games[0]
    assert (g["round"], g["played_on"], g["my_color"], g["opponent_name"], g["opponent_rating"], g["result"]) == \
        (1, "2026-11-07", "white", "Smith, John", 1702, "1-0")
    assert g["source"] == "email" and g["pgn_game_id"]

    unmatched = app_module.list_unmatched_games(uid)
    assert len(unmatched) == 1 and unmatched[0]["event"] == "Club Blitz"

    kinds = {m["subject"]: m["kind"] for m in app_module.list_email_messages(uid)}
    assert kinds == {"Registration confirmation - Dallas Fall Open": "registration", "Your ChessNoteR game": "pgn",
                     "Weekend plans": "other", "Blitz game": "pgn"}

    page = client.get("/my-tournaments")
    assert "nakshwin@gmail.com" in page.text and "Dallas Fall Open" in page.text and "Club Blitz" in page.text
    assert "abcd efgh" not in page.text

    # Checking again re-reads nothing new and duplicates nothing.
    again = client.post("/my-tournaments/email/check", follow_redirects=False)
    assert again.status_code == 303 and "0 new message" in _loc(again)
    assert len(app_module.list_tracked_tournaments(uid)) == 1 and len(app_module.list_tracked_games(t["id"])) == 1

    # Same registration mail re-delivered under a new uid is recognised, not duplicated.
    messages.append(app_module.mr.parse_message(_raw("Registration confirmation - Dallas Fall Open", "again", message_id="<reg2@x>"), uid=105))
    client.post("/my-tournaments/email/check")
    assert len(app_module.list_tracked_tournaments(uid)) == 1

    detail = client.get(f"/my-tournaments/{t['id']}")
    assert detail.status_code == 200 and "Smith, John" in detail.text and "Import entry list" in detail.text

    client.post("/my-tournaments/email/disconnect")
    assert app_module.get_email_account(uid) is None
    assert len(app_module.list_tracked_tournaments(uid)) == 1  # tracking survives disconnecting mail


def test_connect_rejects_outlook_and_bad_credentials(client, app_module, monkeypatch):
    _setup_user(app_module, client)
    resp = client.post("/my-tournaments/email/connect", data={"email_address": "x@outlook.com", "password": "p"}, follow_redirects=False)
    assert "password sign-in" in _loc(resp)

    monkeypatch.setattr(app_module.mr, "test_connection", lambda *a: "Sign-in failed: check the address and use an app password, not your normal password.")
    resp = client.post("/my-tournaments/email/connect", data={"email_address": "x@gmail.com", "password": "p"}, follow_redirects=False)
    assert "Sign-in failed" in _loc(resp)
    assert app_module.get_email_account(1) is None

    anon = app_module
    assert client.post("/logout").status_code in (200, 303)
    assert client.get("/my-tournaments", follow_redirects=False).status_code in (303, 401)


# --- entry list + prediction -----------------------------------------------------

def test_entry_list_import_predicts_round_one_opponent(client, app_module, monkeypatch):
    uid = _setup_user(app_module, client)
    tid, _ = app_module.create_tracked_tournament(uid, "Dallas Fall Open", "2026-11-06", "2026-11-08", "U2000")
    monkeypatch.setattr(app_module, "ANTHROPIC_API_KEY", "sk-ant-test")
    monkeypatch.setattr(app_module, "_fetch_page_text", _async_value("Advance entries ... (page text)"))
    rows = [{"name": n, "rating": r, "section": s} for n, r, s in [
        ("Alpha, A", 1990, "U2000"), ("Beta, B", 1950, "U2000"), ("Gamma, G", 1900, "U2000"),
        ("Mehta, Nakshwin", 1850, "U2000"), ("Delta, D", 1800, "U2000"), ("Epsilon, E", 1750, "U2000"),
        ("Zeta, Z", 1700, "U2000"), ("Eta, E", 1650, "U2000"), ("Grandmaster, Top", 2500, "Open"),
    ]]
    monkeypatch.setattr(app_module, "_claude_json", lambda prompt, text, max_tokens=4000, model="": {"tournament_name": "Dallas Fall Open 2026", "players": rows})

    resp = client.post(f"/my-tournaments/{tid}/entries", data={"entry_list_url": "https://dallaschess.example/fall-open/entries"}, follow_redirects=False)

    assert resp.status_code == 303 and "Imported 8 players" in _loc(resp) and "seed #4" in _loc(resp)
    t = app_module.get_tracked_tournament(tid)
    assert t["tournament_source"] == "https://dallaschess.example/fall-open/entries" and t["my_start_rank"] == 4
    assert app_module.get_research_player_count(t["tournament_source"]) == 8  # Open-section player filtered out

    page = client.get(f"/my-tournaments/{tid}")
    assert "seed <strong>#4</strong> of 8" in page.text
    assert "Round 1 likely opponent: seed #8" in page.text and "Eta, E" in page.text
    assert "/research-players/simulate?tournament_source=" in page.text

    # Re-import replaces rather than duplicates the list.
    client.post(f"/my-tournaments/{tid}/entries", data={})
    assert app_module.get_research_player_count(t["tournament_source"]) == 8


def _async_value(value):
    async def f(*a, **k):
        return value
    return f


# --- games: upload, attach, permissions -------------------------------------------

def test_upload_attach_and_ownership(client, app_module):
    uid = _setup_user(app_module, client)
    tid, _ = app_module.create_tracked_tournament(uid, "Dallas Fall Open", "2026-11-06", "2026-11-08")

    resp = client.post(f"/my-tournaments/{tid}/games/upload", data={"pgn_text": CHESSNOTER_PGN}, follow_redirects=False)
    assert "Added 1 game" in _loc(resp)
    games = app_module.list_tracked_games(tid)
    assert len(games) == 1 and games[0]["source"] == "upload" and games[0]["my_color"] == "white"

    # A stray game lands in the waiting list and can be attached by hand.
    app_module._ingest_pgn_games({"id": uid, "username": "nak"}, STRAY_PGN)
    waiting = app_module.list_unmatched_games(uid)
    assert len(waiting) == 1
    resp = client.post(f"/my-tournaments/{tid}/games/attach", data={"pgn_game_id": waiting[0]["pgn_game_id"]}, follow_redirects=False)
    assert "Game attached" in _loc(resp)
    assert len(app_module.list_tracked_games(tid)) == 2 and app_module.list_unmatched_games(uid) == []

    gid = app_module.list_tracked_games(tid)[1]["id"]
    client.post(f"/my-tournaments/{tid}/games/{gid}/remove")
    assert len(app_module.list_tracked_games(tid)) == 1

    bad = client.post(f"/my-tournaments/{tid}/games/upload", data={"pgn_text": "not pgn"}, follow_redirects=False)
    assert "look like PGN" in _loc(bad)

    # Another user cannot see or touch it.
    app_module.create_user("other", "correct-horse", role="viewer", status="active")
    client.post("/logout")
    login(client, "other", "correct-horse")
    assert client.get(f"/my-tournaments/{tid}").status_code == 404
    assert client.post(f"/my-tournaments/{tid}/dismiss").status_code == 404
    assert client.post(f"/my-tournaments/{tid}/games/upload", data={"pgn_text": CHESSNOTER_PGN}).status_code == 404

    client.post("/logout")
    login(client, "nak", "correct-horse")
    client.post(f"/my-tournaments/{tid}/dismiss")
    assert app_module.list_tracked_tournaments(uid) == []
    assert "Nothing tracked yet" in client.get("/my-tournaments").text


def test_manual_add_and_game_matching_by_event_name(client, app_module):
    uid = _setup_user(app_module, client)
    resp = client.post("/my-tournaments/add", data={"name": "Dallas Fall Open", "section": "U2000"}, follow_redirects=False)
    assert resp.status_code == 303 and _loc(resp).startswith("/my-tournaments/")
    tid = app_module.list_tracked_tournaments(uid)[0]["id"]

    # No dates known, so the game is matched by its Event tag instead.
    res = app_module._ingest_pgn_games({"id": uid, "username": "nak"}, CHESSNOTER_PGN)
    assert res == {"stored": 1, "attached": 1, "unmatched": 0}
    assert app_module.list_tracked_games(tid)[0]["opponent_name"] == "Smith, John"
    assert app_module.get_setting  # module sanity
