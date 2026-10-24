"""USCF/SwissSys-style pairing predictions: score groups, top half vs bottom
half, floaters, byes, no rematches, color equalisation/alternation with the
80/200-point transposition limits.
"""
import swiss_pairing as sp


def _field(n, base=2000, step=50):
    return [sp.Entrant(no=i, rating=base - (i - 1) * step, name=f"P{i}") for i in range(1, n + 1)]


def _by_pair(result):
    return {frozenset((b["white"], b["black"])) for b in result["boards"]}


def _colors(result):
    return {b["white"]: "W" for b in result["boards"]} | {b["black"]: "B" for b in result["boards"]}


def test_round_one_folds_top_half_against_bottom_half_with_alternating_colors():
    r = sp.pair_round(_field(8))
    assert [(b["white"], b["black"]) for b in r["boards"]] == [(1, 5), (6, 2), (3, 7), (8, 4)]
    assert r["bye"] is None and r["floaters"] == []


def test_round_two_pairs_within_score_groups_and_equalises_colors():
    players = _field(8)
    rounds = {1: [
        {"white_start_no": 1, "black_start_no": 5, "result": "1-0"},
        {"white_start_no": 6, "black_start_no": 2, "result": "0-1"},
        {"white_start_no": 3, "black_start_no": 7, "result": "1-0"},
        {"white_start_no": 8, "black_start_no": 4, "result": "0-1"},
    ]}
    entrants = sp.entrants_from_rounds([{"start_rank": p.no, "fide_rating": p.rating} for p in players], rounds)
    scores = {e.no: e.score for e in entrants}
    assert scores == {1: 1, 2: 1, 3: 1, 4: 1, 5: 0, 6: 0, 7: 0, 8: 0}

    r = sp.pair_round(entrants)
    # Natural fold would be 1-3 / 2-4, but 1 and 3 both had White. 3 and 4 are only 50 points
    # apart, inside the 200-point equalisation limit, so SwissSys transposes: 1-4, 2-3.
    # The same happens in the 0-point group: 5-8, 6-7.
    assert _by_pair(r) == {frozenset((1, 4)), frozenset((2, 3)), frozenset((5, 8)), frozenset((6, 7))}
    c = _colors(r)
    assert c == {1: "B", 4: "W", 2: "W", 3: "B", 5: "W", 8: "B", 6: "B", 7: "W"}  # everyone equalised


def test_round_two_keeps_natural_fold_when_transposition_exceeds_limit():
    g = [sp.Entrant(no=1, rating=2000, score=1, colors=["W"]), sp.Entrant(no=2, rating=1950, score=1, colors=["B"]),
         sp.Entrant(no=3, rating=1900, score=1, colors=["W"]), sp.Entrant(no=4, rating=1600, score=1, colors=["B"])]
    r = sp.pair_round(g)
    assert _by_pair(r) == {frozenset((1, 3)), frozenset((2, 4))}
    c = _colors(r)
    assert c[1] == "B" and c[3] == "W"   # both due Black: the higher-ranked player gets it
    assert c[2] == "W" and c[4] == "B"


def test_no_rematch_forces_a_transposition():
    players = _field(4)
    for e in players:
        e.score = 1.0
    players[0].opponents.add(3); players[2].opponents.add(1)       # 1 already played 3
    r = sp.pair_round(players)
    assert _by_pair(r) == {frozenset((1, 4)), frozenset((2, 3))}


def test_odd_field_gives_bye_to_lowest_rated_without_one_and_floats_odd_groups():
    players = _field(9, base=1900)
    for e in players[:3]:          # 1, 2, 3 on 1 point
        e.score = 1.0
    players[8].had_bye = True      # 9 already had a bye -> bye goes to 8
    r = sp.pair_round(players)
    assert r["bye"] == 8
    assert r["floaters"] == [3]                      # odd top group drops its lowest-rated player
    pairs = _by_pair(r)
    assert frozenset((1, 2)) in pairs                # the two remaining 1-pointers play each other
    assert frozenset((3, 4)) in pairs                # floater meets the top of the next group
    assert len(r["boards"]) == 4


def test_three_in_a_row_is_avoided_and_due_color_priority_follows_imbalance():
    a = sp.Entrant(no=1, rating=2000, score=2, colors=["W", "W"])
    b = sp.Entrant(no=2, rating=1990, score=2, colors=["W", "B"])
    r = sp.pair_round([a, b])
    assert _colors(r) == {1: "B", 2: "W"}

    # Both due White; the bigger imbalance (two Blacks) wins.
    a = sp.Entrant(no=1, rating=2000, score=1.5, colors=["B", "W", "B"])
    b = sp.Entrant(no=2, rating=1990, score=1.5, colors=["B", "B"])
    assert _colors(sp.pair_round([a, b])) == {2: "W", 1: "B"}


def test_color_transposition_respects_80_point_limit():
    # Score group of 4: natural fold is 1-3, 2-4. 1 and 3 are both due White (hard conflict
    # avoided only by swapping 3 and 4). 3 and 4 are 40 points apart, so the swap is made.
    g = [sp.Entrant(no=1, rating=2000, score=1, colors=["B"]), sp.Entrant(no=2, rating=1980, score=1, colors=["W"]),
         sp.Entrant(no=3, rating=1960, score=1, colors=["B"]), sp.Entrant(no=4, rating=1920, score=1, colors=["W"])]
    r = sp.pair_round(g)
    assert _by_pair(r) == {frozenset((1, 4)), frozenset((2, 3))}

    # Same situation but 3 and 4 are 300 points apart: too far to transpose for alternation,
    # so the natural pairing stands and the color conflict is resolved by priority.
    g[3].rating = 1660
    r = sp.pair_round(g)
    assert _by_pair(r) == {frozenset((1, 3)), frozenset((2, 4))}


def test_entrants_from_rounds_handles_byes_unplayed_and_half_points():
    rounds = {1: [{"white_start_no": 1, "black_start_no": 2, "result": "½-½"},
                  {"white_start_no": 3, "black_start_no": 0, "result": "1-0"},     # bye
                  {"white_start_no": 4, "black_start_no": 5, "result": "*"}]}      # not finished
    es = {e.no: e for e in sp.entrants_from_rounds([{"start_no": i, "rating": 1500} for i in range(1, 6)], rounds)}
    assert es[1].score == 0.5 and es[2].score == 0.5 and es[1].colors == ["W"] and es[2].opponents == {1}
    assert es[3].had_bye and es[3].score == 1.0 and es[3].colors == []
    assert es[4].score == 0 and es[4].colors == [] and es[4].opponents == set()
