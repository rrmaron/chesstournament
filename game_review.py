"""Post-game review: engine analysis, the eval chart ("Manhattan"), mistake
detection with clock context, annotated PGN, AI coach notes, opponent scouting
and season statistics.

Everything here is pure logic over PGN text and plain dicts so it can be tested
without a database or a real engine: the engine is passed in (anything with a
python-chess-style ``analyse(board, limit, multipv=...)`` method), and the
Claude client is passed in too.
"""
from __future__ import annotations

import io
import json
import logging
import math
import os
import re
import shutil
import statistics
from collections import Counter, defaultdict
from typing import Callable, Iterable, Optional

import chess
import chess.engine
import chess.pgn

log = logging.getLogger(__name__)

MATE_CP = 10000          # how a forced mate is scored, in centipawns
CHART_CLIP_PAWNS = 6.0   # Manhattan bars are clipped to +/- this many pawns
LONG_THINK_S = 300       # a "long think" is at least 5 minutes ...
INSTANT_S = 10           # ... and an "instant" move at most 10 seconds

# Mistake thresholds, as a drop in the mover's winning chances (percentage points).
BLUNDER_WP, MISTAKE_WP, INACCURACY_WP = 30.0, 20.0, 10.0
NAG_FOR = {"blunder": chess.pgn.NAG_BLUNDER, "mistake": chess.pgn.NAG_MISTAKE,
           "inaccuracy": chess.pgn.NAG_DUBIOUS_MOVE}
SYMBOL_FOR = {"blunder": "??", "mistake": "?", "inaccuracy": "?!"}


# ---------------------------------------------------------------------------
# PGN helpers
# ---------------------------------------------------------------------------

def read_game(pgn_text: str) -> Optional[chess.pgn.Game]:
    """Parse one game. Tolerates the blank-line quirk some exporters (ChessNoteR)
    write between the headers and the moves, which otherwise loses the moves."""
    text = re.sub(r"\]\s*\n\s*\n+(?=\s*1\.)", "]\n\n", (pgn_text or "").strip())
    game = chess.pgn.read_game(io.StringIO(text))
    if game is None or game.errors and not list(game.mainline_moves()):
        return None
    return game


def find_stockfish() -> Optional[str]:
    for candidate in (os.environ.get("STOCKFISH_PATH", ""), shutil.which("stockfish") or "",
                      "/usr/games/stockfish", "/usr/bin/stockfish"):
        if candidate and os.path.exists(candidate):
            return candidate
    return None


def _seconds(text: str) -> Optional[int]:
    m = re.match(r"\s*(\d+):(\d{1,2}):(\d{1,2}(?:\.\d+)?)", text or "")
    if not m:
        return None
    return int(m.group(1)) * 3600 + int(m.group(2)) * 60 + int(float(m.group(3)))


def _increment(time_control: str) -> int:
    """'5400+30' -> 30; 'G90+30' -> 30; 'g30/d5' -> 5 (delay counts like an increment here)."""
    tc = (time_control or "").lower()
    m = re.search(r"\+(\d+)", tc) or re.search(r"[d/](\d+)\s*$", tc)
    return int(m.group(1)) if m else 0


def think_times(game: chess.pgn.Game) -> list:
    """Seconds spent on each ply, or None per ply where unknown.

    Uses [%emt] (elapsed move time, what ChessNoteR writes) when present, else
    the difference between a side's successive [%clk] readings plus the increment."""
    nodes = list(game.mainline())
    emt = [re.search(r"\[%emt\s+([\d:.]+)\]", n.comment or "") for n in nodes]
    if any(emt):
        return [_seconds(m.group(1)) if m else None for m in emt]
    clocks = [re.search(r"\[%clk\s+([\d:.]+)\]", n.comment or "") for n in nodes]
    if not any(clocks):
        return [None] * len(nodes)
    inc = _increment(game.headers.get("TimeControl", ""))
    out, last = [], {}
    for i, m in enumerate(clocks):
        side = i % 2
        now = _seconds(m.group(1)) if m else None
        prev = last.get(side)
        out.append(max(0, prev - now + inc) if (now is not None and prev is not None) else None)
        if now is not None:
            last[side] = now
    return out


# ---------------------------------------------------------------------------
# Engine analysis
# ---------------------------------------------------------------------------

def _cp(score: chess.engine.PovScore) -> int:
    return score.white().score(mate_score=MATE_CP)


