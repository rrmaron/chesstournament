"""Tests for the PGN harvest loop (beads mychesspairings-8jb, part 1 of 2).

_harvest_loop itself is an infinite `while True: await asyncio.sleep(60)`
task and isn't unit-testable directly. Its actual per-tick work was
extracted into two standalone async functions, _refresh_live_sources and
_discover_new_broadcasts, specifically so they could be exercised here
without the loop/sleep wrapper. This refactor is behavior-preserving --
_harvest_loop just calls them on the same schedule as before.
"""
import asyncio


def test_refresh_live_sources_imports_live_lichess_rounds(app_module, monkeypatch):
    calls = []
    monkeypatch.setattr(app_module, "list_live_pgn_sources", lambda: [
        {"id": 1, "source_type": "lichess", "lichess_round_id": "abcd1234", "source_url": "https://lichess.org/x"},
    ])
    async def fake_import_lichess_round(round_id, source_id, inserter, url):
        calls.append(("import", round_id, source_id, url))
    monkeypatch.setattr(app_module.ph, "import_lichess_round", fake_import_lichess_round)
    monkeypatch.setattr(app_module, "update_pgn_source_fetched", lambda sid, count: calls.append(("fetched", sid, count)))
    monkeypatch.setattr(app_module, "count_pgn_games_for_source", lambda sid: 5)

    asyncio.run(app_module._refresh_live_sources())

    assert ("import", "abcd1234", 1, "https://lichess.org/x") in calls
    assert ("fetched", 1, 5) in calls


def test_refresh_live_sources_skips_non_lichess_and_missing_round_id(app_module, monkeypatch):
    calls = []
    monkeypatch.setattr(app_module, "list_live_pgn_sources", lambda: [
        {"id": 1, "source_type": "chesscom", "lichess_round_id": None},
        {"id": 2, "source_type": "lichess", "lichess_round_id": None},
    ])
    async def fake_import_lichess_round(*a, **k):
        calls.append("import")
    monkeypatch.setattr(app_module.ph, "import_lichess_round", fake_import_lichess_round)
    monkeypatch.setattr(app_module, "update_pgn_source_fetched", lambda *a: calls.append("fetched"))

    asyncio.run(app_module._refresh_live_sources())
    assert calls == []


def test_refresh_live_sources_one_failure_does_not_block_the_rest(app_module, monkeypatch):
    calls = []
    monkeypatch.setattr(app_module, "list_live_pgn_sources", lambda: [
        {"id": 1, "source_type": "lichess", "lichess_round_id": "bad", "source_url": ""},
        {"id": 2, "source_type": "lichess", "lichess_round_id": "good", "source_url": ""},
    ])
    async def fake_import_lichess_round(round_id, source_id, inserter, url):
        if round_id == "bad":
            raise RuntimeError("network blew up")
        calls.append(("import", round_id))
    monkeypatch.setattr(app_module.ph, "import_lichess_round", fake_import_lichess_round)
    monkeypatch.setattr(app_module, "update_pgn_source_fetched", lambda sid, count: calls.append(("fetched", sid)))
    monkeypatch.setattr(app_module, "count_pgn_games_for_source", lambda sid: 0)

    asyncio.run(app_module._refresh_live_sources())  # must not raise

    assert ("import", "good") in calls
    assert ("fetched", 2) in calls
    assert not any(c[0] == "import" and c[1] == "bad" for c in calls)


def test_discover_new_broadcasts_skips_organizers_without_lichess_id(app_module, monkeypatch):
    calls = []
    monkeypatch.setattr(app_module, "list_pgn_organizers", lambda: [{"id": 1, "lichess_id": None}])
    async def fake_broadcasts(lichess_id):
        calls.append("called")
        return []
    monkeypatch.setattr(app_module.ph, "lichess_organizer_broadcasts", fake_broadcasts)

    asyncio.run(app_module._discover_new_broadcasts())
    assert calls == []


def test_discover_new_broadcasts_imports_a_new_round(app_module, monkeypatch):
    calls = []
    monkeypatch.setattr(app_module, "list_pgn_organizers", lambda: [{"id": 1, "lichess_id": "org1"}])

    async def fake_broadcasts(lichess_id):
        return [{"tour": {"id": "tour1", "name": "Big Open"}}]
    monkeypatch.setattr(app_module.ph, "lichess_organizer_broadcasts", fake_broadcasts)

    async def fake_broadcast_info(tour_id):
        return {"name": "Big Open", "rounds": [
            {"id": "round1", "name": "Round 3", "finished": False, "url": "https://lichess.org/broadcast/tour1/round1"},
        ]}
    monkeypatch.setattr(app_module.ph, "lichess_broadcast_info", fake_broadcast_info)
    monkeypatch.setattr(app_module, "lichess_round_already_known", lambda rid: False)
    monkeypatch.setattr(app_module, "upsert_pgn_source", lambda *a, **k: calls.append(("upsert", a)) or 99)

    async def fake_import_lichess_round(round_id, source_id, inserter, url):
        calls.append(("import", round_id, source_id))
    monkeypatch.setattr(app_module.ph, "import_lichess_round", fake_import_lichess_round)
    monkeypatch.setattr(app_module, "update_pgn_source_fetched", lambda sid, count: calls.append(("fetched", sid)))
    monkeypatch.setattr(app_module, "count_pgn_games_for_source", lambda sid: 0)

    asyncio.run(app_module._discover_new_broadcasts())

    assert any(c[0] == "upsert" for c in calls)
    assert ("import", "round1", 99) in calls
    assert ("fetched", 99) in calls


def test_discover_new_broadcasts_skips_already_known_rounds(app_module, monkeypatch):
    calls = []
    monkeypatch.setattr(app_module, "list_pgn_organizers", lambda: [{"id": 1, "lichess_id": "org1"}])

    async def fake_broadcasts(lichess_id):
        return [{"tour": {"id": "tour1", "name": "Big Open"}}]
    monkeypatch.setattr(app_module.ph, "lichess_organizer_broadcasts", fake_broadcasts)

    async def fake_broadcast_info(tour_id):
        return {"name": "Big Open", "rounds": [{"id": "already-known", "name": "Round 1"}]}
    monkeypatch.setattr(app_module.ph, "lichess_broadcast_info", fake_broadcast_info)
    monkeypatch.setattr(app_module, "lichess_round_already_known", lambda rid: True)
    monkeypatch.setattr(app_module, "upsert_pgn_source", lambda *a, **k: calls.append("upsert"))

    asyncio.run(app_module._discover_new_broadcasts())
    assert calls == []


def test_discover_new_broadcasts_one_organizer_failure_does_not_propagate(app_module, monkeypatch):
    monkeypatch.setattr(app_module, "list_pgn_organizers", lambda: [{"id": 1, "lichess_id": "org1"}])

    async def fake_broadcasts(lichess_id):
        raise RuntimeError("lichess API is down")
    monkeypatch.setattr(app_module.ph, "lichess_organizer_broadcasts", fake_broadcasts)

    asyncio.run(app_module._discover_new_broadcasts())  # must not raise
