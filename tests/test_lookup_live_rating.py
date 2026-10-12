"""Player lookup suggestions show each player's online USCF rating."""
from tests.conftest import make_admin, login


def _setup(client, app_module, monkeypatch, live):
    make_admin(app_module, "alice", "correct-horse")
    login(client, "alice", "correct-horse")

    async def fake_live_search(q):
        return []

    async def fake_live_rating(uscf_id):
        return live.get(uscf_id, 0)

    async def fake_zero(uscf_id):
        return 0

    monkeypatch.setattr(app_module, "search_uscf_members", lambda q: [
        {"uscf_id": "111", "name": "DOE, JANE", "rating": 1500, "fide_id": None},
        {"uscf_id": "222", "name": "ROE, RICK", "rating": 1700, "fide_id": None},
    ])
    monkeypatch.setattr(app_module, "_uscf_live_search", fake_live_search)
    monkeypatch.setattr(app_module, "_live_regular_rating", fake_live_rating)
    monkeypatch.setattr(app_module, "_live_online_regular_rating", fake_zero)


def test_lookup_suggestions_include_live_rating(client, app_module, monkeypatch):
    _setup(client, app_module, monkeypatch, {"111": 1534})
    resp = client.get("/api/player-lookup-search", params={"name": "doe"})
    assert resp.status_code == 200
    assert "Live 1534" in resp.text
    # Player with no online rating gets no badge
    assert resp.text.count("Live ") == 1


def test_lookup_stores_live_and_monthly_ratings(client, app_module, monkeypatch):
    _setup(client, app_module, monkeypatch, {"111": 1534})
    client.get("/api/player-lookup-search", params={"name": "doe"})
    snap = app_module.get_uscf_rating_snapshot("111")
    assert snap["monthly_rating"] == 1500 and snap["live_rating"] == 1534
    # a later failed fetch (live=0) must not erase the stored live rating
    monkeypatch.setattr(app_module, "_live_regular_rating", lambda uid: _zero())
    client.get("/api/player-lookup-search", params={"name": "doe"})
    assert app_module.get_uscf_rating_snapshot("111")["live_rating"] == 1534


async def _zero():
    return 0