def win_pct(cp: int) -> float:
    """Winning chances (0-100) for White from a centipawn eval (Lichess's curve)."""
    cp = max(-1500, min(1500, cp))
    return 50 + 50 * (2 / (1 + math.exp(-0.00368208 * cp)) - 1)


def classify(wp_drop: float, cp_loss: int = 0, before_cp: int = 0, after_cp: int = 0) -> str:
    """Grade a move by the drop in winning chances, or - while the game is still
    undecided (either side of the move within 5 pawns) - by the centipawns lost.

    The centipawn rule matters for juniors: throwing away -3.7 for -0.3 is only a
    'mistake' on the winning-chances curve, but it is the move that lost the game."""
    live = min(abs(before_cp), abs(after_cp)) < 500
    if wp_drop >= BLUNDER_WP or (live and cp_loss >= 250):
        return "blunder"
    if wp_drop >= MISTAKE_WP or (live and cp_loss >= 120):
        return "mistake"
    if wp_drop >= INACCURACY_WP or (live and cp_loss >= 60):
        return "inaccuracy"
    return ""


def fmt_eval(cp: Optional[int]) -> str:
    if cp is None:
        return ""
    if abs(cp) >= MATE_CP - 500:
        moves = MATE_CP - abs(cp)
        return ("#" if cp > 0 else "#-") + str(max(moves, 0)) if moves < 500 else ("+M" if cp > 0 else "-M")
    return f"{cp / 100:+.2f}"


def move_label(ply: int, san: str) -> str:
    n = (ply + 1) // 2
    return f"{n}.{san}" if ply % 2 else f"{n}...{san}"


def analyse_game(pgn_text: str, engine, depth: int = 16, multipv: int = 3,
                 progress: Optional[Callable[[int, int], None]] = None) -> dict:
    """Run the engine over every position and return the review data.

    One search per position (N+1 for N moves): the best-line score of the
    position after a move is that move's evaluation, so each move costs one search."""
    game = read_game(pgn_text)
    if game is None:
        raise ValueError("Could not read the game's moves")
    board = game.board()
    moves = list(game.mainline_moves())
    times = think_times(game)
    limit = chess.engine.Limit(depth=depth)

    searches = []  # per position: (eval_cp, [(san, cp, pv_san), ...])
    for i in range(len(moves) + 1):
        if board.is_game_over():
            outcome = board.outcome()
            cp = 0 if outcome.winner is None else (MATE_CP if outcome.winner else -MATE_CP)
            searches.append((cp, []))
        else:
            infos = engine.analyse(board, limit, multipv=multipv)
            if isinstance(infos, dict):
                infos = [infos]
            cands = []
            for info in infos:
                pv = info.get("pv") or []
                if not pv:
                    continue
                cands.append((board.san(pv[0]), _cp(info["score"]), board.variation_san(pv[:8])))
            searches.append((cands[0][1] if cands else 0, cands))
        if progress:
            progress(i, len(moves))
        if i < len(moves):
            board.push(moves[i])

    board = game.board()
    plies = []
    for i, mv in enumerate(moves):
        ply = i + 1
        mover_white = board.turn == chess.WHITE
        san = board.san(mv)
        best_cp, cands = searches[i]
        after_cp = searches[i + 1][0]
        sign = 1 if mover_white else -1
        wp_best = win_pct(best_cp) if mover_white else 100 - win_pct(best_cp)
        wp_after = win_pct(after_cp) if mover_white else 100 - win_pct(after_cp)
        drop = max(0.0, wp_best - wp_after)
        played_best = bool(cands) and cands[0][0] == san
        cp_loss = 0 if played_best else max(0, sign * (best_cp - after_cp))
        kind = "" if played_best else classify(drop, cp_loss, sign * best_cp, sign * after_cp)
        plies.append({
            "ply": ply, "label": move_label(ply, san), "san": san, "uci": mv.uci(),
            "color": "white" if mover_white else "black",
            "fen_before": board.fen(),
            "eval": after_cp, "eval_str": fmt_eval(after_cp),
            "best_san": cands[0][0] if cands else "", "best_eval": best_cp,
            "best_line": cands[0][2] if cands else "",
            "cands": [{"san": s, "eval": c, "eval_str": fmt_eval(c), "line": l} for s, c, l in cands],
            "wp_drop": round(drop, 1), "cp_loss": min(cp_loss, 1000), "kind": kind,
            "think_s": times[i] if i < len(times) else None,
        })
        board.push(mv)

    return {
        "headers": dict(game.headers),
        "depth": depth,
        "start_eval": searches[0][0],
        "plies": plies,
        "summary": summarize(plies),
        "moments": key_moments(plies),
        "chart": chart_data(plies),
        "castled": castling_plies(game),
    }


