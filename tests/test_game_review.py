"""Post-game review: engine analysis + Manhattan chart, mistake/clock patterns,
annotated PGN, AI coach notes, opponent scouting, season dashboard and the
annotated-PGN import. The engine and Claude are faked; one test runs the real
Stockfish when it is installed."""
import types

import chess
import chess.engine
import pytest

import game_review as gr
from tests.conftest import login

GAME = """[Event "Saturday Swiss Plano"]
[Site "Plano, TX"]
[Date "2026.10.18"]
[Round "2"]
[White "Mehta, Nakshwin"]
[Black "Karry, Tapasya"]
[Result "1-0"]
[WhiteElo "1682"]
[BlackElo "1588"]
[TimeControl "g30/d5"]


1. e4 {[%emt 0:00:01]} e5 {[%emt 0:00:02]} 2. Nf3 {[%emt 0:00:03]} Nc6 {[%emt 0:00:04]}
3. Bc4 {[%emt 0:00:05]} Nf6 {[%emt 0:00:06]} 4. Ng5 {[%emt 0:00:07]} d5 {[%emt 0:00:08]}
5. exd5 {[%emt 0:00:09]} Nxd5 {[%emt 0:06:00]} 6. Nxf7 {[%emt 0:00:11]} Kxf7 {[%emt 0:00:03]}
7. Qf3+ {[%emt 0:00:13]} Ke6 {[%emt 0:00:14]} 8. Nc3 {[%emt 0:00:15]} 1-0
"""

# White-view evaluation of each position, indexed by the number of moves played.
EVALS = [30] * 12 + [400, 420, 410, 430]


class FakeEngine:
    """Scores positions from EVALS and always 'prefers' a legal move other than
    the one actually played, so every move is graded by the eval drop alone."""

    def __init__(self, pgn=GAME, evals=EVALS):
        game = gr.read_game(pgn)
        self.next_move = {}
        b = game.board()
        for mv in game.mainline_moves():
            self.next_move[b.fen()] = mv
            b.push(mv)
        self.evals = evals

    def analyse(self, board, limit, multipv=1):
        played = self.next_move.get(board.fen())
        alt = next(m for m in sorted(board.legal_moves, key=lambda m: m.uci()) if m != played)
        cp = self.evals[min(board.ply(), len(self.evals) - 1)]
        return [{"score": chess.engine.PovScore(chess.engine.Cp(cp), chess.WHITE), "pv": [alt]}]


def _analysis():
    return gr.analyse_game(GAME, FakeEngine(), depth=5)


# ---------------------------------------------------------------------------
# Pure logic
# ---------------------------------------------------------------------------

def test_reads_chessnoter_blank_line_quirk_and_think_times():
    game = gr.read_game(GAME)
    assert len(list(game.mainline_moves())) == 15
    times = gr.think_times(game)
    assert times[9] == 360 and times[11] == 3


def test_think_times_from_clock_tags_use_the_increment():
    pgn = """[Event "x"]
[White "a"]
[Black "b"]
[TimeControl "5400+30"]

1. d4 {[%clk 1:30:30]} d5 {[%clk 1:30:00]} 2. c4 {[%clk 1:25:30]} e6 {[%clk 1:20:30]} *"""
    times = gr.think_times(gr.read_game(pgn))
    assert times[:2] == [None, None]
    assert times[2] == 5 * 60 + 30   # 1:30:30 -> 1:25:30 plus 30s increment
    assert times[3] == 9 * 60 + 60


def test_engine_review_flags_the_blunder_and_the_instant_after_long_think_pattern():
    a = _analysis()
    kxf7 = a["plies"][11]
    assert kxf7["label"] == "6...Kxf7" and kxf7["kind"] == "blunder" and kxf7["cp_loss"] == 370
    assert a["summary"]["black"]["blunders"] == 1 and a["summary"]["white"]["blunders"] == 0
    top = a["moments"][0]
    assert top["label"] == "6...Kxf7"
    assert "instant move right after a long think" in top["tags"]
    assert a["summary"]["black"]["longest_think"] == (360, "5...Nxd5")
    assert a["chart"]["values"][11] == 4.0 and len(a["chart"]["labels"]) == 15
    assert a["castled"] == {"white": None, "black": None}


