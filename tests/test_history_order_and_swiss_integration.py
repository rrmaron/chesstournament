"""Imported USCF tournament history shows newest first, and imported round
results drive a USCF-rules next-round prediction on the simulate page and the
My Tournaments detail page."""
import io

from tests.conftest import login

SOURCE = "https://chess-results.com/tnr777.aspx?lan=1"


def _players():
    names = ["Alpha, A", "Beta, B", "Gamma, G", "Mehta, Nakshwin", "Delta, D", "Epsilon, E", "Zeta, Z", "Eta, E"]
    return [{"start_rank": i, "name": n, "fide_rating": 2000 - (i - 1) * 50, "fide_id": str(1000 + i)}
            for i, n in enumerate(names, start=1)]


def test_saved_tournaments_list_newest_first_and_calculator_renders_newest_on_top(client, app_module):
    uid = app_module.create_user("nak", "correct-horse", role="viewer", status="active")
    login(client, "nak", "correct-horse")
    ids = [app_module.save_user_tournament(uid, name, 1500, 1520, "[]") for name in ("Spring 2025", "Summer 2025", "Fall 2026")]

    listed = [t["id"] for t in app_module.list_user_tournaments(uid)]
    assert listed == list(reversed(ids))  # same-second inserts still come back newest first

    html = io.open("templates/uscf_calculator.html", encoding="utf-8").read()
    assert "}).reverse().join('');" in html  # the working list renders newest on top


def test_imported_rounds_predict_next_round_with_uscf_rules(client, app_module):
    uid = app_module.create_user("nak", "correct-horse", role="viewer", status="active")
    app_module.update_user_profile(uid, "31625896", None, 1850, None, None, None, None, None, "Mehta, Nakshwin")
    login(client, "nak", "correct-horse")
    app_module.upsert_research_players(_players(), SOURCE, "Dallas Fall Open", uid)
    app_module.store_round_results(SOURCE, 1, [
        {"board_no": 1, "white_no": 1, "white_name": "Alpha, A", "black_no": 5, "black_name": "Delta, D", "result": "1 - 0"},
        {"board_no": 2, "white_no": 6, "white_name": "Epsilon, E", "black_no": 2, "black_name": "Beta, B", "result": "0 - 1"},
        {"board_no": 3, "white_no": 3, "white_name": "Gamma, G", "black_no": 7, "black_name": "Zeta, Z", "result": "1 - 0"},
        {"board_no": 4, "white_no": 8, "white_name": "Eta, E", "black_no": 4, "black_name": "Mehta, Nakshwin", "result": "0 - 1"},
    ], SOURCE + "&rd=1")

    pred = app_module._swiss_prediction(SOURCE, 4)
    assert pred["predict_round"] == 2 and not pred["in_progress"] and pred["bye"] is None
    pairs = {frozenset((b["white"], b["black"])) for b in pred["boards"]}
    # 1 and 3 both had White; 3 and 4 are 50 points apart so the colors are fixed by transposing.
    assert pairs == {frozenset((1, 4)), frozenset((2, 3)), frozenset((5, 8)), frozenset((6, 7))}
    assert pred["my_opponent"]["name"] == "Alpha, A" and pred["my_color"] == "white"  # 4 had Black, 1 had White

    page = client.get("/research-players/simulate", params={"tournament_source": SOURCE, "my_rank": 4})
    assert page.status_code == 200
    assert "Round 2 predicted pairings (USCF / SwissSys rules)" in page.text
    assert "you are predicted to play" in page.text and "Alpha, A" in page.text

    tid, _ = app_module.create_tracked_tournament(uid, "Dallas Fall Open", "2026-11-06", "2026-11-08")
    app_module.update_tracked_tournament(tid, tournament_source=SOURCE, my_start_rank=4)
    detail = client.get(f"/my-tournaments/{tid}").text
    assert "Round 2 by USCF rules" in detail and "Alpha, A" in detail and "<strong>white</strong>" in detail

    # Without any imported rounds nothing is predicted, and the pages still render.
    assert app_module._swiss_prediction("https://chess-results.com/none.aspx", 1) is None