def run_engine_analysis(pgn_text: str, depth: int = 16, threads: int = 1, hash_mb: int = 64,
                        progress=None) -> dict:
    path = find_stockfish()
    if not path:
        raise RuntimeError("Stockfish is not installed on this server")
    engine = chess.engine.SimpleEngine.popen_uci(path)
    try:
        engine.configure({"Threads": threads, "Hash": hash_mb})
        return analyse_game(pgn_text, engine, depth=depth, progress=progress)
    finally:
        engine.quit()


# ---------------------------------------------------------------------------
# Derived views
# ---------------------------------------------------------------------------

def chart_data(plies: list) -> dict:
    labels = [p["label"] for p in plies]
    values = [round(max(-CHART_CLIP_PAWNS, min(CHART_CLIP_PAWNS, p["eval"] / 100)), 2) for p in plies]
    return {"labels": labels, "values": values, "kinds": [p["kind"] for p in plies]}


def summarize(plies: list) -> dict:
    out = {}
    for color in ("white", "black"):
        mine = [p for p in plies if p["color"] == color]
        known = [p["think_s"] for p in mine if p["think_s"] is not None]
        out[color] = {
            "moves": len(mine),
            "acpl": round(sum(p["cp_loss"] for p in mine) / len(mine)) if mine else 0,
            "blunders": sum(p["kind"] == "blunder" for p in mine),
            "mistakes": sum(p["kind"] == "mistake" for p in mine),
            "inaccuracies": sum(p["kind"] == "inaccuracy" for p in mine),
            "time_used_s": sum(known) if known else None,
            "longest_think": max(((p["think_s"], p["label"]) for p in mine if p["think_s"] is not None),
                                 default=None),
        }
    return out


def key_moments(plies: list) -> list:
    """Turning points worth a coach's attention, most damaging first.

    Besides plain blunders/mistakes it flags the pattern that kept costing games
    in this project's history: a long think followed, on the next move, by an
    instant reply that turns out to be an error."""
    moments = []
    for i, p in enumerate(plies):
        if p["kind"] in ("blunder", "mistake"):
            tags = []
            prev_own = plies[i - 2] if i >= 2 else None
            if (p["think_s"] is not None and p["think_s"] <= INSTANT_S and prev_own
                    and (prev_own["think_s"] or 0) >= LONG_THINK_S):
                tags.append("instant move right after a long think")
            elif p["think_s"] is not None and p["think_s"] >= LONG_THINK_S:
                tags.append("long think chose the wrong move")
            elif p["think_s"] is not None and p["think_s"] <= INSTANT_S:
                tags.append("played instantly")
            moments.append({"ply": p["ply"], "label": p["label"], "color": p["color"], "kind": p["kind"],
                            "wp_drop": p["wp_drop"], "before": fmt_eval(p["best_eval"]),
                            "after": p["eval_str"], "best": p["best_san"], "best_line": p["best_line"],
                            "think_s": p["think_s"], "tags": tags})
    moments.sort(key=lambda m: -m["wp_drop"])
    return moments


def castling_plies(game: chess.pgn.Game) -> dict:
    board = game.board()
    out = {"white": None, "black": None}
    for i, mv in enumerate(game.mainline_moves()):
        if board.is_castling(mv):
            out["white" if board.turn == chess.WHITE else "black"] = i + 1
        board.push(mv)
    return out


def fmt_secs(s: Optional[int]) -> str:
    if s is None:
        return ""
    return f"{s // 60}:{s % 60:02d}" if s < 3600 else f"{s // 3600}:{(s % 3600) // 60:02d}:{s % 60:02d}"


# ---------------------------------------------------------------------------
# Annotated PGN
# ---------------------------------------------------------------------------

