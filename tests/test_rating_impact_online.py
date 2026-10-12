"""The "What will happen to my ratings if I play this player?" calculator
only compared over-the-board USCF/FIDE ratings -- it ignored the USCF
Online Regular/Quick/Blitz ratings entirely, even though both the user's
profile and the opponent's player-lookup card now carry them.
"""
from tests.conftest import make_admin, login


def test_rating_impact_includes_online_regular_column(client, app_module):
    make_admin(app_module, "alice", "correct-horse")
    login(client, "alice", "correct-horse")
    uid = app_module.get_user_by_username("alice")["id"]
    app_module.update_user_profile(
        uid, "11111111", None, None, None,
        uscf_online_rating=1500,
    )

    resp = client.get("/api/rating-impact", params={
        "opp_name": "Opponent", "opp_uscf_online": "1600",
    })

    assert resp.status_code == 200
    assert "USCF Online Regular" in resp.text
    assert "alice" in resp.text.lower() or "1500" in resp.text


def test_rating_impact_includes_online_quick_and_blitz_columns(client, app_module):
    make_admin(app_module, "bob", "correct-horse")
    login(client, "bob", "correct-horse")
    uid = app_module.get_user_by_username("bob")["id"]
    app_module.update_user_profile(
        uid, "22222222", None, None, None,
        uscf_online_quick_rating=1400, uscf_online_blitz_rating=1300,
    )

    resp = client.get("/api/rating-impact", params={
        "opp_name": "Opponent",
        "opp_uscf_online_quick": "1450",
        "opp_uscf_online_blitz": "1250",
    })

    assert resp.status_code == 200
    assert "USCF Online Quick" in resp.text
    assert "USCF Online Blitz" in resp.text


def test_rating_impact_omits_online_columns_when_no_data(client, app_module):
    make_admin(app_module, "carol", "correct-horse")
    login(client, "carol", "correct-horse")
    uid = app_module.get_user_by_username("carol")["id"]
    app_module.update_user_profile(uid, "33333333", None, 1500, None)

    resp = client.get("/api/rating-impact", params={"opp_name": "Opponent", "opp_uscf": "1550"})

    assert resp.status_code == 200
    assert "USCF Online Regular" not in resp.text


def test_player_details_card_exposes_online_rating_as_dataset_for_impact_calc(client, app_module, monkeypatch):
    make_admin(app_module, "dave", "correct-horse")
    login(client, "dave", "correct-horse")
    monkeypatch.setattr(app_module, "lookup_uscf_member", lambda uscf_id: {
        "name": "TEST PLAYER", "fide_id": None, "expiry": None,
    })

    class _FakeResponse:
        status_code = 200
        def json(self):
            return {"items": [{"ratingRecords": [{"ratingSource": "OR", "postRating": 1720}]}]}

    class _FakeClient:
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def get(self, *a, **k): return _FakeResponse()

    monkeypatch.setattr(app_module.httpx, "AsyncClient", lambda *a, **k: _FakeClient())

    resp = client.get("/api/player-details", params={"uscf_id": "44444444"})

    assert resp.status_code == 200
    assert 'data-opp-uscf-online="1720"' in resp.text
