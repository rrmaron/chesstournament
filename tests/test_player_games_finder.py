"""'Find all games' for a player: pulls from the local archive (incl. TWIC
imports), Lichess/Chess.com by username, Lichess broadcast search and user
uploads; dedupes into pgn_games; links games to the player as confirmed or
probable; coaches/admins confirm or reject; everything downloads as one PGN.
"""
import asyncio

from tests.conftest import make_admin, login

ARCHIVE_PGN = """[Event "Club Championship"]
[Site "Dallas"]
[Date "2026.05.01"]
[Round "1"]
[White "Mehta, Nakshwin"]
[Black "Smith, John"]
[Result "1-0"]
[ECO "C50"]
[Opening "Italian Game"]

1. e4 e5 2. Nf3 Nc6 3. Bc4 1-0

[Event "Club Championship"]
[Site "Dallas"]
[Date "2026.05.02"]
[Round "2"]
[White "Mehta, Nikhil"]
[Black "Jones, Amy"]
[Result "0-1"]

1. d4 d5 0-1
"""

LICHESS_PGN = """[Event "Rated Blitz game"]
[Site "https://lichess.org/abcd1234"]
[Date "2026.06.01"]
[White "nmehta"]
[Black "someone"]
[Result "1/2-1/2"]
[ECO "B20"]
[Opening "Sicilian Defense"]

1. e4 c5 1/2-1/2
"""


def _make_coach(app_module, username="coachk"):
    return app_module.create_user(username, "correct-horse", role="coach", status="active")


def _quiet_network(app_module, monkeypatch, lichess_pgn=""):
    async def lichess(username, max_games=300):
        return lichess_pgn

    async def nothing(*a, **k):
        return ""

    async def no_tours(*a, **k):
        return []

    async def no_suggestions(name):
        return {"lichess": [], "chesscom": []}

    monkeypatch.setattr(app_module.ph, "lichess_user_games_pgn", lichess)
    monkeypatch.setattr(app_module.ph, "chesscom_user_games_pgn", nothing)
    monkeypatch.setattr(app_module.ph, "lichess_search_broadcasts", no_tours)
    monkeypatch.setattr(app_module, "_suggest_online_ids", no_suggestions)


def test_name_matching_handles_uscf_and_pgn_name_styles(app_module):
    m = app_module._name_matches
    assert m("MEHTA, NAKSHWIN", "Mehta, Nakshwin")
    assert m("Nakshwin Mehta", "Mehta, N")
    assert m("Mehta, Nakshwin", "GM Nakshwin Mehta")
    assert not m("Mehta, Nakshwin", "Mehta, Nikhil")
    assert not m("Mehta, Nakshwin", "Smith, Nakshwin")
    assert app_module._player_key("12345678", "", "x") == "uscf:12345678"
    assert app_module._player_key("", "2000", "x") == "fide:2000"
    assert app_module._player_key("", "", "Mehta, Nakshwin") == "name:mehtanakshwin"
    assert "nakshwinmehta" in app_module._guess_usernames("Mehta, Nakshwin")


def test_find_links_archive_by_name_and_lichess_by_username(client, app_module, monkeypatch):
    make_admin(app_module, "root", "correct-horse")
    login(client, "root", "correct-horse")
    sid = app_module.ensure_pgn_source("upload", "seed", "Seed")
    app_module._store_pgn_text(ARCHIVE_PGN, sid)
    _quiet_network(app_module, monkeypatch, lichess_pgn=LICHESS_PGN)

    resp = client.post("/api/player-games/find", data={
        "name": "MEHTA, NAKSHWIN", "uscf_id": "31625896", "lichess_username": "nmehta",
    })

    assert resp.status_code == 200
    assert "Games archive: 1" in resp.text
    assert "Lichess (nmehta): 1" in resp.text
    assert "Mehta, Nakshwin" in resp.text and "nmehta" in resp.text
    assert "Mehta, Nikhil" not in resp.text
    games = app_module.list_player_games("uscf:31625896")
    assert {(g["white"], g["status"]) for g in games} == {("Mehta, Nakshwin", "probable"), ("nmehta", "confirmed")}

    again = client.post("/api/player-games/find", data={"name": "MEHTA, NAKSHWIN", "uscf_id": "31625896",
                                                        "lichess_username": "nmehta"})
    assert len(app_module.list_player_games("uscf:31625896")) == 2
    assert "(+" not in again.text.split("Searched:")[1].split("</div>")[0]

    download = client.get("/api/player-games/download", params={"name": "MEHTA, NAKSHWIN", "uscf_id": "31625896"})
    assert download.status_code == 200
    assert download.headers["content-disposition"].endswith('MEHTA_NAKSHWIN_games.pgn"')
    assert download.text.count("[Event ") == 2

    research = client.get("/api/player-research", params={"name": "MEHTA, NAKSHWIN", "uscf_id": "31625896"})
    assert "Found games (2)" in research.text
    assert "2 game(s) found" in research.text