def annotated_pgn(pgn_text: str, analysis: dict, notes: Optional[dict] = None,
                  annotator: str = "MyChessRating game review") -> str:
    """The game with NAGs, evals, best lines as variations, and (optionally) the
    coach's intro and per-move notes. Clock tags are kept."""
    game = read_game(pgn_text)
    if game is None:
        raise ValueError("Could not read the game's moves")
    notes = notes or {}
    game.headers["Annotator"] = annotator
    intro = (notes.get("intro") or "").strip()
    lessons = notes.get("lessons") or []
    if lessons:
        intro = (intro + "\n\nLESSONS:\n" + "\n".join(f"- {l}" for l in lessons)).strip()
    if intro:
        game.comment = intro
    by_ply = {p["ply"]: p for p in analysis.get("plies", [])}
    note_by_ply = {int(k): v for k, v in (notes.get("notes") or {}).items() if str(k).isdigit()}
    for node in list(game.mainline()):
        ply = node.ply()
        p = by_ply.get(ply)
        parts = [node.comment.strip()] if node.comment.strip() else []
        has_prose = bool(re.sub(r"\[%[^\]]*\]", "", node.comment or "").strip())
        if p:
            if p["kind"]:
                node.nags.add(NAG_FOR[p["kind"]])
            parts.append(f"[%eval {p['eval'] / 100:.2f}]" if abs(p["eval"]) < MATE_CP - 500 else "")
        if ply in note_by_ply:
            parts.append(str(note_by_ply[ply]).strip())
        elif p and p["kind"] in ("blunder", "mistake") and p["best_san"] and not has_prose:
            parts.append(f"{p['eval_str']}. Better was {p['best_san']} ({fmt_eval(p['best_eval'])}).")
        node.comment = " ".join(x for x in parts if x)
        if p and p["kind"] in ("blunder", "mistake") and p["best_line"] and node.parent is not None:
            _add_line(node.parent, p["best_line"], f"{fmt_eval(p['best_eval'])}")
    exporter = chess.pgn.StringExporter(headers=True, variations=True, comments=True)
    return game.accept(exporter)


def _add_line(parent: chess.pgn.GameNode, line_san: str, comment: str):
    board = parent.board()
    tokens = [t for t in line_san.split() if not re.match(r"^\d+\.(\.\.)?$", t)]
    tokens = [re.sub(r"^\d+\.(\.\.)?", "", t) for t in tokens]
    cur = parent
    try:
        for t in tokens:
            if not t:
                continue
            mv = board.parse_san(t)
            if cur is parent and parent.has_variation(mv):
                return  # the best move was the game move's sibling already present
            cur = cur.add_variation(mv)
            board.push(mv)
    except ValueError:
        return
    if cur is not parent:
        cur.comment = comment


def strip_annotations(pgn_text: str) -> Optional[str]:
    """The game with only its moves, headers and clock tags: no variations,
    NAGs or prose comments. Used when importing an annotated PGN so the notes
    can live alongside the engine review instead of inside the game text."""
    game = read_game(pgn_text)
    if game is None:
        return None
    game.comment = ""
    for node in [game] + list(game.mainline()):
        if node is not game:
            node.nags.clear()
            node.starting_comment = ""
            node.comment = " ".join(re.findall(r"\[%(?:clk|emt)\s+[^\]]+\]", node.comment or ""))
        for var in list(node.variations[1:]):
            node.remove_variation(var)
    return game.accept(chess.pgn.StringExporter(headers=True, variations=False, comments=True))


def comments_by_ply(pgn_text: str) -> tuple:
    """(intro, {ply: comment}) from an already annotated PGN, clock tags stripped."""
    game = read_game(pgn_text)
    if game is None:
        return "", {}
    strip = lambda c: re.sub(r"\[%[^\]]*\]", "", c or "").strip()
    out = {}
    for node in game.mainline():
        c = strip(node.comment)
        if c:
            out[node.ply()] = c
    return strip(game.comment), out


# ---------------------------------------------------------------------------
# AI coach notes
# ---------------------------------------------------------------------------

COACH_SYSTEM = """You are an experienced chess coach writing post-game notes for a junior tournament player and the parent who supports them. You are given an engine review of one game: evaluations after every move (centipawns, White's point of view), the engine's best move and line, think time per move, and the flagged turning points.

Write the way a strong coach talks after a game: the bottom line first, then the two to four moments that decided the game, each with the move, the time spent, what the engine preferred and the concrete line, then one or two lessons the player can act on next game. Be specific and honest, including about the opponent's errors; credit good moves. Never invent moves or lines that are not in the data. Use standard algebraic notation and move numbers like 19...Nxe5.

Return ONLY a JSON object, no prose around it:
{"intro": "<4-10 sentences: result, story of the game, the turning points, clock>",
 "notes": {"<ply number>": "<1-3 sentences on that move>", ...},
 "lessons": ["<short actionable lesson>", ...]}
Write notes for every blunder and mistake and for any move that deserves credit, at most 25 notes."""


