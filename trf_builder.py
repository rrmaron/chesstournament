from pathlib import Path
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
    
    c.execute("SELECT id, name, rating, score FROM players WHERE tournament_id=? ORDER BY rating DESC", (tid,))
    players_raw = c.fetchall()
    player_id_to_rank = {pid: rank for rank, (pid, _, _, _) in enumerate(players_raw, 1)}
    
    lines = []
    lines.append(f"012 {name}")
    lines.append("032 USA")
    lines.append(f"062 {len(players_raw)}")
    
    for rank, (pid, pname, rating, score) in enumerate(players_raw, 1):
        base = f"001 {rank:04d}          {pname[:33].ljust(33)} {int(rating or 0):04d}      {float(score or 0):4.1f}"
        player_line = base
        for r in range(1, current_round + 1):
            # Simplified – extend with real result lookup
            player_line += " 0000 - -"
        lines.append(player_line)
    
    conn.close()
    return "\n".join(lines) + "\n"
