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
    if 'chess.com/events/info/' in url or 'chess.com/events/' in url:
        return 'chesscom_event'
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

def _extract_chapter_ids(html: str) -> list[str]:
    """Extract Lichess study chapter IDs from broadcast round HTML."""
    m = re.search(r'"chapters"\s*:\s*(\[)', html)
    if not m:
        return []
    start = m.start(1)
    depth, i = 0, start
    while i < len(html):
        c = html[i]
        if c == '[': depth += 1
        elif c == ']':
            depth -= 1
            if depth == 0:
                break
        i += 1
    return re.findall(r'"id"\s*:\s*"([A-Za-z0-9]{8})"', html[start:i+1])

async def lichess_round_pgn(round_id: str, round_url: str = '') -> str:
    """Fetch PGN for a Lichess broadcast round.
    Tries web URL + .pgn first; if 403, falls back to chapter-by-chapter
    download via the Lichess Study API (works even when bulk export is blocked).
    """
    if round_url:
        pgn_url = round_url.split('#')[0].rstrip('/') + '.pgn'
    else:
        pgn_url = f"https://lichess.org/api/broadcast/round/{round_id}/pgn"
    async with httpx.AsyncClient(timeout=30, headers={'User-Agent': UA}, follow_redirects=True) as client:
        r = await client.get(pgn_url)
        if r.status_code == 200 and '[Event' in r.text:
            return r.text

        # Bulk export blocked — fall back to chapter-by-chapter via Study API
        # Fetch round page HTML to discover chapter IDs
        page_url = round_url.split('#')[0] if round_url else pgn_url.removesuffix('.pgn')
        rp = await client.get(page_url)
        if rp.status_code != 200:
            return ''
        chapter_ids = _extract_chapter_ids(rp.text)
        pgns = []
        for cid in chapter_ids:
            rc = await client.get(f"https://lichess.org/api/study/{round_id}/{cid}.pgn")
            if rc.status_code == 200 and '[Event' in rc.text:
                pgns.append(rc.text)
            await asyncio.sleep(0.1)
        return '\n\n'.join(pgns)

async def lichess_tour_id_from_round_page(round_url: str) -> str:
    """Scrape the broadcast round HTML to extract the tour ID."""
    url = round_url.split('#')[0]
    async with httpx.AsyncClient(timeout=15, headers={'User-Agent': UA}, follow_redirects=True) as client:
        try:
            r = await client.get(url)
            if r.status_code == 200:
                m = re.search(r'"tour"\s*:\s*\{[^}]*"id"\s*:\s*"([A-Za-z0-9]{8})"', r.text)
                if m:
                    return m.group(1)
        except Exception as e:
            log.warning("lichess_tour_id_from_round_page: %s", e)
    return ''

async def lichess_broadcast_info(tour_id: str) -> dict:
    """Return {'name': str, 'owner_id': str, 'owner_name': str, 'rounds': [...]} for a broadcast tour.
    Each round dict has: id, name, slug, url, finished.
    """
    async with httpx.AsyncClient(timeout=15, headers={'User-Agent': UA}) as client:
        r = await client.get(
            f"https://lichess.org/api/broadcast/{tour_id}",
            headers={'Accept': 'application/json'},
        )
        if r.status_code == 200:
            d = r.json()
            tour = d.get('tour', {})
            owner = tour.get('communityOwner', {})
            return {
                'name':       tour.get('name', ''),
                'owner_id':   owner.get('id', ''),
                'owner_name': owner.get('name', ''),
                'rounds':     d.get('rounds', []),
            }
    return {'name': '', 'owner_id': '', 'owner_name': '', 'rounds': []}

async def lichess_broadcast_all_rounds(tour_id: str) -> list[dict]:
    """Get all rounds for a Lichess broadcast tournament by tour ID."""
    return (await lichess_broadcast_info(tour_id)).get('rounds', [])

async def lichess_organizer_broadcasts(username: str) -> list[dict]:
    """Return all broadcast tours created by a Lichess user.
    The API returns paginated JSON; we collect all pages.
    Each item has a 'tour' dict (with id, name, slug) — rounds are NOT included
    and must be fetched separately via lichess_broadcast_info(tour_id).
    """
    tours = []
    async with httpx.AsyncClient(timeout=30, headers={'User-Agent': UA}) as client:
        page = 1
        while True:
            try:
                r = await client.get(
                    f"https://lichess.org/api/broadcast/by/{username}?page={page}",
                    headers={'Accept': 'application/json'},
                )
                if r.status_code != 200:
                    break
                d = r.json()
                results = d.get('currentPageResults', [])
                tours.extend(results)
                if len(results) < d.get('maxPerPage', 24):
                    break
                page += 1
            except Exception as e:
                log.warning("lichess_organizer_broadcasts(%s) page %d: %s", username, page, e)
                break
    return tours

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
# Chess.com Events API (OTB broadcasts via chessbomb/clono)
# ---------------------------------------------------------------------------

