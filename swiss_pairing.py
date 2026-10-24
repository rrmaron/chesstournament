"""USCF-style Swiss pairings, the rules SwissSys follows, used to predict the
next round from imported results.

Implemented from the USCF rulebook (chapter 2, rules 27-29):
- players are ranked by score, then rating (27A1, 27A2); score groups pair top
  half against bottom half in order (27A3);
- an odd score group drops its lowest-rated player to the next group, where the
  floater is paired against the highest-rated player there (29C/29D);
- no rematches (27A1);
- colors are equalised first and alternated second (29E); nobody gets the same
  color three times running or a two-game imbalance when it can be avoided;
  transpositions and interchanges to fix colors are limited to 80 rating points
  for alternation and 200 for equalisation (29E5);
- when both players are due the same color, the larger imbalance wins, then the
  most recent differing color history, then the higher-ranked player (29E4);
- the bye goes to the lowest-rated player in the lowest score group who has not
  had one (28L).
"""
from dataclasses import dataclass, field
from itertools import groupby
from typing import Dict, List, Optional, Tuple

ALTERNATION_LIMIT = 80
EQUALIZATION_LIMIT = 200
_SEARCH_BUDGET = 25_000


@dataclass
class Entrant:
    no: int
    rating: int = 0
    score: float = 0.0
    colors: List[str] = field(default_factory=list)      # 'W' / 'B' for each game actually played
    opponents: set = field(default_factory=set)
    had_bye: bool = False
    active: bool = True
    name: str = ""

    def imbalance(self) -> int:
        return self.colors.count("W") - self.colors.count("B")

    def due(self) -> Tuple[Optional[str], str]:
        """(color, 'equalize' | 'alternate' | '') the player is due next."""
        imb = self.imbalance()
        if imb > 0:
            return "B", "equalize"
        if imb < 0:
            return "W", "equalize"
        if self.colors:
            return ("B" if self.colors[-1] == "W" else "W"), "alternate"
        return None, ""

    def must(self) -> Optional[str]:
        """Color the player must receive to avoid three in a row or a 2-game imbalance."""
        if len(self.colors) >= 2 and self.colors[-1] == self.colors[-2]:
            return "B" if self.colors[-1] == "W" else "W"
        imb = self.imbalance()
        if imb >= 2:
            return "B"
        if imb <= -2:
            return "W"
        return None


def _rank_key(e: Entrant):
    return (-e.score, -e.rating, e.no)


def _pair_cost(a: Entrant, b: Entrant, natural: Entrant) -> Optional[float]:
    """Cost of pairing a (top half) with b (bottom half) instead of b's natural
    slot; None when the pairing is illegal."""
    if b.no in a.opponents:
        return None
    cost = 0.0
    ma, mb = a.must(), b.must()
    if ma and mb and ma == mb:
        cost += 1000.0                      # three-in-a-row for someone: only if nothing else works
    da, ka = a.due()
    db, kb = b.due()
    if da and db and da == db:
        cost += 40.0 if (ka == "equalize" or kb == "equalize") else 20.0
    diff = abs(b.rating - natural.rating)
    if b is not natural and diff:
        # Transposition. Allowed freely to avoid a rematch; for colors only within the limits.
        limit = EQUALIZATION_LIMIT if (ka == "equalize" or kb == "equalize") else ALTERNATION_LIMIT
        cost += diff / 50.0 if diff <= limit else 60.0 + diff / 50.0
    return cost


def _best_pairing(top: List[Entrant], bottom: List[Entrant]) -> List[Tuple[Entrant, Entrant]]:
    """Minimum-cost matching of top vs bottom by bounded backtracking."""
    n = len(top)
    best: Dict[str, object] = {"cost": float("inf"), "pairs": None, "nodes": 0}
    order = list(range(n))

    def rec(i: int, used: set, acc: float, pairs: list):
        best["nodes"] += 1
        if acc >= best["cost"] or best["nodes"] > _SEARCH_BUDGET:
            return
        if i == n:
            best["cost"], best["pairs"] = acc, list(pairs)
            return
        a = top[i]
        # natural partner first, then nearest by rating
        candidates = sorted((j for j in order if j not in used),
                            key=lambda j: (j != i, abs(bottom[j].rating - bottom[i].rating), j))
        for j in candidates:
            c = _pair_cost(a, bottom[j], bottom[i])
            if c is None:
                continue
            used.add(j)
            pairs.append((a, bottom[j]))
            rec(i + 1, used, acc + c, pairs)
            pairs.pop()
            used.discard(j)

    rec(0, set(), 0.0, [])
    if best["pairs"] is None:
        # Everything illegal (tiny groups full of rematches): fall back to the fold.
        return list(zip(top, bottom))
    return best["pairs"]


