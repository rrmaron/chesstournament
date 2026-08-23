"""PGN harvesting from Lichess broadcasts, Chess.com, chess-results, and direct URLs."""
import asyncio
import hashlib
import json
import re
import logging
from typing import Optional

import httpx

log = logging.getLogger(__name__)
UA = "Mozilla/5.0 (compatible; MyChessRating/1.0)"

# ---------------------------------------------------------------------------
# PGN parsing
# ---------------------------------------------------------------------------

def split_pgn(raw: str) -> list[str]:
    games, current = [], []
    for line in raw.splitlines():
        if line.startswith('[Event ') and current:
            games.append('\n'.join(current).strip())
            current = [line]
        else:
            current.append(line)
    if current:
        games.append('\n'.join(current).strip())
    return [g for g in games if g.strip() and '[' in g]

def parse_pgn_headers(pgn: str) -> dict:
    return {m.group(1): m.group(2) for m in re.finditer(r'\[(\w+)\s+"([^"]*)"\]', pgn)}

def make_game_hash(h: dict) -> str:
    key = '|'.join([h.get('Event',''), h.get('Round',''), h.get('Date',''),
                    h.get('White',''), h.get('Black','')])
    return hashlib.md5(key.encode()).hexdigest()

def _int_or_none(v: str) -> Optional[int]:
    try:
        return int(v)
    except (ValueError, TypeError):
        return None

def pgn_to_game_dict(pgn: str, source_id: int) -> Optional[dict]:
    h = parse_pgn_headers(pgn)
    if not h.get('White') or not h.get('Black'):
        return None
    return {
        'source_id':    source_id,
        'event':        h.get('Event', ''),
        'site':         h.get('Site', ''),
        'date':         h.get('Date', ''),
        'round':        h.get('Round', ''),
        'white':        h.get('White', ''),
        'black':        h.get('Black', ''),
        'result':       h.get('Result', ''),
        'white_elo':    _int_or_none(h.get('WhiteElo', '')),
        'black_elo':    _int_or_none(h.get('BlackElo', '')),
        'eco':          h.get('ECO', ''),
        'opening':      h.get('Opening', ''),
        'time_control': h.get('TimeControl', ''),
        'raw_pgn':      pgn,
        'game_hash':    make_game_hash(h),
    }

# ---------------------------------------------------------------------------
# URL detection & parsing
# ---------------------------------------------------------------------------

def detect_source_type(url: str) -> str:
    if 'lichess.org/broadcast' in url:
        return 'lichess'
    if 'chess.com/tournament' in url or 'chess.com/live/tournament' in url:
        return 'chesscom'
    if 'chess-results.com' in url:
        return 'chessresults'
    return 'direct'

def parse_lichess_broadcast_url(url: str) -> dict:
    """Extract slugs and round ID from a Lichess broadcast URL."""
    m = re.search(r'lichess\.org/broadcast/([^/?#]+)/([^/?#]+)/([A-Za-z0-9]+)', url)
    if m:
        return {'tour_slug': m.group(1), 'round_slug': m.group(2), 'round_id': m.group(3)}
    return {}

def parse_chesscom_tournament_url(url: str) -> Optional[str]:
    m = re.search(r'chess\.com/(?:live/)?tournament/(?:live/|arena/)?([^/?#]+)', url)
    return m.group(1) if m else None

# ---------------------------------------------------------------------------
# Lichess API
# ---------------------------------------------------------------------------

async def lichess_round_pgn(round_id: str) -> str:
    async with httpx.AsyncClient(timeout=30, headers={'User-Agent': UA}) as client:
        r = await client.get(
            f"https://lichess.org/api/broadcast/round/{round_id}/pgn",
            headers={'Accept': 'application/x-chess-pgn'},
        )
        r.raise_for_status()
        return r.text

async def lichess_round_info(round_id: str) -> dict:
    """Fetch broadcast+round info by round ID via NDJSON stream."""
    async with httpx.AsyncClient(timeout=15, headers={'User-Agent': UA}) as client:
        r = await client.get(
            f"https://lichess.org/api/broadcast/round/{round_id}",
            headers={'Accept': 'application/json'},
        )
        if r.status_code == 200:
            return r.json()
    return {}

async def lichess_broadcast_all_rounds(tour_id: str) -> list[dict]:
    """Get all rounds for a Lichess broadcast tournament by tour ID."""
    async with httpx.AsyncClient(timeout=15, headers={'User-Agent': UA}) as client:
        r = await client.get(
            f"https://lichess.org/api/broadcast/{tour_id}",
            headers={'Accept': 'application/json'},
        )
        if r.status_code == 200:
            return r.json().get('rounds', [])
    return []