def coach_prompt(analysis: dict, player_name: str = "", player_color: str = "") -> str:
    h = analysis.get("headers", {})
    lines = [
        f"Event: {h.get('Event', '')}, round {h.get('Round', '')}, {h.get('Date', '')}",
        f"White: {h.get('White', '')} ({h.get('WhiteElo', '?')})  Black: {h.get('Black', '')} ({h.get('BlackElo', '?')})",
        f"Result: {h.get('Result', '')}  Time control: {h.get('TimeControl', '') or 'unknown'}",
    ]
    if player_name:
        lines.append(f"Our player: {player_name}" + (f" (playing {player_color})" if player_color else ""))
    s = analysis.get("summary", {})
    for color in ("white", "black"):
        c = s.get(color, {})
        lines.append(f"{color.title()}: avg centipawn loss {c.get('acpl')}, {c.get('blunders')} blunders, "
                     f"{c.get('mistakes')} mistakes, {c.get('inaccuracies')} inaccuracies, "
                     f"time used {fmt_secs(c.get('time_used_s')) or 'unknown'}")
    lines.append("\nMoves (ply, move, eval after, think time, engine best if different, flag):")
    for p in analysis.get("plies", []):
        best = "" if p["best_san"] == p["san"] else f" best {p['best_san']} {fmt_eval(p['best_eval'])}: {p['best_line']}"
        flag = f" [{p['kind'].upper()}]" if p["kind"] else ""
        lines.append(f"{p['ply']} {p['label']} {p['eval_str']} {fmt_secs(p['think_s'])}{best}{flag}")
    if analysis.get("moments"):
        lines.append("\nTurning points:")
        for m in analysis["moments"][:8]:
            tags = f" ({'; '.join(m['tags'])})" if m["tags"] else ""
            lines.append(f"ply {m['ply']} {m['label']}: {m['before']} -> {m['after']}, best {m['best']}{tags}")
    return "\n".join(lines)


def parse_coach_json(text: str) -> dict:
    m = re.search(r"\{.*\}", text or "", re.S)
    if not m:
        raise ValueError("The coach notes were not in the expected format")
    data = json.loads(m.group(0))
    notes = {str(k): str(v) for k, v in (data.get("notes") or {}).items() if str(k).isdigit()}
    return {"intro": str(data.get("intro") or ""), "notes": notes,
            "lessons": [str(x) for x in (data.get("lessons") or [])][:6]}


def generate_coach_notes(client, analysis: dict, player_name: str = "", player_color: str = "",
                         model: str = "claude-opus-5-5") -> tuple:
    """Returns (notes dict, response) so the caller can price the usage."""
    response = client.beta.messages.create(
        model=model,
        max_tokens=6000,
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
        output_config={"effort": "medium"},
        system=COACH_SYSTEM,
        messages=[{"role": "user", "content": coach_prompt(analysis, player_name, player_color)}],
    )
    if getattr(response, "stop_reason", "") == "refusal":
        raise ValueError("The model declined to write notes for this game")
    text = "".join(b.text for b in response.content if getattr(b, "type", "") == "text")
    return parse_coach_json(text), response


# ---------------------------------------------------------------------------
# Name matching, scouting and season statistics
# ---------------------------------------------------------------------------

def name_tokens(name: str) -> set:
    return {t for t in re.split(r"[\s,.\-]+", (name or "").lower()) if len(t) > 1}


def same_player(query: str, candidate: str) -> bool:
    """'Rajesh, Ednit' matches 'Ednit Rajesh' and 'Rajesh, Ednit A'; a lone
    surname matches any player with that surname."""
    q, c = name_tokens(query), name_tokens(candidate)
    return bool(q) and q <= c


def score_of(result: str, color: str) -> Optional[float]:
    if result == "1-0":
        return 1.0 if color == "white" else 0.0
    if result == "0-1":
        return 0.0 if color == "white" else 1.0
    if result in ("1/2-1/2", "½-½"):
        return 0.5
    return None


