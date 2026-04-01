import sqlite3

DB_FILE = "mychessrating.db"

def build_trf(tid: int) -> str:
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT name, rounds, current_round FROM tournaments WHERE id=?", (tid,))
    tour = c.fetchone()
    if not tour:
        raise ValueError("Tournament not found")
    name, total_rounds, current_round = tour
    current_round = current_round or 0
    
    c.execute("""SELECT id, name, rating, score 
                 FROM players WHERE tournament_id=? 
                 ORDER BY rating DESC, registered_at""", (tid,))
    players_raw = c.fetchall()
    
    player_id_to_rank = {pid: rank for rank, (pid, _, _, _) in enumerate(players_raw, 1)}
    
    lines = []
    lines.append(f"012 {name}")
    lines.append("032 USA")
    lines.append(f"062 {len(players_raw)}")
    lines.append(f"072 {len(players_raw)}")
    
    for rank, (pid, pname, rating, score) in enumerate(players_raw, 1):
        base = f"001 {rank:04d}          {pname[:33].ljust(33)} {int(rating or 0):04d}      {float(score or 0):4.1f}"
        player_line = base
        for r in range(1, current_round + 1):
            # Fetch result for this player/round (simplified; expand for full accuracy)
            c.execute("""SELECT result, white_id, black_id FROM results 
                         WHERE tournament_id=? AND round=? AND (white_id=? OR black_id=?)""",
                      (tid, r, pid, pid))
            res_row = c.fetchone()
            if res_row:
                res, w, b = res_row
                opp_rank = player_id_to_rank.get(b if w == pid else w, 0)
                color = "w" if w == pid else "b"
                # Map to TRF codes (FIDE 2026 / bbp compatible)
                if res in ("1-0", "1F-0F"):
                    res_code = "1" if w == pid else "0"
                elif res in ("0-1", "0F-1F"):
                    res_code = "0" if w == pid else "1"
                elif res == "1/2-1/2":
                    res_code = "="
                elif "B" in res or res == "F":
                    res_code = "F"  # full bye
                elif res == "H":
                    res_code = "H"
                else:
                    res_code = "-"
                block = f" {opp_rank:04d} {color} {res_code}"
            else:
                block = " 0000 - -"
            player_line += block
        lines.append(player_line)
    
    conn.close()
    return "\n".join(lines) + "\n"
