"""Tests for the "Research this player" preview feature (beads):
pulling a player's public Lichess/Chess.com games and summarizing opening
tendencies as White and as Black, plus the crowdsourced reported/confirmed
online-ID workflow that feeds it.
"""
from tests.conftest import make_admin, login


LICHESS_PGN = """[Event "Rated Blitz game"]
[Site "https://lichess.org/abc123"]
[Date "2026.01.01"]
[White "myopponent"]
[Black "someoneelse"]
[Result "1-0"]
[ECO "B20"]
[Opening "Sicilian Defense"]

1. e4 c5 2. b4 1-0


[Event "Rated Blitz game"]
[Site "https://lichess.org/def456"]
[Date "2026.01.02"]
[White "someoneelse"]
[Black "myopponent"]
[Result "0-1"]
[ECO "B90"]
[Opening "Sicilian Defense: Najdorf Variation"]

1. e4 c5 2. Nf3 d6 0-1


[Event "Rated Blitz game"]
[Site "https://lichess.org/ghi789"]
[Date "2026.01.03"]
[White "myopponent"]
[Black "thirdplayer"]
[Result "1/2-1/2"]
[ECO "B20"]
[Opening "Sicilian Defense"]

1. e4 c5 2. b4 1/2-1/2
"""


class _FakeLichessResponse:
    status_code = 200

    def __init__(self, text):
        self.text = text


class _FakeLichessClient:
    def __init__(self, text):
        self._text = text

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def get(self, url, params=None, headers=None):
        return _FakeLichessResponse(self._text)


def test_player_research_summarizes_openings_by_color(client, app_module, monkeypatch):
    make_admin(app_module, "alice", "correct-horse")
    login(client, "alice", "correct-horse")
    monkeypatch.setattr(
        app_module.httpx, "AsyncClient",
        lambda *a, **k: _FakeLichessClient(LICHESS_PGN),
    )

    resp = client.get("/api/player-research", params={
        "name": "My Opponent", "lichess_username": "myopponent",
    })

    assert resp.status_code == 200
    assert "3 game(s) found" in resp.text
    assert "Sicilian Defense" in resp.text
    # 2 games as White (1 win, 1 draw -> 75%), 1 game as Black (1 win -> 100%)
    assert "75%" in resp.text
    assert "100%" in resp.text


def test_player_research_with_no_sources_prompts_for_username(client, app_module):
    make_admin(app_module, "bob", "correct-horse")
    login(client, "bob", "correct-horse")

    resp = client.get("/api/player-research", params={"name": "Nobody"})

    assert resp.status_code == 200
    assert "Report one below" in resp.text


def test_player_research_uses_confirmed_online_id_without_explicit_username(client, app_module, monkeypatch):
    make_admin(app_module, "carol", "correct-horse")
    login(client, "carol", "correct-horse")
    app_module.report_online_id("12345678", "lichess", "myopponent", None)
    uid = app_module.get_user_by_username("carol")["id"]
    entries = app_module.list_online_ids("12345678")
    app_module.set_online_id_status(entries[0]["id"], "confirmed")

    monkeypatch.setattr(
        app_module.httpx, "AsyncClient",
        lambda *a, **k: _FakeLichessClient(LICHESS_PGN),
    )

    resp = client.get("/api/player-research", params={"name": "My Opponent", "uscf_id": "12345678"})

    assert resp.status_code == 200
    assert "Lichess (myopponent)" in resp.text


def test_report_and_confirm_online_id_workflow(client, app_module):
    make_admin(app_module, "dave", "correct-horse")
    login(client, "dave", "correct-horse")

    resp = client.post("/api/player-online-ids", data={
        "uscf_id": "99999999", "platform": "chesscom", "username": "someplayer",
    })
    assert resp.status_code == 200
    assert "Reported" in resp.text
    assert "someplayer" in resp.text

    entries = app_module.list_online_ids("99999999")
    assert len(entries) == 1
    assert entries[0]["status"] == "reported"

    oid = entries[0]["id"]
    resp = client.post(f"/api/player-online-ids/{oid}/confirm", data={"uscf_id": "99999999"})
    assert resp.status_code == 200
    assert "Confirmed" in resp.text
    assert app_module.list_online_ids("99999999")[0]["status"] == "confirmed"


def test_non_admin_cannot_confirm_online_id(client, app_module):
    app_module.create_user("viewer1", "correct-horse", role="viewer", status="active")
    login(client, "viewer1", "correct-horse")
    app_module.report_online_id("11112222", "lichess", "guess1", None)
    oid = app_module.list_online_ids("11112222")[0]["id"]

    resp = client.post(f"/api/player-online-ids/{oid}/confirm", data={"uscf_id": "11112222"}, follow_redirects=False)

    assert resp.status_code in (302, 303, 401, 403)
    assert app_module.list_online_ids("11112222")[0]["status"] == "reported"


def test_duplicate_report_is_a_no_op(app_module):
    first = app_module.report_online_id("33334444", "lichess", "sameuser", None)
    second = app_module.report_online_id("33334444", "lichess", "sameuser", None)
    assert first is not None
    assert second is None
    assert len(app_module.list_online_ids("33334444")) == 1
