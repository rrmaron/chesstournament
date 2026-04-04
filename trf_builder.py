import sqlite3
import os
from typing import Optional

DB_FILE = os.environ.get("DB_FILE", "/data/mychessrating.db" if os.path.isdir("/data") else "mychessrating.db")

def build_trf(tid: int, rounds_to_include: Optional[int] = None) -> str:
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT name, rounds, current_round FROM tournaments WHERE id=?", (tid,))
    tour = c.fetchone()
    if not tour:
        raise ValueError("Tournament not found")
    name, total_rounds, current_round = tour
    current_round = current_round or 0
    if rounds_to_include is None:
        rounds_to_include = current_round

    c.execute("""SELECT id, name, rating, score
                 FROM players WHERE tournament_id=?
                 AND (status IS NULL OR status != 'withdrawn')
                 ORDER BY rating DESC, registered_at""", (tid,))
    players_raw = c.fetchall()

    player_id_to_rank = {pid: rank for rank, (pid, _, _, _) in enumerate(players_raw, 1)}

    lines = []
    lines.append(f"012 {name}")
    lines.append("032 USA")
    lines.append(f"062 {len(players_raw)}")
    lines.append(f"072 {len(players_raw)}")
    lines.append(f"XXR {total_rounds}")
    lines.append("XXC white1")

    for rank, (pid, pname, rating, score) in enumerate(players_raw, 1):
        # bbpPairings TRF format — 91-char header, then 10 chars per round:
        # [0-8]:  "001 " + rank(4) + " "
        # [9-12]: sex(1) + title(3)
        # [13]:   space
        # [14-47]: name (34 chars, left-justified, space-padded)
        # [48-51]: rating (4 chars)
        # [52-79]: FIDE ID + birthday fields (28 spaces when blank)
        # [80-83]: score (4 chars, e.g. " 2.0")
        # [84-90]: " " + rank(4) + "  "
        name_str = pname[:34].ljust(34)
        base = (f"001 {rank:4d} "                  # [0-8]:  9 chars
                f"    "                             # [9-12]: 4 chars (sex+title blanks)
                f" "                               # [13]:   1 char
                f"{name_str}"                      # [14-47]: 34 chars
                f"{int(rating or 0):4d}"           # [48-51]: 4 chars
                f"{'':28}"                         # [52-79]: 28 chars (FIDE ID + birthday)
                f"{float(score or 0):4.1f}"        # [80-83]: 4 chars
                f" {rank:4d}  ")                   # [84-90]: 7 chars
        player_line = base
        for r in range(1, rounds_to_include + 1):
            c.execute("""SELECT result, white_id, black_id FROM results
                         WHERE tournament_id=? AND round=? AND (white_id=? OR black_id=?)""",
                      (tid, r, pid, pid))
            res_row = c.fetchone()
            if res_row:
                res, w, b = res_row
                opp_rank = player_id_to_rank.get(b if w == pid else w, 0)
                color = "w" if w == pid else "b"
                if res in ("1-0", "1F-0F"):
                    res_code = "1" if w == pid else "0"
                elif res in ("0-1", "0F-1F"):
                    res_code = "0" if w == pid else "1"
                elif res == "1/2-1/2":
                    res_code = "="
                elif "B" in res or res == "F":
                    res_code = "F"
                    color = "-"
                    opp_rank = 0
                elif res == "H":
                    res_code = "H"
                    color = "-"
                    opp_rank = 0
                else:
                    res_code = "-"
                block = f"{opp_rank:4d} {color} {res_code}  "  # 10 chars
            else:
                block = "   0 - -  "  # 10 chars: unplayed
            player_line += block
        lines.append(player_line)
    
    conn.close()
    return "\n".join(lines) + "\n"
