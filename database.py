import sqlite3
from typing import List, Dict, Optional
from datetime import datetime

import os
DB_FILE = os.environ.get("DB_FILE", "/data/mychessrating.db" if os.path.isdir("/data") else "mychessrating.db")

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
    
    c.execute('''CREATE TABLE IF NOT EXISTS uscf_members (
        uscf_id TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        rating INTEGER DEFAULT 0,
        state TEXT,
        expiry TEXT,
        fide_id TEXT
    )''')
    c.execute('CREATE INDEX IF NOT EXISTS idx_uscf_members_name ON uscf_members(name)')

    # Migrations for existing DBs
    for sql in [
        "ALTER TABLE uscf_members ADD COLUMN fide_id TEXT",
        "ALTER TABLE players ADD COLUMN fide_id TEXT",
        "ALTER TABLE players ADD COLUMN expiry TEXT",
    ]:
        try:
            c.execute(sql)
        except Exception:
            pass

    conn.commit()
    conn.close()

init_db()


def import_uscf_members(raw_bytes: bytes) -> int:
    """Stream-parse and bulk-insert TSV bytes from the USCF allratings download."""
    import io
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    # Speed-optimised pragmas for bulk load
    c.execute("PRAGMA synchronous = OFF")
    c.execute("PRAGMA journal_mode = WAL")
    c.execute("PRAGMA cache_size = -64000")  # 64 MB cache
    c.execute("DELETE FROM uscf_members")
    SQL = "INSERT OR REPLACE INTO uscf_members (uscf_id, name, rating, state, expiry, fide_id) VALUES (?,?,?,?,?,?)"
    batch = []
    count = 0
    first_row_written = False
    reader = io.TextIOWrapper(io.BytesIO(raw_bytes), encoding="utf-8", errors="replace")
    for line in reader:
        parts = line.split('\t')
        if len(parts) < 5:
            continue
        uscf_id = parts[0].strip()
        if not uscf_id.isdigit():
            continue
        if not first_row_written or uscf_id == "31625896":
            import json as _json
            debug_path = DB_FILE.replace(".db", "_col_debug.json")
            label = uscf_id if uscf_id == "31625896" else "first_row"
            try:
                with open(debug_path) as _f:
                    existing = _json.load(_f)
            except Exception:
                existing = {}
            existing[label] = {str(i): v.strip() for i, v in enumerate(parts[:20])}
            with open(debug_path, "w") as _f:
                _json.dump(existing, _f, indent=2)
            first_row_written = True
        name   = parts[1].strip()
        state  = parts[2].strip() if len(parts) > 2 else ""
        expiry = parts[4].strip() if len(parts) > 4 else ""
        # col 6 = FIDE ID (7-9 digits), col 9 = regular rating (e.g. "1570*")
        import re as _re
        fide_raw = parts[6].strip() if len(parts) > 6 else ""
        fide_id  = fide_raw if _re.match(r'^\d{7,9}$', fide_raw) else None
        raw_r  = parts[9].strip() if len(parts) > 9 else ""
        m      = _re.search(r'(\d+)', raw_r)
        rating = int(m.group(1)) if m else 0
        batch.append((uscf_id, name, rating, state, expiry, fide_id))
        if len(batch) >= 50000:
            c.executemany(SQL, batch)
            conn.commit()
            count += len(batch)
            batch = []
    if batch:
        c.executemany(SQL, batch)
        conn.commit()
        count += len(batch)
    conn.close()
    return count


def search_uscf_members(q: str, limit: int = 12) -> List[Dict]:
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    words = q.upper().split()
    if not words:
        conn.close()
        return []
    # Each word must match as a prefix of either the last name or the first name.
    # Names are stored as "LASTNAME, FIRSTNAME", so:
    #   last-name prefix  → name LIKE 'WORD%'
    #   first-name prefix → name LIKE '%, WORD%'
    conditions = " AND ".join(["(name LIKE ? OR name LIKE ?)" for _ in words])
    params = []
    for w in words:
        params.append(f"{w}%")      # last-name prefix
        params.append(f"%, {w}%")   # first-name prefix
    params.append(limit)
    c.execute(
        f"SELECT uscf_id, name, rating, state, fide_id FROM uscf_members WHERE {conditions} ORDER BY rating DESC LIMIT ?",
        params
    )
    rows = c.fetchall()
    conn.close()
    return [{"uscf_id": r[0], "name": r[1], "rating": r[2], "state": r[3], "fide_id": r[4]} for r in rows]


def lookup_uscf_member(uscf_id: str) -> Optional[Dict]:
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT uscf_id, name, rating, state, fide_id, expiry FROM uscf_members WHERE uscf_id=?", (uscf_id.strip(),))
    row = c.fetchone()
    conn.close()
    if row:
        return {"uscf_id": row[0], "name": row[1], "rating": row[2], "state": row[3], "fide_id": row[4], "expiry": row[5]}
    return None


def get_uscf_db_count() -> int:
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT COUNT(*) FROM uscf_members")
    count = c.fetchone()[0]
    conn.close()
    return count

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

def add_player(tid: int, name: str, uscf_id: Optional[str] = None, rating: Optional[int] = None,
               email: Optional[str] = None, fide_id: Optional[str] = None, expiry: Optional[str] = None):
    if uscf_id:
        # Always check local DB — fills rating, fide_id, expiry regardless of what form submitted
        local = lookup_uscf_member(uscf_id)
        if local:
            if not rating:
                rating = local.get("rating") or 0
            if not fide_id:
                fide_id = local.get("fide_id")
            if not expiry:
                expiry = local.get("expiry")
        elif not rating:
            # Fall back to thin3.php only if not in local DB
            try:
                import httpx, re
                headers = {"User-Agent": "Mozilla/5.0 (compatible; MyChessRating/1.0)"}
                r = httpx.get(f"http://www.uschess.org/msa/thin3.php?{uscf_id.strip()}", timeout=8, follow_redirects=True, headers=headers)
                if r.status_code == 200:
                    m = re.search(r"name=rating1[^>]+value='([^']+)'", r.text)
                    if m:
                        num = re.search(r"(\d+)", m.group(1))
                        rating = int(num.group(1)) if num else 0
            except:
                rating = 0
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("INSERT INTO players (tournament_id, name, uscf_id, rating, email, fide_id, expiry) VALUES (?, ?, ?, ?, ?, ?, ?)",
              (tid, name.strip(), uscf_id, rating or 0, email, fide_id, expiry))
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

def delete_player(pid: int) -> Optional[int]:
    """Delete player and return their tournament_id."""
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT tournament_id FROM players WHERE id=?", (pid,))
    row = c.fetchone()
    tid = row[0] if row else None
    c.execute("DELETE FROM players WHERE id=?", (pid,))
    conn.commit()
    conn.close()
    return tid

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