def _assign_colors(a: Entrant, b: Entrant, board_parity_white_top: bool) -> Tuple[Entrant, Entrant]:
    """(white, black). `a` is the higher-ranked player."""
    ma, mb = a.must(), b.must()
    if ma and not (mb and mb == ma):
        return (a, b) if ma == "W" else (b, a)
    if mb and not (ma and ma == mb):
        return (b, a) if mb == "W" else (a, b)
    da, _ = a.due()
    db, _ = b.due()
    if da and not db:
        return (a, b) if da == "W" else (b, a)
    if db and not da:
        return (b, a) if db == "W" else (a, b)
    if da and db and da != db:
        return (a, b) if da == "W" else (b, a)
    if da and db and da == db:
        ia, ib = abs(a.imbalance()), abs(b.imbalance())
        if ia != ib:
            winner = a if ia > ib else b
        else:
            winner = a
            for ca, cb in zip(reversed(a.colors), reversed(b.colors)):
                if ca != cb:
                    # the player who did NOT have the due color most recently gets it now
                    winner = a if ca != da else b
                    break
        loser = b if winner is a else a
        return (winner, loser) if da == "W" else (loser, winner)
    # Nobody has a history (round 1): alternate down the boards, higher-ranked gets white on board 1.
    return (a, b) if board_parity_white_top else (b, a)


def pair_round(entrants: List[Entrant], first_board_white_top: bool = True) -> dict:
    """Predict the next round. Returns {"boards": [...], "bye": no|None, "floaters": [no, ...]}
    where each board is {"board", "white", "black", "white_score", "black_score", "floater"}."""
    players = sorted([e for e in entrants if e.active], key=_rank_key)
    bye = None
    if len(players) % 2 == 1:
        for e in reversed(players):
            if not e.had_bye:
                bye = e
                break
        bye = bye or players[-1]
        players = [p for p in players if p is not bye]

    boards: List[dict] = []
    floaters: List[int] = []
    carry: List[Entrant] = []
    board_no = 0
    for _score, grp in groupby(players, key=lambda e: e.score):
        pool = carry + list(grp)
        carry = []
        if len(pool) % 2 == 1:
            # drop the lowest-rated player who still has someone to play below
            floater = pool[-1]
            for cand in reversed(pool):
                if cand not in carry:
                    floater = cand
                    break
            pool.remove(floater)
            carry = [floater]
            floaters.append(floater.no)
        if not pool:
            continue
        pairs: List[Tuple[Entrant, Entrant]] = []
        dropped = [e for e in pool if e.no in floaters and e.score > pool[-1].score]
        for fl in dropped:
            # 29C: the floater meets the highest-rated player of the lower group they can legally play.
            rest = [e for e in pool if e is not fl]
            partner = next((e for e in rest if e.no not in fl.opponents
                            and not (e.must() and fl.must() and e.must() == fl.must())), None)
            partner = partner or next((e for e in rest if e.no not in fl.opponents), None) or rest[0]
            pairs.append((fl, partner))
            pool = [e for e in pool if e is not fl and e is not partner]
        half = len(pool) // 2
        top, bottom = pool[:half], pool[half:]
        for a, b in pairs + _best_pairing(top, bottom):
            board_no += 1
            white, black = _assign_colors(a, b, board_parity_white_top=(board_no % 2 == 1) == first_board_white_top)
            boards.append({"board": board_no, "white": white.no, "black": black.no,
                           "white_score": white.score, "black_score": black.score,
                           "floater": a.no in floaters or b.no in floaters})
    if carry:
        # odd leftover at the very bottom (only when the bye rule above could not resolve parity)
        if bye is None:
            bye = carry[0]
        else:
            boards.append({"board": board_no + 1, "white": carry[0].no, "black": bye.no,
                           "white_score": carry[0].score, "black_score": bye.score, "floater": True})
            bye = None
    return {"boards": boards, "bye": bye.no if bye else None, "floaters": floaters}


# ---------------------------------------------------------------------------
# Building entrants from imported round results
# ---------------------------------------------------------------------------

def _points(result: str) -> Tuple[Optional[float], Optional[float]]:
    r = (result or "").replace(" ", "")
    if r in ("1-0", "1:0", "+:-", "1-0F"):
        return 1.0, 0.0
    if r in ("0-1", "0:1", "-:+", "0-1F"):
        return 0.0, 1.0
    if r in ("½-½", "1/2-1/2", "½:½", "1/2:1/2", "0.5-0.5"):
        return 0.5, 0.5
    return None, None


def entrants_from_rounds(players: List[dict], rounds: Dict[int, List[dict]]) -> List[Entrant]:
    """players: [{start_rank|start_no, fide_rating|rating, name}], rounds: {round: [{white_start_no,
    black_start_no, result}]}. Games with result '*' count as unplayed (no color, no points)."""
    by_no: Dict[int, Entrant] = {}
    for p in players:
        no = p.get("start_rank") or p.get("start_no")
        if not no:
            continue
        by_no[int(no)] = Entrant(no=int(no), rating=int(p.get("fide_rating") or p.get("rating") or 0),
                                 name=p.get("name") or "")
    for rd in sorted(rounds):
        for g in rounds[rd]:
            w, b = int(g.get("white_start_no") or 0), int(g.get("black_start_no") or 0)
            pw, pb = _points(g.get("result", ""))
            if w and not b:
                if w in by_no:
                    by_no[w].had_bye = True
                    by_no[w].score += pw if pw is not None else 1.0
                continue
            if not w or not b or w not in by_no or b not in by_no:
                continue
            if pw is None:
                continue
            by_no[w].score += pw
            by_no[b].score += pb
            by_no[w].colors.append("W")
            by_no[b].colors.append("B")
            by_no[w].opponents.add(b)
            by_no[b].opponents.add(w)
    return list(by_no.values())
