import sqlite3
from typing import List, Dict, Optional
from datetime import datetime

DB_FILE = "mychessrating.db"

def init_db():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    
    c.execute('''CREATE TABLE IF NOT EXISTS tournaments (
        id INTEGER PRIMARY KEY,
        name TEXT NOT NULL,
        rounds INTEGER NOT NULL DEFAULT 5,
        system TEXT DEFAULT 'dutch',
        current_round INTEGER DEFAULT 0,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP
    )''')
    
    c.execute('''CREATE TABLE IF NOT EXISTS players (
        id INTEGER PRIMARY KEY,
        tournament_id INTEGER,
        name TEXT NOT NULL,
        uscf_id TEXT,
        rating INTEGER DEFAULT 0,
        email TEXT,
        score REAL DEFAULT 0.0,
        registered_at TEXT DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY(tournament_id) REFERENCES tournaments(id)
    )''')
    
    c.execute('''CREATE TABLE IF NOT EXISTS results (
        id INTEGER PRIMARY KEY,
        tournament_id INTEGER,
        round INTEGER NOT NULL,
        white_id INTEGER,
        black_id INTEGER,
        result TEXT NOT NULL,
        FOREIGN KEY(tournament_id) REFERENCES tournaments(id)
    )''')
    
    c.execute('''CREATE TABLE IF NOT EXISTS player_round_scores (
        id INTEGER PRIMARY KEY,
        tournament_id INTEGER,
        player_id INTEGER,
        round INTEGER NOT NULL,
        score_after REAL DEFAULT 0.0,
        FOREIGN KEY(tournament_id) REFERENCES tournaments(id),
        FOREIGN KEY(player_id) REFERENCES players(id),
        UNIQUE(tournament_id, player_id, round)
    )''')
    
    conn.commit()
    conn.close()

init_db()

def create_tournament(name: str, rounds: int = 5, system: str = "dutch") -> int:
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("INSERT INTO tournaments (name, rounds, system) VALUES (?, ?, ?)", (name, rounds, system))
    tid = c.lastrowid
    conn.commit()
    conn.close()
    return tid


def get_tournaments() -> List[Dict]:
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("""
        SELECT id, name, rounds, system, current_round 
        FROM tournaments 
        ORDER BY created_at DESC
    """)
    rows = c.fetchall()
    conn.close()
    
    tournaments = []
    for row in rows:
        tournaments.append({
            "id": int(row[0]),
            "name": str(row[1]),
            "rounds": int(row[2]),
            "system": str(row[3]),
            "current_round": int(row[4]) if row[4] is not None else 0
        })
    return tournaments

def get_tournament(tid: int) -> Optional[Dict]:
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT * FROM tournaments WHERE id=?", (tid,))
    row = c.fetchone()
    conn.close()
    return dict(zip([col[0] for col in c.description], row)) if row else None

def add_player(tid: int, name: str, uscf_id: Optional[str] = None, rating: Optional[int] = None, email: Optional[str] = None):
    if uscf_id and not rating:
        try:
            import httpx, re
            r = httpx.get(f"http://www.uschess.org/msa/thin3.php?{uscf_id.strip()}", timeout=8, follow_redirects=True)
            if r.status_code == 200:
                m = re.search(r"(?i)name=[\"']?rating1[\"']?\s+value=[\"']([^\"'<>]+)[\"']", r.text)
                rating = int(m.group(1).strip()) if m else 0
        except:
            rating = 0
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("INSERT INTO players (tournament_id, name, uscf_id, rating, email) VALUES (?, ?, ?, ?, ?)",
              (tid, name.strip(), uscf_id, rating or 0, email))
    conn.commit()
    conn.close()

def get_players(tid: int) -> List[Dict]:
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT * FROM players WHERE tournament_id=? ORDER BY rating DESC, name", (tid,))
    rows = c.fetchall()
    cols = [col[0] for col in c.description]
    conn.close()
    return [dict(zip(cols, row)) for row in rows]

def delete_player(pid: int):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("DELETE FROM players WHERE id=?", (pid,))
    conn.commit()
    conn.close()

