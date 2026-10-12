"""Regression test: Profile page didn't show the USCF Online Regular rating.

Reported: "under my profile > ratings, I don't see the online uscf ratings" --
the live-refresh block in profile_page() fetches the USCF ratings-api member
and sections endpoints, but only ever looked at ratingSystem/ratingSource
"R"/"Q"/"B" (over-the-board Regular/Quick/Blitz). USCF's online-only ratings
use the separate codes "OR"/"OQ"/"OB" (confirmed against the live
ratings-api.uschess.org response for real players, e.g. Samuel Sevian,
Jeffery Xiong), so a player's Online Regular rating was silently dropped.

Fix: also track ratingSystem/ratingSource "OR", store it in a new
uscf_online_rating column, and surface it on the profile page/form.
"""
from tests.conftest import make_admin, login


class _FakeResponse:
    def __init__(self, data, status_code=200):
        self._data = data
        self.status_code = status_code

    def json(self):
        return self._data


class _FakeAsyncClient:
    def __init__(self, member_data, sections_data):
        self._member_data = member_data
        self._sections_data = sections_data

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def get(self, url, headers=None):
        if url.endswith("/sections"):
            return _FakeResponse(self._sections_data)
        return _FakeResponse(self._member_data)


def _patch_ratings_api(monkeypatch, app_module, member_data, sections_data):
    monkeypatch.setattr(
        app_module.httpx, "AsyncClient",
        lambda *a, **k: _FakeAsyncClient(member_data, sections_data),
    )


def test_profile_page_shows_live_online_regular_rating(client, app_module, monkeypatch):
    make_admin(app_module, "alice", "correct-horse")
    login(client, "alice", "correct-horse")
    app_module.update_user_profile(
        app_module.get_user_by_username("alice")["id"],
        "13493815", None, None, None,
    )
    _patch_ratings_api(
        monkeypatch, app_module,
        member_data={"ratings": []},
        sections_data={"items": [{"ratingRecords": [
            {"ratingSource": "OR", "postRating": 2644},
            {"ratingSource": "R", "postRating": 2600},
        ]}]},
    )

    resp = client.get("/profile")

    assert resp.status_code == 200
    assert "USCF Online Regular" in resp.text
    uid = app_module.get_user_by_username("alice")["id"]
    assert app_module.get_user_profile(uid)["uscf_online_rating"] == 2644


def test_profile_online_rating_falls_back_to_monthly_when_no_live_value(client, app_module, monkeypatch):
    make_admin(app_module, "bob", "correct-horse")
    login(client, "bob", "correct-horse")
    app_module.update_user_profile(
        app_module.get_user_by_username("bob")["id"],
        "99999999", None, None, None,
    )
    _patch_ratings_api(
        monkeypatch, app_module,
        member_data={"ratings": [{"ratingSystem": "OR", "rating": 1980}]},
        sections_data={"items": []},
    )

    client.get("/profile")

    uid = app_module.get_user_by_username("bob")["id"]
    assert app_module.get_user_profile(uid)["uscf_online_rating"] == 1980