def test_chart_clips_at_six_pawns_and_mates_format():
    plies = [{"label": "1.e4", "eval": 2500, "kind": ""}, {"label": "1...e5", "eval": -gr.MATE_CP + 3, "kind": "blunder"}]
    assert gr.chart_data(plies)["values"] == [6.0, -6.0]
    assert gr.fmt_eval(gr.MATE_CP - 2) == "#2" and gr.fmt_eval(-gr.MATE_CP + 3) == "#-3"
    assert gr.fmt_eval(-123) == "-1.23"


def test_classification_uses_centipawns_while_the_game_is_live():
    # Throwing away -3.7 for -0.3 is the move that lost the game: a blunder.
    assert gr.classify(26.8, cp_loss=339, before_cp=371, after_cp=32) == "blunder"
    # Going from +9 to +6 changes nothing that matters.
    assert gr.classify(1.0, cp_loss=300, before_cp=900, after_cp=600) == ""
    assert gr.classify(12.0, cp_loss=141, before_cp=0, after_cp=-141) == "mistake"


def test_annotated_pgn_has_nags_best_lines_and_coach_notes():
    a = _analysis()
    notes = {"intro": "Won in 8.", "notes": {"12": "Taking on f7 walks into Qf3+."}, "lessons": ["Count the checks."]}
    text = gr.annotated_pgn(GAME, a, notes)
    game = gr.read_game(text)
    assert not game.errors
    assert "Won in 8." in game.comment and "Count the checks." in game.comment
    node = list(game.mainline())[11]
    assert chess.pgn.NAG_BLUNDER in node.nags
    assert "Taking on f7 walks into Qf3+." in node.comment and "[%emt 0:00:03]" in node.comment
    assert len(node.parent.variations) == 2  # engine's best move added as a sideline


def test_strip_and_reimport_round_trip():
    annotated = gr.annotated_pgn(GAME, _analysis(), {"intro": "Intro text", "notes": {"3": "Develops."}})
    clean = gr.strip_annotations(annotated)
    g = gr.read_game(clean)
    assert not g.comment and all(len(n.variations) <= 1 for n in g.mainline())
    assert "[%emt 0:06:00]" in clean and "Develops." not in clean and "$4" not in clean
    intro, by_ply = gr.comments_by_ply(annotated)
    assert intro == "Intro text" and by_ply[3].endswith("Develops.")


def test_name_matching_keeps_siblings_apart():
    assert gr.same_player("Alsisaria, Ahaan", "Ahaan Alsisaria")
    assert not gr.same_player("Alsisaria, Ahaan", "Shreyansh Alsisaria")
    assert gr.same_player("Rajesh", "Rajesh, Ednit")


def _g(i, white, black, result, first_moves, we=1700, be=1700, date="2026.01.01", event="E"):
    return {"id": i, "white": white, "black": black, "result": result, "white_elo": we, "black_elo": be,
            "date": date, "event": event, "eco": "", "opening": "",
            "raw_pgn": f'[White "{white}"]\n[Black "{black}"]\n\n1. {first_moves} *'}