def record_result(tid: int, round_num: int, white_id: Optional[int] = None, black_id: Optional[int] = None,
                  result: Optional[str] = None, is_bye: bool = False, bye_type: str = "none"):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    
    pts_w = pts_b = 0.0
    res_code = result or "-"
    
    if is_bye:
        if bye_type == "full":
            pts_w = 1.0
            res_code = "F"  # full bye
        elif bye_type == "half":
            pts_w = 0.5
            res_code = "H"
        else:
            pts_w = 0.0
            res_code = "Z"
    elif result:
        if result in ("1-0", "1F-0F"):
            pts_w, pts_b = 1.0, 0.0
        elif result in ("0-1", "0F-1F"):
            pts_w, pts_b = 0.0, 1.0
        elif result == "1/2-1/2":
            pts_w, pts_b = 0.5, 0.5
    
    if white_id and black_id and not is_bye:
        c.execute("""SELECT id FROM results
                     WHERE tournament_id=? AND round=? AND white_id=? AND black_id=?""",
                  (tid, round_num, white_id, black_id))
        existing = c.fetchone()
        if existing:
            c.execute("UPDATE results SET result=? WHERE id=?", (result or res_code, existing[0]))
        else:
            c.execute("""INSERT INTO results
                         (tournament_id, round, white_id, black_id, result)
                         VALUES (?, ?, ?, ?, ?)""",
                      (tid, round_num, white_id, black_id, result or res_code))
    
    def update_running(pid: int, pts: float):
        c.execute("""SELECT score_after FROM player_round_scores 
                     WHERE tournament_id=? AND player_id=? AND round=?""",
                  (tid, pid, round_num - 1))
        prev = c.fetchone()
        prev_score = prev[0] if prev else 0.0
        c.execute("""INSERT OR REPLACE INTO player_round_scores 
                     (tournament_id, player_id, round, score_after) 
                     VALUES (?, ?, ?, ?)""", 
                  (tid, pid, round_num, prev_score + pts))
    
    if white_id:
        update_running(white_id, pts_w)
    if black_id:
        update_running(black_id, pts_b)
    
    conn.commit()
    conn.close()
    recalculate_scores(tid)

def recalculate_scores(tid: int):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("UPDATE players SET score = 0 WHERE tournament_id=?", (tid,))
    c.execute("SELECT white_id, black_id, result FROM results WHERE tournament_id=?", (tid,))
    for w, b, res in c.fetchall():
        if res in ("1-0", "1F-0F"):
            c.execute("UPDATE players SET score = score + 1 WHERE id=?", (w,))
        elif res in ("0-1", "0F-1F"):
            c.execute("UPDATE players SET score = score + 1 WHERE id=?", (b,))
        elif res == "1/2-1/2":
            c.execute("UPDATE players SET score = score + 0.5 WHERE id IN (?, ?)", (w, b))
    conn.commit()
    conn.close()

def get_pairings_for_round(tid: int, round_num: int) -> List[Dict]:
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("""SELECT r.id as result_id, r.white_id, r.black_id, 
                        pw.name as white_name, pb.name as black_name, r.result
                 FROM results r
                 LEFT JOIN players pw ON r.white_id = pw.id
                 LEFT JOIN players pb ON r.black_id = pb.id
                 WHERE r.tournament_id=? AND r.round=? 
                 ORDER BY r.id""", (tid, round_num))
    rows = c.fetchall()
    conn.close()
    cols = [col[0] for col in c.description]
    return [dict(zip(cols, row)) for row in rows]

def get_standings(tid: int) -> List[Dict]:
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT id, name, rating, score FROM players WHERE tournament_id=? ORDER BY score DESC, rating DESC", (tid,))
    players = [dict(zip([col[0] for col in c.description], row)) for row in c.fetchall()]
    conn.close()
    
    for p in players:
        pid = p["id"]
        conn = sqlite3.connect(DB_FILE)
        c = conn.cursor()
        c.execute("SELECT score_after FROM player_round_scores WHERE tournament_id=? AND player_id=? ORDER BY round", (tid, pid))
        running = [r[0] for r in c.fetchall()]
        conn.close()
        p["progressive"] = round(sum(running), 1)
        p["cumulative"] = p["progressive"]
        p["buchholz"] = round(p["score"] * 2, 1)  # Replace with full opponent sum in production
        p["sonneborn_berger"] = round(p["score"] * 1.5, 1)
        p["median"] = round(p["score"], 1)
        p["solkoff"] = p["buchholz"]
    
    players.sort(key=lambda x: (-x["score"], -x.get("buchholz", 0), -x.get("sonneborn_berger", 0),
                                -x.get("median", 0), -x.get("progressive", 0)))
    return players

def update_current_round(tid: int, new_round: int):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("UPDATE tournaments SET current_round=? WHERE id=?", (new_round, tid))
    conn.commit()
    conn.close()

def store_pairing(tid: int, round_num: int, white_id: int, black_id: Optional[int]):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("""INSERT INTO results (tournament_id, round, white_id, black_id, result)
                 VALUES (?, ?, ?, ?, '*')""",
              (tid, round_num, white_id, black_id))
    conn.commit()
    conn.close()

def get_player_rank_map(tid: int) -> Dict[int, int]:
    """Returns {trf_rank: player_id} using the same ordering as build_trf."""
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT id FROM players WHERE tournament_id=? ORDER BY rating DESC, registered_at", (tid,))
    rows = c.fetchall()
    conn.close()
    return {rank: row[0] for rank, row in enumerate(rows, 1)}
