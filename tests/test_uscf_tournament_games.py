"""Regression test for the USCF calculator's "import tournament history" W/D/L bug.

Reported: on the USCF calculator page, importing a tournament's history no
longer filled in each game's Win/Draw/Loss status. Root cause: /api/uscf-
tournament-games has two backend paths -- the primary one (scraping
XtblMain.php via _parse_uscf_crosstable) returns g['result'] as a score
string ('1'/'0.5'/'0'), which is the convention every consumer in
uscf_calculator.html actually expects (the result <select> options,
parseFloat(g.result) score sums, bye detection). The fallback path
(_fetch_games_from_ratings_api, used when the crosstable scrape fails --
which it apparently was, triggering the bug) returned literal 'W'/'L'/'D'
instead, so none of those consumers recognized the result and the imported
games' W/D/L dropdowns came through unselected/blank.
"""
import asyncio

import pytest


class _FakeResponse:
    def __init__(self, data, status_code=200):
        self._data = data
        self.status_code = status_code

    def json(self):
        return self._data


class _FakeClient:
    def __init__(self, data):
        self._data = data

    async def get(self, url, headers=None):
        return _FakeResponse(self._data)


STANDINGS = {
    "items": [
        {
            "memberId": "12345678",
            "firstName": "Jane", "lastName": "Doe",
            "ratings": [{"ratingSystem": "R", "preRating": 1500, "postRating": 1520}],
            "roundOutcomes": [
                {"roundNumber": 1, "outcome": "Win", "opponentMemberId": "11111111",
                 "opponentFirstName": "Bob", "opponentLastName": "Smith"},
                {"roundNumber": 2, "outcome": "Loss", "opponentMemberId": "22222222",
                 "opponentFirstName": "Ann", "opponentLastName": "Lee"},
                {"roundNumber": 3, "outcome": "Draw", "opponentMemberId": "33333333",
                 "opponentFirstName": "Sam", "opponentLastName": "Park"},
            ],
        },
        {
            "memberId": "11111111", "firstName": "Bob", "lastName": "Smith",
            "ratings": [{"ratingSystem": "R", "preRating": 1400, "postRating": 1380}],
            "roundOutcomes": [],
        },
        {
            "memberId": "22222222", "firstName": "Ann", "lastName": "Lee",
            "ratings": [{"ratingSystem": "R", "preRating": 1600, "postRating": 1620}],
            "roundOutcomes": [],
        },
        {
            "memberId": "33333333", "firstName": "Sam", "lastName": "Park",
            "ratings": [{"ratingSystem": "R", "preRating": 1500, "postRating": 1500}],
            "roundOutcomes": [],
        },
    ]
}


def test_ratings_api_fallback_uses_score_string_results_not_letters(app_module):
    client = _FakeClient(STANDINGS)
    games = asyncio.run(app_module._fetch_games_from_ratings_api("evt1", 1, "12345678", client))

    assert len(games) == 3
    results = {g["uscfId"]: g["result"] for g in games}
    assert results == {"11111111": "1", "22222222": "0", "33333333": "0.5"}
    # Must never be the old letter format -- that's exactly what broke the
    # W/D/L dropdown in the calculator's imported games.
    assert all(r in ("1", "0.5", "0") for r in results.values())


def test_ratings_api_fallback_carries_opponent_pre_and_post_ratings(app_module):
    client = _FakeClient(STANDINGS)
    games = asyncio.run(app_module._fetch_games_from_ratings_api("evt1", 1, "12345678", client))
    bob = next(g for g in games if g["uscfId"] == "11111111")
    assert bob["rating"] == 1400
    assert bob["post_rating"] == 1380


def test_ratings_api_fallback_returns_empty_for_unknown_player(app_module):
    client = _FakeClient(STANDINGS)
    games = asyncio.run(app_module._fetch_games_from_ratings_api("evt1", 1, "99999999", client))
    assert games == []
