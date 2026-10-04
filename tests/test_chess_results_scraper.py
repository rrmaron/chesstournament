"""Tests for main.py's chess-results.com scraper (beads mychesspairings-9z3).

Uses the saved fixture at tests/fixtures/chess_results_players.html (a
synthetic reconstruction of the page shape the parser's own docstring/column
comments describe — see that file's header comment) instead of hitting the
live site, so a chess-results.com markup change breaks a fast local test
rather than silently importing zero/wrong players in production.
"""
import asyncio
from pathlib import Path

import pytest

FIXTURE = (Path(__file__).parent / "fixtures" / "chess_results_players.html").read_text(encoding="utf-8")


class _FakeResponse:
    def __init__(self, text, status_code=200):
        self.text = text
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class _FakeAsyncClient:
    def __init__(self, response, calls):
        self._response = response
        self._calls = calls

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def get(self, url, headers=None):
        self._calls.append(url)
        return self._response


def _patch_get(monkeypatch, app_module, html, status_code=200):
    calls = []
    monkeypatch.setattr(
        app_module.httpx, "AsyncClient",
        lambda *a, **k: _FakeAsyncClient(_FakeResponse(html, status_code), calls),
    )
    return calls


def test_scrapes_tournament_name_and_players_from_fixture(app_module, monkeypatch):
    _patch_get(monkeypatch, app_module, FIXTURE)

    name, players = asyncio.run(app_module._scrape_chess_results("https://chess-results.com/tnr123.aspx?lan=1"))

    assert name == "Spring Open 2026"
    assert [p["name"] for p in players] == [
        "Carlsen, Magnus", "Doe, Jane", "Nondigit Fide", "No Rating Player",
    ]


def test_skips_rows_missing_name_or_fide_id_or_too_few_cells(app_module, monkeypatch):
    _patch_get(monkeypatch, app_module, FIXTURE)
    _, players = asyncio.run(app_module._scrape_chess_results("https://chess-results.com/tnr123.aspx"))
    # Rows 3 (blank name), 4 (blank FideID) and 5 (only 3 cells) must all be absent.
    assert len(players) == 4
    assert "" not in [p["name"] for p in players]


def test_start_rank_is_sequential_over_included_players_only(app_module, monkeypatch):
    _patch_get(monkeypatch, app_module, FIXTURE)
    _, players = asyncio.run(app_module._scrape_chess_results("https://chess-results.com/tnr123.aspx"))
    assert [p["start_rank"] for p in players] == [1, 2, 3, 4]


def test_fide_id_strips_non_digit_characters(app_module, monkeypatch):
    _patch_get(monkeypatch, app_module, FIXTURE)
    _, players = asyncio.run(app_module._scrape_chess_results("https://chess-results.com/tnr123.aspx"))
    nondigit_player = next(p for p in players if p["name"] == "Nondigit Fide")
    assert nondigit_player["fide_id"] == "9999999"


def test_non_numeric_rating_becomes_zero_not_a_crash(app_module, monkeypatch):
    _patch_get(monkeypatch, app_module, FIXTURE)
    _, players = asyncio.run(app_module._scrape_chess_results("https://chess-results.com/tnr123.aspx"))
    no_rating_player = next(p for p in players if p["name"] == "No Rating Player")
    assert no_rating_player["fide_rating"] == 0


def test_valid_rating_is_parsed_as_int(app_module, monkeypatch):
    _patch_get(monkeypatch, app_module, FIXTURE)
    _, players = asyncio.run(app_module._scrape_chess_results("https://chess-results.com/tnr123.aspx"))
    carlsen = next(p for p in players if p["name"] == "Carlsen, Magnus")
    assert carlsen["fide_rating"] == 2830
    assert carlsen["title"] == "GM"
    assert carlsen["country"] == "NOR"


def test_raises_when_no_player_table_is_present(app_module, monkeypatch):
    _patch_get(monkeypatch, app_module, "<html><head><title>Empty Page</title></head><body>no table here</body></html>")
    with pytest.raises(ValueError, match="Could not find player table"):
        asyncio.run(app_module._scrape_chess_results("https://chess-results.com/tnr999.aspx"))


def test_http_errors_propagate(app_module, monkeypatch):
    _patch_get(monkeypatch, app_module, FIXTURE, status_code=404)
    with pytest.raises(RuntimeError, match="404"):
        asyncio.run(app_module._scrape_chess_results("https://chess-results.com/tnr404.aspx"))


def test_fetch_url_forces_the_print_view_params(app_module, monkeypatch):
    """The scraper must always request art=0&prt=1 (the starting-rank print
    view), stripping any pre-existing art=N from the caller's URL."""
    calls = _patch_get(monkeypatch, app_module, FIXTURE)
    asyncio.run(app_module._scrape_chess_results("https://chess-results.com/tnr123.aspx?lan=1&art=2"))
    assert len(calls) == 1
    fetched = calls[0]
    assert fetched.startswith("https://chess-results.com/tnr123.aspx?")
    assert "art=0" in fetched
    assert "prt=1" in fetched
    assert "art=2" not in fetched