def test_review_status_requires_coach_or_admin_and_excludes_rejected(client, app_module, monkeypatch):
    make_admin(app_module, "root", "correct-horse")
    app_module.create_user("viewer1", "correct-horse", role="viewer", status="active")
    sid = app_module.ensure_pgn_source("upload", "seed", "Seed")
    app_module._store_pgn_text(ARCHIVE_PGN, sid)
    _quiet_network(app_module, monkeypatch)

    login(client, "viewer1", "correct-horse")
    client.post("/api/player-games/find", data={"name": "Mehta, Nakshwin", "uscf_id": "31625896"})
    link = app_module.list_player_games("uscf:31625896")[0]
    denied = client.post(f"/api/player-games/{link['link_id']}/status",
                         data={"status": "rejected", "name": "Mehta, Nakshwin", "uscf_id": "31625896"})
    assert denied.status_code == 403
    assert "✓" not in client.get("/api/player-games", params={"name": "Mehta, Nakshwin", "uscf_id": "31625896"}).text

    client.post("/logout")
    login(client, "root", "correct-horse")
    page = client.get("/api/player-games", params={"name": "Mehta, Nakshwin", "uscf_id": "31625896"})
    assert "✓" in page.text
    resp = client.post(f"/api/player-games/{link['link_id']}/status",
                       data={"status": "rejected", "name": "Mehta, Nakshwin", "uscf_id": "31625896"})
    assert resp.status_code == 200
    assert "rejected" in resp.text
    assert app_module.list_player_games("uscf:31625896") == []
    assert client.get("/api/player-games/download", params={"name": "Mehta, Nakshwin", "uscf_id": "31625896"}).status_code == 404

    wrong_player = client.post(f"/api/player-games/{link['link_id']}/status",
                               data={"status": "confirmed", "name": "Other", "uscf_id": "999"})
    assert wrong_player.status_code == 404


def test_upload_attaches_matching_games_as_probable(client, app_module, monkeypatch):
    _make_coach(app_module)
    login(client, "coachk", "correct-horse")

    resp = client.post("/api/player-games/upload",
                       data={"name": "Mehta, Nakshwin", "uscf_id": "31625896"},
                       files={"pgn_file": ("games.pgn", ARCHIVE_PGN.encode(), "text/plain")})

    assert resp.status_code == 200
    assert "Your upload: 1" in resp.text
    games = app_module.list_player_games("uscf:31625896")
    assert len(games) == 1 and games[0]["status"] == "probable" and games[0]["reason"] == "upload"
    assert games[0]["source_type"] == "upload"


def test_twic_import_stores_games_and_tracks_last_issue(client, app_module, monkeypatch):
    async def fake_issue(n):
        return ARCHIVE_PGN if n == 1666 else ""

    monkeypatch.setattr(app_module.ph, "twic_issue_pgn", fake_issue)

    stored = asyncio.run(app_module._twic_import_issue(1666))

    assert stored == 2
    assert app_module.get_setting("twic_last_issue") == "1666"
    assert app_module.count_pgn_games() == 2
    assert asyncio.run(app_module._twic_import_issue(1666)) == 2  # re-import is a no-op (dedup by hash)
    assert app_module.count_pgn_games() == 2
    source = [s for s in app_module.list_pgn_sources() if s["source_type"] == "twic"][0]
    assert source["tournament_name"] == "TWIC 1666" and source["game_count"] == 2


def test_twic_admin_controls(client, app_module, monkeypatch):
    make_admin(app_module, "root", "correct-horse")
    app_module.create_user("viewer1", "correct-horse", role="viewer", status="active")
    started = {}

    async def fake_range(start, end):
        started.update(start=start, end=end)

    async def latest():
        return 1666

    monkeypatch.setattr(app_module, "_twic_import_range", fake_range)
    monkeypatch.setattr(app_module.ph, "twic_latest_issue", latest)

    login(client, "viewer1", "correct-horse")
    assert client.post("/admin/twic/import", data={"from_issue": "1660"}).status_code == 403
    assert "The Week in Chess archive" not in client.get("/pgn-database").text

    client.post("/logout")
    login(client, "root", "correct-horse")
    assert "The Week in Chess archive" in client.get("/pgn-database").text
    resp = client.post("/admin/twic/import", data={"from_issue": "1500", "to_issue": "0"}, follow_redirects=False)
    assert resp.status_code == 303
    assert started == {"start": 1500, "end": 1559}  # capped at 60 issues per run

    client.post("/admin/twic/auto", data={"enabled": "1"})
    assert app_module.get_setting("twic_auto") == "1"
