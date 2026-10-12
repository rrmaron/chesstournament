"""The "Load Quick & Blitz ratings" panel on the player lookup page showed
over-the-board USCF Quick/Blitz (ratingSystem/ratingSource Q/B) but not the
separate USCF Online Quick/Blitz ratings (OQ/OB), the same gap previously
fixed for Regular (OR) on the profile page and main player-details card.
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


def test_quick_blitz_panel_shows_live_online_quick_and_blitz(client, app_module, monkeypatch):
    make_admin(app_module, "alice", "correct-horse")
    login(client, "alice", "correct-horse")
    monkeypatch.setattr(
        app_module.httpx, "AsyncClient",
        lambda *a, **k: _FakeAsyncClient(
            member_data={"ratings": []},
            sections_data={"items": [{"ratingRecords": [
                {"ratingSource": "OQ", "postRating": 1590},
                {"ratingSource": "OB", "postRating": 1610},
                {"ratingSource": "Q", "postRating": 1500},
                {"ratingSource": "B", "postRating": 1520},
            ]}]},
        ),
    )

    resp = client.get("/api/player-quick-blitz", params={"uscf_id": "111"})

    assert resp.status_code == 200
    assert "USCF Online Quick" in resp.text
    assert "USCF Online Blitz" in resp.text
    assert "1590" in resp.text
    assert "1610" in resp.text


def test_quick_blitz_online_falls_back_to_monthly(client, app_module, monkeypatch):
    make_admin(app_module, "bob", "correct-horse")
    login(client, "bob", "correct-horse")
    monkeypatch.setattr(
        app_module.httpx, "AsyncClient",
        lambda *a, **k: _FakeAsyncClient(
            member_data={"ratings": [
                {"ratingSystem": "OQ", "rating": 1480},
                {"ratingSystem": "OB", "rating": 1495},
            ]},
            sections_data={"items": []},
        ),
    )

    resp = client.get("/api/player-quick-blitz", params={"uscf_id": "222"})

    assert resp.status_code == 200
    assert "1480" in resp.text
    assert "1495" in resp.text