def test_scout_report_openings_strength_and_head_to_head():
    games = [
        _g(1, "Ahaan Alsisaria", "X, Y", "1-0", "d4 d5 2. c4 e6"),
        _g(2, "Ahaan Alsisaria", "Mehta, Nakshwin", "1/2-1/2", "d4 Nf6 2. c4 c5", be=1682),
        _g(3, "Z, Q", "Ahaan Alsisaria", "0-1", "e4 c5 2. Nf3 e6", we=1900),
        _g(4, "W, V", "Ahaan Alsisaria", "1-0", "d4 d5 2. c4 e6 3. Nc3 f5"),
        _g(5, "Shreyansh Alsisaria", "R, S", "1-0", "e4 e5"),
    ]
    r = gr.scout_report(games, "Alsisaria, Ahaan", ["Mehta, Nakshwin"])
    assert r["total"]["games"] == 4 and r["total"]["pts"] == 2.5
    assert r["first_move"][0] == {"line": "d4", "games": 2, "w": 1, "d": 1, "l": 0, "pts": 1.5, "pct": 75}
    assert r["vs_e4"][0]["line"] == "c5"
    assert r["vs_d4"][0]["line"] == "d5 e6"
    assert [x["band"] for x in r["by_strength"]] == ["vs higher (+100)", "vs similar"]
    assert [g["id"] for g in r["head_to_head"]] == [2]


def test_season_stats_patterns():
    rows = gr.player_games([
        _g(1, "Mehta, Nakshwin", "A, B", "0-1", "d4 d5", be=1900, event="Coppell"),
        _g(2, "C, D", "Mehta, Nakshwin", "1-0", "e4 c6", we=1500, event="Coppell"),
        _g(3, "Mehta, Nakshwin", "E, F", "1-0", "d4 d5", be=1600, event="Plano"),
    ], "Mehta, Nakshwin")
    plies = [{"ply": i + 1, "color": "white" if i % 2 == 0 else "black", "eval": 200, "cp_loss": 0,
              "kind": "", "think_s": 10} for i in range(20)]
    analyses = {1: {"plies": plies, "moments": [], "castled": {"white": None, "black": 8}}}
    s = gr.season_stats(rows, analyses)
    assert s["total"]["games"] == 3 and s["total"]["pts"] == 1.0
    assert s["perf"] == round((1900 + 1500 + 1600) / 3 + 400 * (1 - 2) / 3)
    pats = {p["pattern"]: p["count"] for p in s["patterns"]}
    assert pats["was clearly better (+1.5) and did not win"] == 1
    assert pats["lost with the king still uncastled at move 20"] == 1
    assert s["reviewed"] == 1 and s["by_event"][0]["event"] in ("Coppell", "Plano")


def test_parse_coach_json_tolerates_fences():
    out = gr.parse_coach_json('Here:\n```json\n{"intro": "x", "notes": {"12": "y", "bad": "z"}, "lessons": ["l"]}\n```')
    assert out == {"intro": "x", "notes": {"12": "y"}, "lessons": ["l"]}


@pytest.mark.skipif(not gr.find_stockfish(), reason="Stockfish not installed")
def test_real_stockfish_smoke():
    a = gr.run_engine_analysis(GAME, depth=6)
    assert len(a["plies"]) == 15 and len(a["chart"]["values"]) == 15
    assert all(isinstance(p["eval"], int) and p["best_san"] for p in a["plies"])
    assert gr.read_game(gr.annotated_pgn(GAME, a)).errors == []


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

def _store_game(app_module, pgn=GAME):
    import pgn_harvester as ph
    sid = app_module.ensure_pgn_source("upload", "test:1", "Test games")
    d = ph.pgn_to_game_dict(pgn, sid)
    app_module.insert_pgn_games([d])
    return app_module.find_pgn_game_by_hash(d["game_hash"])


def _fake_engine_run(app_module, monkeypatch):
    monkeypatch.setattr(app_module.gr, "run_engine_analysis",
                        lambda raw, **kw: gr.analyse_game(raw, FakeEngine(raw), depth=5))


