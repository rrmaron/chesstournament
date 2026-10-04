"""mychesspairings-859 (remainder): 72 route handlers with no internal `await`
were converted from `async def` to plain `def`, so FastAPI runs them in its
threadpool executor instead of blocking the single event loop thread on every
synchronous sqlite3 call. These tests exercise a representative path through
those converted routes end-to-end to catch anything the mechanical conversion
might have broken (e.g. a stray `await` call site left pointing at a function
that no longer returns a coroutine)."""
from tests.conftest import make_admin, login


def test_public_pages_render(client, app_module):
    assert client.get("/login").status_code == 200
    assert client.get("/register").status_code == 200
    assert client.get("/privacy").status_code == 200


def test_home_and_tournament_list_render(client, app_module):
    make_admin(app_module, "homeuser", "homepassword1")
    login(client, "homeuser", "homepassword1")
    assert client.get("/").status_code == 200
    assert client.get("/tournaments").status_code == 200


def test_td_golden_path_through_converted_routes(client, app_module):
    make_admin(app_module, "golden_td", "goldenpassword1", )
    login(client, "golden_td", "goldenpassword1")

    # new_tournament (converted) — create via the admin tournaments flow directly
    # against the DB to isolate this test from that route's own form contract.
    tid = app_module.create_tournament("Golden Path Open", rounds=3, entry_fee=0)

    # tournament_detail (converted)
    resp = client.get(f"/tournament/{tid}")
    assert resp.status_code == 200

    # register_player (converted) — TD manually adds two players
    resp = client.post(f"/tournament/{tid}/player", data={"name": "Alpha Player"}, follow_redirects=False)
    assert resp.status_code == 303
    resp = client.post(f"/tournament/{tid}/player", data={"name": "Beta Player"}, follow_redirects=False)
    assert resp.status_code == 303

    players = app_module.get_players(tid)
    assert len(players) == 2

    # entry_list_page (converted)
    resp = client.get(f"/tournament/{tid}/entries")
    assert resp.status_code == 200
    assert "Alpha Player" in resp.text

    # view_standings (converted)
    resp = client.get(f"/tournament/{tid}/standings")
    assert resp.status_code == 200

    # round_table_fragment (converted) as its own route, round 0 (no round yet)
    resp = client.get(f"/tournament/{tid}/round/0/table")
    assert resp.status_code == 200

    # withdraw_player / restore_player (converted)
    pid = players[0]["id"]
    resp = client.post(f"/player/{pid}/withdraw", follow_redirects=False)
    assert resp.status_code == 303
    assert app_module.get_player(pid)["status"] == "withdrawn"

    resp = client.post(f"/player/{pid}/restore", follow_redirects=False)
    assert resp.status_code == 303
    assert app_module.get_player(pid)["status"] == "active"

    # remove_player (converted)
    resp = client.post(f"/player/{pid}/delete", follow_redirects=False)
    assert resp.status_code == 303
    assert app_module.get_player(pid) is None


def test_admin_pages_render_for_admin(client, app_module):
    make_admin(app_module, "adminuser2", "adminpassword2")
    login(client, "adminuser2", "adminpassword2")
    assert client.get("/admin/tournaments").status_code == 200
    assert client.get("/admin/federations").status_code == 200
    assert client.get("/users").status_code == 200