def parse_chesscom_event_url(url: str) -> str:
    """Extract event index slug from a chess.com events URL."""
    m = re.search(r'chess\.com/events/(?:info/)?([^/?#]+)', url)
    return m.group(1) if m else ''

async def chesscom_event_info(index_slug: str) -> dict:
    """Return event index metadata and list of rooms (sections)."""
    async with httpx.AsyncClient(timeout=15, headers={'User-Agent': UA, 'Accept': 'application/json'}, follow_redirects=True) as client:
        r = await client.post(f"https://www.chess.com/events/v1/api/index/{index_slug}")
        if r.status_code != 200:
            return {}
        d = r.json()
        idx = d.get('index', {})
        rooms = d.get('rooms', [])
        return {'name': idx.get('name', index_slug), 'id': idx.get('id'), 'slug': index_slug, 'rooms': rooms}

async def chesscom_room_data(room_slug: str) -> dict:
    """Return room details including rounds and games from chess.com events API."""
    async with httpx.AsyncClient(timeout=15, headers={'User-Agent': UA, 'Accept': 'application/json'}, follow_redirects=True) as client:
        r = await client.post(f"https://www.chess.com/events/v1/api/room/{room_slug}")
        if r.status_code != 200:
            return {}
        return r.json()

async def _chessbomb_source_url(room_id: int) -> str:
    """Fetch chessbomb Next.js page and extract sourceUrl from embedded game data."""
    async with httpx.AsyncClient(timeout=20, headers={'User-Agent': UA, 'Accept': 'text/html'}, follow_redirects=True) as client:
        try:
            r = await client.get(f"https://nxt.chessbomb.com/cp/rooms/{room_id}")
            if r.status_code != 200:
                return ''
            m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', r.text, re.DOTALL)
            if not m:
                return ''
            nd = json.loads(m.group(1))
            games = nd.get('props', {}).get('pageProps', {}).get('games', [])
            for g in games:
                src = g.get('sourceUrl', '')
                if src:
                    return src
        except Exception as e:
            log.warning("_chessbomb_source_url(%s): %s", room_id, e)
    return ''

async def chesscom_event_pgns(room_id: int, rounds: list[dict]) -> list[tuple[str, str]]:
    """Download PGN text for all rounds of a chess.com event room.

    Returns list of (round_name, pgn_text) tuples.
    Uses clono.no sourceUrl when available; falls back to empty string.
    """
    source_url = await _chessbomb_source_url(room_id)
    if not source_url:
        log.warning("chesscom_event_pgns: no sourceUrl for room %s", room_id)
        return []

    # Detect clono.no pattern: https://clono.no/pgn/{event_id}/{section_id}/{round}/games.pgn
    clono_m = re.match(r'https://clono\.no/pgn/(\d+)/(\d+)/\d+/games\.pgn', source_url)
    if not clono_m:
        log.info("chesscom_event_pgns: unknown source URL format: %s", source_url)
        return []

    event_id, section_id = clono_m.groups()
    results = []
    async with httpx.AsyncClient(timeout=30, headers={'User-Agent': UA}, follow_redirects=True) as client:
        for rnd in sorted(rounds, key=lambda r: r.get('slug', '0')):
            round_slug = rnd.get('slug', '')
            try:
                round_num = int(round_slug)
            except ValueError:
                continue
            pgn_url = f"https://clono.no/pgn/{event_id}/{section_id}/{round_num}/games.pgn"
            try:
                r = await client.get(pgn_url)
                if r.status_code == 200 and '[Event' in r.text:
                    results.append((f"Round {round_slug}", r.text))
                else:
                    log.info("chesscom_event_pgns: %s -> %s", pgn_url, r.status_code)
            except Exception as e:
                log.warning("chesscom_event_pgns round %s: %s", round_slug, e)
            await asyncio.sleep(0.2)
    return results

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

async def import_lichess_round(round_id: str, source_id: int, db_insert_fn, round_url: str = '') -> int:
    """Fetch PGN for one Lichess round and store games. Returns count inserted."""
    try:
        raw = await lichess_round_pgn(round_id, round_url)
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