def test_review_page_queue_run_and_download(client, app_module, monkeypatch):
    app_module.create_user("parent", "correct-horse", role="viewer", status="active")
    gid = _store_game(app_module)
    assert client.get(f"/review/{gid}", follow_redirects=False).status_code in (303, 401)
    login(client, "parent", "correct-horse")
    page = client.get(f"/review/{gid}")
    assert page.status_code == 200 and "Run engine review" in page.text and "Nxd5" in page.text

    resp = client.post(f"/review/{gid}/analyze", follow_redirects=False)
    assert resp.status_code == 303
    assert client.get(f"/api/review/{gid}/status").json()["status"] == "queued"
    assert "Engine review queued" in client.get(resp.headers["location"]).text

    _fake_engine_run(app_module, monkeypatch)
    job = app_module.claim_next_analysis()
    assert job["pgn_game_id"] == gid and job["status"] == "running"
    app_module._run_one_analysis(job)

    page = client.get(f"/review/{gid}").text
    assert 'id="manhattan"' in page and "Turning points" in page and "6...Kxf7" in page
    assert "instant move right after a long think" in page
    dl = client.get(f"/review/{gid}/pgn")
    assert dl.status_code == 200 and "attachment" in dl.headers["content-disposition"]
    assert '[Annotator "MyChessRating game review"]' in dl.text and "$4" in dl.text

    # Coach notes are for coaches/admins only.
    assert client.post(f"/review/{gid}/notes").status_code == 403


def test_queue_cap_per_user(client, app_module, monkeypatch):
    app_module.create_user("busy", "correct-horse", role="viewer", status="active")
    gid = _store_game(app_module)
    monkeypatch.setattr(app_module, "MAX_PENDING_PER_USER", 0)
    login(client, "busy", "correct-horse")
    resp = client.post(f"/review/{gid}/analyze")
    assert "reviews waiting" in resp.text
    assert (app_module.get_game_analysis(gid) or {}).get("status") is None


def test_ai_coach_notes(client, app_module, monkeypatch):
    app_module.create_user("coach1", "correct-horse", role="coach", status="active")
    gid = _store_game(app_module)
    _fake_engine_run(app_module, monkeypatch)
    app_module.request_game_analysis(gid, None, 5)
    app_module._run_one_analysis(app_module.claim_next_analysis())

    seen = {}

    class Messages:
        def create(self, **kw):
            seen.update(kw)
            text = '{"intro": "Bottom line: won in 8.", "notes": {"12": "Kxf7 came in 3 seconds."}, "lessons": ["Pause after a long think."]}'
            return types.SimpleNamespace(content=[types.SimpleNamespace(type="text", text=text)], stop_reason="end_turn",
                                         model="claude-opus-5-5", usage=types.SimpleNamespace(input_tokens=1000, output_tokens=200))

    monkeypatch.setattr(app_module, "ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setattr(app_module, "_anthropic_client",
                        lambda: types.SimpleNamespace(beta=types.SimpleNamespace(messages=Messages())))
    login(client, "coach1", "correct-horse")
    resp = client.post(f"/review/{gid}/notes")
    assert resp.status_code == 200 and "Coach notes written" in resp.text
    assert "6...Kxf7" in seen["messages"][0]["content"] and "instant move right after a long think" in seen["messages"][0]["content"]
    assert "Bottom line: won in 8." in resp.text and "Pause after a long think." in resp.text
    row = app_module.get_game_analysis(gid)
    assert row["notes_source"] == "claude" and row["usage"]["cost_usd"] is not None
    assert "Kxf7 came in 3 seconds." in client.get(f"/review/{gid}/pgn").text


def test_scout_and_season_pages(client, app_module):
    app_module.create_user("parent", "correct-horse", role="viewer", status="active")
    gid = _store_game(app_module)
    login(client, "parent", "correct-horse")
    scout = client.get("/scout", params={"name": "Karry, Tapasya"}).text
    assert "As Black against 1.e4" in scout and f'/review/{gid}' in scout
    assert "No games found" in client.get("/scout", params={"name": "Nobody, Here"}).text
    season = client.get("/season", params={"player": "Mehta, Nakshwin"}).text
    assert "1.0/1" in season and "Queue engine review for the 1 unreviewed" in season
    resp = client.post("/season/review-all", data={"player": "Mehta, Nakshwin", "since": ""})
    assert "Queued 1 game(s) for engine review." in resp.text
    assert "No games found" in client.get("/season", params={"player": "Mehta, Nakshwin", "since": "2027-01-01"}).text


