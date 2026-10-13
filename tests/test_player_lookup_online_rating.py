"""Player lookup (search suggestions + selected player card) shows the USCF
Online Regular rating alongside the over-the-board Live rating.

USCF tracks online-only ratings under separate ratingSystem/ratingSource
codes OR/OQ/OB (confirmed against the live ratings-api.uschess.org response
for real members), distinct from R/Q/B for over-the-board play. Neither the
lookup-suggestions dropdown nor the selected-player detail card surfaced OR
before this fix.
"""
import asyncio

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


def test_lookup_suggestions_show_online_rating_badge(client, app_module, monkeypatch):
    make_admin(app_module, "alice", "correct-horse")
    login(client, "alice", "correct-horse")

    async def fake_live_search(q):
        return []

    monkeypatch.setattr(app_module, "search_uscf_members", lambda q: [
        {"uscf_id": "111", "name": "DOE, JANE", "rating": 1500, "fide_id": None},
    ])
    monkeypatch.setattr(app_module, "_uscf_live_search", fake_live_search)
    monkeypatch.setattr(
        app_module, "_fetch_uscf_sections",
        lambda uscf_id: asyncio.sleep(0, result={"items": [{"ratingRecords": [
            {"ratingSource": "OR", "postRating": 1620},
        ]}]}),
    )

    resp = client.get("/api/player-lookup-search", params={"name": "doe"})

    assert resp.status_code == 200
    assert "Online 1620" in resp.text


def test_player_details_card_shows_online_rating(client, app_module, monkeypatch):
    make_admin(app_module, "bob", "correct-horse")
    login(client, "bob", "correct-horse")

    monkeypatch.setattr(app_module, "lookup_uscf_member", lambda uscf_id: {
        "name": "ROE, RICK", "fide_id": None, "expiry": "2099-12-31",
    })
    monkeypatch.setattr(
        app_module.httpx, "AsyncClient",
        lambda *a, **k: _FakeAsyncClient(
            member_data={"ratings": [{"ratingSystem": "R", "rating": 1700}]},
            sections_data={"items": [{"ratingRecords": [
                {"ratingSource": "R", "postRating": 1710},
                {"ratingSource": "OR", "postRating": 1680},
            ]}]},
        ),
    )

    resp = client.get("/api/player-details", params={"uscf_id": "222"})

    assert resp.status_code == 200
    assert "USCF Online Regular" in resp.text
    assert "1680" in resp.text


def test_player_details_card_shows_monthly_online_rating_and_renamed_otb_label(client, app_module, monkeypatch):
    make_admin(app_module, "carol", "correct-horse")
    login(client, "carol", "correct-horse")

    monkeypatch.setattr(app_module, "lookup_uscf_member", lambda uscf_id: {
        "name": "ROE, RICK", "fide_id": None, "expiry": "2099-12-31",
    })
    monkeypatch.setattr(
        app_module.httpx, "AsyncClient",
        lambda *a, **k: _FakeAsyncClient(
            member_data={"ratings": [
                {"ratingSystem": "R", "rating": 1700},
                {"ratingSystem": "OR", "rating": 1650},
            ]},
            sections_data={"items": [{"ratingRecords": [
                {"ratingSource": "R", "postRating": 1710},
                {"ratingSource": "OR", "postRating": 1680},
            ]}]},
        ),
    )

    resp = client.get("/api/player-details", params={"uscf_id": "333"})

    assert resp.status_code == 200
    assert "OTB USCF Monthly Regular" in resp.text
    assert "Monthly USCF Online Regular" in resp.text
    assert "1650" in resp.text