async def lichess_organizer_broadcasts(username: str) -> list[dict]:
    """Stream all broadcasts created by a Lichess user."""
    broadcasts = []
    async with httpx.AsyncClient(timeout=30, headers={'User-Agent': UA}) as client:
        try:
            async with client.stream(
                'GET', f"https://lichess.org/api/broadcast/by/{username}",
                headers={'Accept': 'application/x-ndjson'},
            ) as r:
                if r.status_code == 200:
                    async for line in r.aiter_lines():
                        line = line.strip()
                        if line:
                            try:
                                broadcasts.append(json.loads(line))
                            except json.JSONDecodeError:
                                pass
        except Exception as e:
            log.warning("lichess_organizer_broadcasts(%s): %s", username, e)
    return broadcasts

async def lichess_recent_broadcasts(nb: int = 100) -> list[dict]:
    """Stream recent Lichess broadcasts (all users)."""
    broadcasts = []
    async with httpx.AsyncClient(timeout=30, headers={'User-Agent': UA}) as client:
        try:
            async with client.stream(
                'GET', f"https://lichess.org/api/broadcast?nb={nb}",
                headers={'Accept': 'application/x-ndjson'},
            ) as r:
                if r.status_code == 200:
                    async for line in r.aiter_lines():
                        line = line.strip()
                        if line:
                            try:
                                broadcasts.append(json.loads(line))
                            except json.JSONDecodeError:
                                pass
        except Exception as e:
            log.warning("lichess_recent_broadcasts: %s", e)
    return broadcasts

# ---------------------------------------------------------------------------
# Chess.com API
# ---------------------------------------------------------------------------

async def chesscom_tournament_info(tour_url_id: str) -> dict:
    async with httpx.AsyncClient(timeout=15, headers={'User-Agent': UA}) as client:
        r = await client.get(f"https://api.chess.com/pub/tournament/{tour_url_id}")
        if r.status_code == 200:
            return r.json()
    return {}

async def chesscom_round_pgn(tour_url_id: str, round_num: int) -> str:
    """Fetch all games for one round of a Chess.com tournament as PGN."""
    pgns = []
    async with httpx.AsyncClient(timeout=30, headers={'User-Agent': UA}) as client:
        # round index (0-based on API, 1-based in URL)
        r = await client.get(f"https://api.chess.com/pub/tournament/{tour_url_id}/{round_num}")
        if r.status_code != 200:
            return ''
        rdata = r.json()
        groups = rdata.get('groups', [])
        if not groups:
            groups = ['1']  # single group
        for group_num in range(1, len(groups) + 1):
            rg = await client.get(
                f"https://api.chess.com/pub/tournament/{tour_url_id}/{round_num}/{group_num}"
            )
            if rg.status_code != 200:
                continue
            for game in rg.json().get('games', []):
                url = game.get('url', '')
                gid_m = re.search(r'/game/(?:live|daily)/(\d+)', url)
                if not gid_m:
                    continue
                gr = await client.get(f"https://api.chess.com/pub/game/{gid_m.group(1)}")
                if gr.status_code == 200:
                    pgn = gr.json().get('pgn', '')
                    if pgn:
                        pgns.append(pgn)
                await asyncio.sleep(0.15)  # gentle rate limit
    return '\n\n'.join(pgns)

# ---------------------------------------------------------------------------
# Chess-results PGN
# ---------------------------------------------------------------------------

async def chessresults_pgn(url: str) -> str:
    """Try to fetch PGN from a chess-results.com URL."""
    base = url.split('?')[0]
    qs = url.split('?')[1] if '?' in url else ''
    qs_clean = re.sub(r'(?:^|&)art=\d+', '', qs).lstrip('&')
    async with httpx.AsyncClient(timeout=20, headers={'User-Agent': UA}, follow_redirects=True) as client:
        for suffix in ['art=4', 'art=2&prt=4']:
            sep = '&' if qs_clean else ''
            pgn_url = f"{base}?{qs_clean}{sep}{suffix}" if qs_clean else f"{base}?{suffix}"
            try:
                r = await client.get(pgn_url)
                if r.status_code == 200 and '[Event' in r.text:
                    return r.text
            except Exception:
                pass
    return ''

# ---------------------------------------------------------------------------
# Direct PGN URL
# ---------------------------------------------------------------------------

async def fetch_direct_pgn(url: str) -> str:
    async with httpx.AsyncClient(timeout=20, headers={'User-Agent': UA}, follow_redirects=True) as client:
        r = await client.get(url)
        if r.status_code == 200:
            return r.text
    return ''

# ---------------------------------------------------------------------------
# High-level import helpers (used by routes + background task)
# ---------------------------------------------------------------------------

async def import_lichess_round(round_id: str, source_id: int, db_insert_fn) -> int:
    """Fetch PGN for one Lichess round and store games. Returns count inserted."""
    try:
        raw = await lichess_round_pgn(round_id)
    except Exception as e:
        log.warning("import_lichess_round(%s): %s", round_id, e)
        return 0
    games = [pgn_to_game_dict(g, source_id) for g in split_pgn(raw)]
    games = [g for g in games if g]
    return db_insert_fn(games)

async def import_pgn_text(raw: str, source_id: int, db_insert_fn) -> int:
    games = [pgn_to_game_dict(g, source_id) for g in split_pgn(raw)]
    games = [g for g in games if g]
    return db_insert_fn(games)
