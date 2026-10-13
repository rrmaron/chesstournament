"""Admins can bulk-upload a PGN file (e.g. a ChessBase export) into the games
archive, where it becomes searchable by "Find all games".
"""
from tests.conftest import make_admin, login
from tests.test_player_games_finder import ARCHIVE_PGN, _quiet_network


def test_admin_pgn_file_import_feeds_find_all_games(client, app_module, monkeypatch):
    make_admin(app_module, "root", "correct-horse")
    app_module.create_user("viewer1", "correct-horse", role="viewer", status="active")
    _quiet_network(app_module, monkeypatch)

    login(client, "viewer1", "correct-horse")
    denied = client.post("/pgn-database/import-file", data={"label": "x"},
                         files={"pgn_file": ("mega.pgn", ARCHIVE_PGN.encode(), "text/plain")})
    assert denied.status_code == 403
    assert "Import PGN file" not in client.get("/pgn-database").text

    client.post("/logout")
    login(client, "root", "correct-horse")
    assert "Import PGN file" in client.get("/pgn-database").text
    resp = client.post("/pgn-database/import-file", data={"label": "ChessBase Mega - Mehta"},
                       files={"pgn_file": ("mega.pgn", ARCHIVE_PGN.encode("latin-1"), "text/plain")},
                       follow_redirects=False)

    assert resp.status_code == 303
    assert "2%20game%28s%29%20imported%20from%20ChessBase%20Mega%20-%20Mehta" in resp.headers["location"]
    assert app_module.count_pgn_games() == 2
    page = client.get(resp.headers["location"])
    assert "2 game(s) imported from ChessBase Mega - Mehta" in page.text

    found = client.post("/api/player-games/find", data={"name": "Mehta, Nakshwin", "uscf_id": "31625896"})
    assert "Games archive: 1" in found.text
    assert "Mehta, Nakshwin" in found.text