def _first_moves(raw_pgn: str, n: int = 4) -> list:
    body = re.sub(r"\[[^\]]*\]", " ", raw_pgn or "")
    body = re.sub(r"\{[^}]*\}", " ", body)
    body = re.sub(r"\([^()]*\)", " ", body)
    toks = [t for t in body.split() if not re.match(r"^\d+\.+$", t) and t not in ("1-0", "0-1", "1/2-1/2", "*")]
    toks = [re.sub(r"^\d+\.+", "", t) for t in toks]
    toks = [t for t in toks if t]
    # Normalise to SAN: some broadcast exports write long algebraic ("e2e4").
    board, out = chess.Board(), []
    for t in toks[:n]:
        try:
            mv = board.parse_san(t)
        except ValueError:
            try:
                mv = chess.Move.from_uci(t.replace("-", "").lower())
            except ValueError:
                break
            if mv not in board.legal_moves:
                break
        out.append(board.san(mv))
        board.push(mv)
    return out


def player_games(games: Iterable[dict], name: str) -> list:
    """Attach the player's colour, opponent and score to each matching game."""
    out = []
    for g in games:
        if same_player(name, g.get("white", "")):
            color = "white"
        elif same_player(name, g.get("black", "")):
            color = "black"
        else:
            continue
        opp = g.get("black") if color == "white" else g.get("white")
        out.append({**g, "color": color, "opponent": opp,
                    "my_elo": g.get("white_elo") if color == "white" else g.get("black_elo"),
                    "opp_elo": g.get("black_elo") if color == "white" else g.get("white_elo"),
                    "score": score_of(g.get("result", ""), color)})
    return out


def _wdl(rows: list) -> dict:
    scored = [r["score"] for r in rows if r["score"] is not None]
    return {"games": len(rows), "w": scored.count(1.0), "d": scored.count(0.5), "l": scored.count(0.0),
            "pts": sum(scored), "pct": round(100 * sum(scored) / len(scored)) if scored else None}


def scout_report(games: list, name: str, my_names: Iterable[str] = ()) -> dict:
    """Everything worth knowing about an opponent from their games in the archive."""
    rows = player_games(games, name)
    rows.sort(key=lambda r: (r.get("date") or "", r.get("id") or 0), reverse=True)
    report = {"name": name, "total": _wdl(rows), "white": _wdl([r for r in rows if r["color"] == "white"]),
              "black": _wdl([r for r in rows if r["color"] == "black"]), "recent": rows[:25]}

    def openings(sub: list, key: Callable[[dict], str]) -> list:
        groups = defaultdict(list)
        for r in sub:
            groups[key(r)].append(r)
        return sorted(({"line": k, **_wdl(v)} for k, v in groups.items() if k), key=lambda x: -x["games"])[:8]

    def opening_name(r: dict) -> str:
        if r.get("opening"):
            return f"{r.get('eco') or ''} {r['opening']}".strip()
        return " ".join(_first_moves(r.get("raw_pgn", ""), 4))

    whites = [r for r in rows if r["color"] == "white"]
    blacks = [r for r in rows if r["color"] == "black"]
    report["first_move"] = openings(whites, lambda r: (_first_moves(r.get("raw_pgn", ""), 1) or [""])[0])
    report["white_openings"] = openings(whites, opening_name)
    report["vs_e4"] = openings([r for r in blacks if _first_moves(r.get("raw_pgn", ""), 1) == ["e4"]],
                               lambda r: (_first_moves(r.get("raw_pgn", ""), 2) + ["", ""])[1])
    report["vs_d4"] = openings([r for r in blacks if _first_moves(r.get("raw_pgn", ""), 1) == ["d4"]],
                               lambda r: " ".join(_first_moves(r.get("raw_pgn", ""), 4)[1::2]))
    report["vs_other"] = openings([r for r in blacks if _first_moves(r.get("raw_pgn", ""), 1) not in (["e4"], ["d4"])],
                                  lambda r: " ".join(_first_moves(r.get("raw_pgn", ""), 2)))

    bands = defaultdict(list)
    for r in rows:
        if r["my_elo"] and r["opp_elo"]:
            diff = r["opp_elo"] - r["my_elo"]
            bands["vs higher (+100)" if diff >= 100 else "vs lower (-100)" if diff <= -100 else "vs similar"].append(r)
    report["by_strength"] = [{"band": b, **_wdl(v)} for b, v in sorted(bands.items())]
    report["ratings"] = [{"date": r.get("date"), "elo": r["my_elo"]} for r in reversed(rows) if r["my_elo"]]
    my_names = [n for n in my_names if n]
    h2h = [r for r in rows if any(same_player(n, r["opponent"] or "") for n in my_names)]
    report["head_to_head"] = h2h
    report["h2h_summary"] = _wdl(h2h)
    return report