def test_my_tournament_review_all_and_links(client, app_module):
    uid = app_module.create_user("parent", "correct-horse", role="viewer", status="active")
    gid = _store_game(app_module)
    tid, _ = app_module.create_tracked_tournament(uid, "Saturday Swiss", "2026-10-18", "2026-10-18")
    app_module.add_tracked_game(tid, 2, "2026-10-18", "white", "Karry, Tapasya", 1588, "1-0", gid, "upload")
    login(client, "parent", "correct-horse")
    page = client.get(f"/my-tournaments/{tid}").text
    assert f'href="/review/{gid}"' in page and "/scout?name=Karry" in page
    resp = client.post(f"/my-tournaments/{tid}/review-all")
    assert "Queued 1 game(s) for engine review." in resp.text


def test_admin_import_of_annotated_pgn_keeps_notes(client, app_module):
    app_module.create_user("root", "correct-horse", role="admin", status="active")
    app_module.create_user("parent", "correct-horse", role="viewer", status="active")
    annotated = gr.annotated_pgn(GAME, _analysis(), {"intro": "Hand-written intro.", "notes": {"12": "Hand note on Kxf7."}})
    login(client, "parent", "correct-horse")
    assert client.post("/review/import-annotated", files={"pgn_file": ("a.pgn", annotated.encode(), "text/plain")}).status_code == 403
    client.post("/logout")
    login(client, "root", "correct-horse")
    resp = client.post("/review/import-annotated", data={"label": "Mehta annotated", "queue_engine": "1"},
                       files={"pgn_file": ("a.pgn", annotated.encode(), "text/plain")})
    assert "1 game(s) imported from Mehta annotated; notes kept on 1; 1 queued for engine review." in resp.text
    gid = app_module.find_games_for_name("Karry, Tapasya")[0]["id"]
    raw = app_module.get_pgn_game_raw(gid)
    assert "Hand note" not in raw and "[%emt 0:03]" not in raw and "[%emt 0:00:03]" in raw
    row = app_module.get_game_analysis(gid)
    assert row["status"] == "queued" and row["notes_source"] == "imported"
    page = client.get(f"/review/{gid}").text
    assert "Hand-written intro." in page and "Hand note on Kxf7." in page and "imported annotation" in page


def test_imported_notes_survive_the_engine_run(client, app_module, monkeypatch):
    admin = {"id": app_module.create_user("root", "correct-horse", role="admin", status="active"), "role": "admin"}
    annotated = gr.annotated_pgn(GAME, _analysis(), {"intro": "Hand-written intro.", "notes": {"12": "Hand note on Kxf7."}})
    res = app_module._import_annotated_text(annotated, "Mehta annotated", admin, True)
    assert res == {"stored": 1, "notes": 1, "queued": 1}
    _fake_engine_run(app_module, monkeypatch)
    app_module._run_one_analysis(app_module.claim_next_analysis())
    gid = app_module.find_games_for_name("Karry, Tapasya")[0]["id"]
    final = app_module.get_game_analysis(gid)["annotated_pgn"]
    assert final.count("Hand note on Kxf7.") == 1 and "Hand-written intro." in final and "$4" in final


def test_nav_has_game_review_menu(client, app_module):
    app_module.create_user("parent", "correct-horse", role="viewer", status="active")
    login(client, "parent", "correct-horse")
    html = client.get("/profile").text
    assert 'href="/season"' in html and 'href="/scout"' in html


def test_first_moves_normalises_long_algebraic():
    assert gr._first_moves('[White "a"]\n\n1. e2e4 c7c5 2. g1f3 *', 3) == ["e4", "c5", "Nf3"]
    assert gr._first_moves("1. d4 {[%clk 1:30:00]} Nf6 (1... d5) 2. c4 c5 *", 4) == ["d4", "Nf6", "c4", "c5"]
