"""Tests for fide.py's calculate_rating (beads mychesspairings-e33).

calculate_rating is pure and deterministic (no DB/network), so these tests
import it directly rather than going through the app_module/client fixtures.
Expected values below were cross-checked against the actual implementation,
not re-derived from the FIDE regulation independently — these are regression/
characterization tests for the existing arithmetic and control flow (filtering,
score parsing, dp-table lookup, floor branches), which is exactly where bugs
like an off-by-one, a wrong operator, or a min/max mix-up tend to hide.
"""
from fide import calculate_rating


def test_fewer_than_five_valid_games_returns_none():
    assert calculate_rating([1500] * 4, [0.5] * 4) is None


def test_zero_games_returns_none():
    assert calculate_rating([], []) is None


def test_baseline_five_draws_against_1500():
    result = calculate_rating([1500] * 5, [0.5] * 5)
    assert result == {
        "rating": 1585, "Rp": 1585, "avg": 1585, "score": 3.5,
        "games": 7, "real_games": 5, "perc": 50.0, "dp": 0,
    }


def test_rating_floors_at_1000_for_very_weak_performance():
    """5 losses against the minimum-allowed opponent rating (800) must floor
    at 1000, not go negative or below it."""
    result = calculate_rating([800] * 5, [0] * 5)
    assert result["Rp"] == 776          # the raw (unfloored) calculation
    assert result["rating"] == 1000     # floored


def test_high_rated_wins_do_not_get_lowered_by_the_floor_checks():
    """Guards against a min/max mix-up: when Rp is already above every floor
    threshold (1600/1800/2000), those checks must never reduce the rating."""
    result = calculate_rating([2900] * 5, [1] * 5)
    assert result["Rp"] == 2894
    assert result["rating"] == 2894     # unchanged by the floor branches


def test_out_of_range_and_invalid_opponents_are_filtered_out():
    """700 (<800) and 3100 (>3000) are out of FIDE's valid rating range;
    'abc' and None aren't valid ratings at all. All four must be dropped,
    leaving only the 5 real 1500-rated draws -- same result as the baseline."""
    opponents = [700, 3100, "abc", None, 1500, 1500, 1500, 1500, 1500]
    results = [0, 0, 0, 0, 0.5, 0.5, 0.5, 0.5, 0.5]
    result = calculate_rating(opponents, results)
    assert result == calculate_rating([1500] * 5, [0.5] * 5)
    assert result["real_games"] == 5


def test_textual_result_values_parse_the_same_as_numeric_ones():
    """'win'/'draw'/'='/'½'/'loss' (case-insensitive) must score identically
    to their numeric equivalents (1 / 0.5 / 0.5 / 0.5 / 0)."""
    result = calculate_rating([1500] * 5, ["Win", "draw", "=", "loss", "½"])
    # win(1) + draw(0.5) + '='(0.5) + loss(0) + ½(0.5) = 2.5, same total score
    # as five 0.5 draws (2.5) -- so the two scenarios must match exactly.
    assert result == calculate_rating([1500] * 5, [0.5] * 5)


def test_numeric_string_results_are_parsed_as_floats():
    result = calculate_rating([1500] * 5, ["1", "0.5", "0", "0.5", "1"])
    assert result["score"] == 1.0 + 0.5 + 0.0 + 0.5 + 1.0 + 1.0  # +1.0 fictitious
    assert result["real_games"] == 5


def test_unrecognized_result_strings_default_to_a_loss():
    result = calculate_rating([1500] * 5, ["forfeit", "bye", "?", "", "n/a"])
    assert result["real_games"] == 5
    assert result["score"] == 1.0  # only the +1.0 fictitious score, all real games scored 0


def test_mismatched_but_otherwise_valid_pairs_still_count():
    """Games beyond the minimum of 5 (here 6 real games) are all included,
    not just the first 5."""
    result = calculate_rating([1500] * 6, [1, 1, 1, 1, 1, 1])
    assert result["real_games"] == 6
    assert result["games"] == 8
