"""Tests for the four P3 fixes (beads mychesspairings-445, -su1, -4lq, -arn)."""
import asyncio
import sys
import sqlite3

import pytest

import pgn_harvester as ph


# ---------------------------------------------------------------------------
# mychesspairings-445: trf_builder.build_trf leaks its SQLite connection
#
# trf_builder is reloaded fresh (against an isolated temp DB) by the
# app_module fixture, same as database/main — so it's fetched from
# sys.modules inside each test rather than imported at module scope, where
# it would bind to a stale copy pointed at the wrong DB_FILE.
# ---------------------------------------------------------------------------

class _TrackingConnection(sqlite3.Connection):
    closed = False
    def close(self):
        _TrackingConnection.closed = True
        super().close()


def test_build_trf_closes_connection_on_not_found(monkeypatch, app_module):
    trf_builder = sys.modules["trf_builder"]
    _TrackingConnection.closed = False
    real_connect = sqlite3.connect
    monkeypatch.setattr(
        trf_builder.sqlite3, "connect",
        lambda *a, **k: real_connect(*a, factory=_TrackingConnection, **k),
    )
    with pytest.raises(ValueError, match="Tournament not found"):
        trf_builder.build_trf(999999)
    assert _TrackingConnection.closed is True


def test_build_trf_closes_connection_on_success(monkeypatch, app_module):
    trf_builder = sys.modules["trf_builder"]
    _TrackingConnection.closed = False
    real_connect = sqlite3.connect
    monkeypatch.setattr(
        trf_builder.sqlite3, "connect",
        lambda *a, **k: real_connect(*a, factory=_TrackingConnection, **k),
    )
    tid = app_module.create_tournament("Open", rounds=3)
    text = trf_builder.build_trf(tid, rounds_to_include=0)
    assert "012 Open" in text
    assert _TrackingConnection.closed is True


# ---------------------------------------------------------------------------
# mychesspairings-su1: PGN dedupe hash collision on legitimate replays
# ---------------------------------------------------------------------------

def _headers(site=""):
    return {
        "Event": "Club Championship", "Round": "5", "Date": "2026.03.01",
        "White": "Smith, John", "Black": "Doe, Jane", "Site": site,
    }


def test_same_game_polled_twice_hashes_identically():
    """A live game re-fetched mid-game must still hash the same (Site is
    fixed at game creation, so repeated polling doesn't create duplicates)."""
    h1 = _headers(site="https://lichess.org/abcd1234")
    h2 = _headers(site="https://lichess.org/abcd1234")
    assert ph.make_game_hash(h1) == ph.make_game_hash(h2)


def test_distinct_replay_with_different_site_does_not_collide():
    """Same event/round/date/players (e.g. a same-day tiebreak replay) but a
    different per-game Site/ID must hash differently, so the real replay
    isn't silently dropped as a duplicate of the first game."""
    h1 = _headers(site="https://lichess.org/abcd1234")
    h2 = _headers(site="https://lichess.org/wxyz5678")
    assert ph.make_game_hash(h1) != ph.make_game_hash(h2)


def test_sources_without_a_unique_site_are_unaffected():
    """Documented scope limit: a source with no per-game Site value behaves
    exactly as before this fix (no new false-negative dedup introduced)."""
    h1 = _headers(site="")
    h2 = _headers(site="")
    assert ph.make_game_hash(h1) == ph.make_game_hash(h2)


# ---------------------------------------------------------------------------
# mychesspairings-4lq: Chess.com API fallback flattening rounds
# ---------------------------------------------------------------------------

def _api_game(round_id, white, black, result="1-0"):
    return {
        "white": {"name": white}, "black": {"name": black},
        "result": result, "roundId": round_id, "site": "chess.com",
        "startAt": "2026-03-01T12:00:00Z",
    }


def test_api_fallback_splits_games_by_round(monkeypatch):
    rounds = [{"id": 1, "slug": "1"}, {"id": 2, "slug": "2"}]
    api_games = [
        _api_game(1, "Alice", "Bob"),
        _api_game(1, "Carol", "Dave"),
        _api_game(2, "Alice", "Carol"),
    ]

    async def fake_source_url(room_id):
        return None  # simulate clono.no being unavailable

    monkeypatch.setattr(ph, "_chessbomb_source_url", fake_source_url)

    results = asyncio.run(ph.chesscom_event_pgns(123, rounds, api_games=api_games, event_name="Test Open"))

    assert len(results) == 2, "must produce one entry per round, not one flattened blob"
    names = [name for name, _ in results]
    assert any("Round 1" in n for n in names)
    assert any("Round 2" in n for n in names)

    round1_pgn = next(pgn for name, pgn in results if "Round 1" in name)
    round2_pgn = next(pgn for name, pgn in results if "Round 2" in name)
    assert round1_pgn.count('[Event ') == 2  # Alice/Bob + Carol/Dave
    assert round2_pgn.count('[Event ') == 1  # Alice/Carol only
    assert '[White "Alice"]' in round2_pgn and '[Black "Carol"]' in round2_pgn
    assert "Bob" not in round2_pgn and "Dave" not in round2_pgn


def test_api_fallback_round_label_discloses_missing_moves(monkeypatch):
    rounds = [{"id": 1, "slug": "1"}]
    api_games = [_api_game(1, "Alice", "Bob")]

    async def fake_source_url(room_id):
        return None

    monkeypatch.setattr(ph, "_chessbomb_source_url", fake_source_url)
    results = asyncio.run(ph.chesscom_event_pgns(123, rounds, api_games=api_games, event_name="Test Open"))
    assert len(results) == 1
    name, pgn_text = results[0]
    assert "no moves" in name.lower()