def season_stats(rows: list, analyses: dict) -> dict:
    """rows: player_games() output for one player; analyses: {pgn_game_id: analysis dict}.

    Results, performance, openings and - for games with an engine review - the
    error patterns that recur across games."""
    rows = sorted(rows, key=lambda r: (r.get("date") or "", r.get("id") or 0))
    total = _wdl(rows)
    rated = [r for r in rows if r["opp_elo"] and r["score"] is not None]
    perf = None
    if rated:
        avg_opp = statistics.mean(r["opp_elo"] for r in rated)
        w = sum(r["score"] == 1 for r in rated)
        l = sum(r["score"] == 0 for r in rated)
        perf = round(avg_opp + 400 * (w - l) / len(rated))
    events = defaultdict(list)
    for r in rows:
        events[r.get("event") or "?"].append(r)
    by_event = [{"event": e, "first_date": v[0].get("date"), **_wdl(v)} for e, v in events.items()]
    by_event.sort(key=lambda x: x["first_date"] or "", reverse=True)

    patterns = Counter()
    examples = defaultdict(list)
    acpl, blunders, mistakes, reviewed = [], 0, 0, 0
    for r in rows:
        a = analyses.get(r["id"])
        if not a:
            continue
        reviewed += 1
        mine = [p for p in a["plies"] if p["color"] == r["color"]]
        if mine:
            acpl.append(sum(p["cp_loss"] for p in mine) / len(mine))
        blunders += sum(p["kind"] == "blunder" for p in mine)
        mistakes += sum(p["kind"] == "mistake" for p in mine)
        ref = {"id": r["id"], "label": f"{r.get('event', '')} vs {r['opponent']}"}
        for m in a.get("moments", []):
            if m["color"] == r["color"]:
                for tag in m["tags"]:
                    patterns[tag] += 1
                    examples[tag].append({**ref, "move": m["label"]})
        evals = [p["eval"] * (1 if r["color"] == "white" else -1) for p in a["plies"]]
        if r["score"] is not None and r["score"] < 1 and evals and max(evals) >= 150:
            tag = "was clearly better (+1.5) and did not win"
            patterns[tag] += 1
            examples[tag].append({**ref, "move": f"peak {max(evals) / 100:+.1f}"})
        castled = (a.get("castled") or {}).get(r["color"])
        if r["score"] == 0 and (castled is None or castled > 40):
            tag = "lost with the king still uncastled at move 20"
            patterns[tag] += 1
            examples[tag].append({**ref, "move": "never castled" if castled is None else f"castled ply {castled}"})
        mine_t = [p["think_s"] for p in mine if p["think_s"] is not None]
        if mine_t and r["score"] is not None and r["score"] < 1:
            total_t = sum(mine_t)
            share_late = sum(mine_t[30:]) / total_t if total_t else 0
            if len(mine_t) > 40 and share_late < 0.15:
                tag = "rushed the second half (under 15% of clock after move 30)"
                patterns[tag] += 1
                examples[tag].append({**ref, "move": ""})
    return {
        "total": total, "perf": perf,
        "white": _wdl([r for r in rows if r["color"] == "white"]),
        "black": _wdl([r for r in rows if r["color"] == "black"]),
        "by_event": by_event,
        "reviewed": reviewed,
        "avg_acpl": round(statistics.mean(acpl)) if acpl else None,
        "blunders_per_game": round(blunders / reviewed, 2) if reviewed else None,
        "mistakes_per_game": round(mistakes / reviewed, 2) if reviewed else None,
        "patterns": [{"pattern": p, "count": c, "examples": examples[p][:6]} for p, c in patterns.most_common()],
        "timeline": [{"id": r["id"], "date": r.get("date"), "event": r.get("event"), "opponent": r["opponent"],
                      "opp_elo": r["opp_elo"], "color": r["color"], "result": r.get("result"),
                      "score": r["score"], "reviewed": r["id"] in analyses} for r in reversed(rows)],
    }
