from fastapi import FastAPI, Form, Request, HTTPException, UploadFile, File
from fastapi.responses import HTMLResponse, RedirectResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
import subprocess
import os
from pathlib import Path
import tempfile
import html
import re
import httpx
from typing import Optional

from database import (
    get_tournaments, create_tournament, get_tournament,
    add_player, get_players, delete_player,
    record_result, get_pairings_for_round, get_standings,
    update_current_round, store_pairing, get_player_rank_map,
    import_uscf_members, search_uscf_members, lookup_uscf_member, get_uscf_db_count
)
from trf_builder import build_trf

app = FastAPI(title="MyChessRating Pairings")
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")

BBP_PATH = "./bbpPairings"
if os.name == "nt":
    BBP_PATH += ".exe"

@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    tournaments = get_tournaments()
    return templates.TemplateResponse(request=request, name="tournament_list.html", context={"tournaments": tournaments})

@app.post("/tournament")
async def new_tournament(name: str = Form(...), rounds: int = Form(5), system: str = Form("dutch")):
    tid = create_tournament(name, rounds, system)
    return RedirectResponse(f"/tournament/{tid}", status_code=303)

@app.get("/tournament/{tid}", response_class=HTMLResponse)
async def tournament_detail(request: Request, tid: int):
    tournament = get_tournament(tid)
    if not tournament:
        raise HTTPException(404)
    players = get_players(tid)
    current_round = tournament.get("current_round", 0) or 1
    return templates.TemplateResponse(request=request, name="tournament_detail.html", context={
        "tournament": tournament,
        "players": players,
        "current_round": current_round
    })

def _parse_uscf_thin3(body: str) -> dict:
    """Parse name and rating from USCF thin3.php HTML response."""
    # Attributes appear between name= and value= so match across them: name=memname ...attrs... value='...'
    name_m = re.search(r"name=memname[^>]+value='([^']+)'", body)
    rating_m = re.search(r"name=rating1[^>]+value='([^']+)'", body)
    name = ""
    if name_m:
        raw = name_m.group(1).strip()
        if ", " in raw:  # "DOE, JOHN" → "John Doe"
            parts = raw.split(", ", 1)
            name = f"{parts[1]} {parts[0]}".title()
        else:
            name = raw.title()
    rating = 0
    if rating_m:
        # Value is like "1478* 2025-12-01" — extract leading digits only
        num_m = re.search(r"(\d+)", rating_m.group(1))
        if num_m:
            rating = int(num_m.group(1))
    return {"name": name, "rating": rating}

def _format_uscf_name(raw: str) -> str:
    """Convert 'DOE, JOHN' → 'John Doe', or title-case if no comma."""
    raw = raw.strip()
    if ", " in raw:
        ln, fn = raw.split(", ", 1)
        return f"{fn.title()} {ln.title()}"
    return raw.title()

def _lookup_oob(full_name: str, rating: int, source: str = "") -> str:
    safe_name = html.escape(full_name)
    src = f" <span class='text-muted'>({html.escape(source)})</span>" if source else ""
    preview = f'<div id="uscf-preview"><span class="text-success small">✓ {safe_name} — Rating: {rating or "Unrated"}{src}</span></div>'
    name_oob = f'<input type="text" id="player-name" name="name" class="form-control" value="{safe_name}" required placeholder="Full name" hx-swap-oob="true">'
    rating_oob = f'<input type="number" id="player-rating" name="rating" class="form-control" value="{html.escape(str(rating))}" placeholder="Optional" hx-swap-oob="true">'
    return preview + name_oob + rating_oob

@app.get("/api/uscf-lookup", response_class=HTMLResponse)
async def uscf_lookup(uscf_id: str = ""):
    uscf_id = uscf_id.strip()
    empty = '<div id="uscf-preview"></div>'
    if len(uscf_id) < 7:
        return HTMLResponse(empty)
    # 1. Try local DB first
    local = lookup_uscf_member(uscf_id)
    if local:
        name = _format_uscf_name(local["name"])
        return HTMLResponse(_lookup_oob(name, local["rating"], "local DB"))
    # 2. Fall back to USCF thin3.php
    try:
        headers = {"User-Agent": "Mozilla/5.0 (compatible; MyChessRating/1.0)"}
        async with httpx.AsyncClient(timeout=8, follow_redirects=True) as client:
            r = await client.get(f"http://www.uschess.org/msa/thin3.php?{uscf_id}", headers=headers)
        if r.status_code != 200 or "memname" not in r.text:
            return HTMLResponse('<div id="uscf-preview"><span class="text-warning small">USCF ID not found</span></div>')
        data = _parse_uscf_thin3(r.text)
        return HTMLResponse(_lookup_oob(data["name"], data["rating"], "uschess.org"))
    except Exception as e:
        import logging
        logging.exception("USCF lookup failed")
        return HTMLResponse(f'<div id="uscf-preview"><span class="text-danger small">Lookup failed: {html.escape(str(e))}</span></div>')

def _suggestions_html(results: list) -> str:
    if not results:
        return '<div id="uscf-suggestions"></div>'
    items = ""
    for r in results:
        display = _format_uscf_name(r["name"])
        rating = r.get("rating") or ""
        dn = html.escape(display)
        items += (
            f'<button type="button" class="list-group-item list-group-item-action py-1 small"'
            f' data-name="{dn}" data-id="{html.escape(r["uscf_id"])}" data-rating="{html.escape(str(rating))}"'
            f' onclick="fillUscfPlayer(this)">'
            f'{dn} <span class="text-muted">{html.escape(r["uscf_id"])}</span>'
            f'{" — " + str(rating) if rating else ""}'
            f'</button>'
        )
    return (
        '<div id="uscf-suggestions">'
        '<div class="list-group mt-1" style="max-height:220px;overflow-y:auto;position:absolute;z-index:100;width:100%">'
        f'{items}</div></div>'
    )

@app.get("/api/uscf-search", response_class=HTMLResponse)
async def uscf_search(name: str = ""):
    q = name.strip()
    empty = '<div id="uscf-suggestions"></div>'
    if len(q) < 2:
        return HTMLResponse(empty)
    # 1. Try local DB first
    local = search_uscf_members(q)
    if local:
        return HTMLResponse(_suggestions_html(local))
    # 2. Fall back to USCF thin2.php
    try:
        headers = {"User-Agent": "Mozilla/5.0 (compatible; MyChessRating/1.0)"}
        parts = q.split()
        data = {"memln": parts[-1], "memfn": " ".join(parts[:-1]), "mode": "Search"}
        async with httpx.AsyncClient(timeout=8, follow_redirects=True) as client:
            r = await client.post("http://www.uschess.org/msa/thin2.php", data=data, headers=headers)
        rows = re.findall(r'<td>(\d{5,8})</td>\s*<td>([^<]+)</td>\s*<td>([^<]+)</td>', r.text)
        results = []
        for uid, raw_name, info in rows[:12]:
            rating_m = re.search(r'(\d{3,4})\*?(?:\s|$)', info)
            results.append({
                "uscf_id": uid,
                "name": raw_name.strip(),
                "rating": int(rating_m.group(1)) if rating_m else 0,
            })
        return HTMLResponse(_suggestions_html(results))
    except Exception:
        return HTMLResponse(empty)

@app.post("/tournament/{tid}/player")
async def register_player(tid: int, name: str = Form(...), uscf_id: Optional[str] = Form(None),
                          rating: Optional[int] = Form(None), email: Optional[str] = Form(None)):
    add_player(tid, name, uscf_id, rating, email)
    return RedirectResponse(f"/tournament/{tid}", status_code=303)

@app.post("/player/{pid}/delete")
async def remove_player(pid: int):
    delete_player(pid)
    return RedirectResponse("/", status_code=303)

# HTMX: Round table fragment
@app.get("/tournament/{tid}/round/{round_num}/table", response_class=HTMLResponse)
async def round_table_fragment(request: Request, tid: int, round_num: int):
    tournament = get_tournament(tid)
    if not tournament:
        raise HTTPException(404)
    pairings = get_pairings_for_round(tid, round_num)
    standings = get_standings(tid)
    return templates.TemplateResponse(request=request, name="fragments/round_table.html", context={
        "tournament": tournament,
        "round_num": round_num,
        "pairings": pairings,
        "standings": standings
    })

# Submit normal result (HTMX)
@app.post("/result/submit", response_class=HTMLResponse)
async def submit_result_htmx(
    request: Request,
    tid: int = Form(...),
    round_num: int = Form(...),
    white_id: int = Form(...),
    black_id: int = Form(...),
    result: str = Form(...)
):
    record_result(tid, round_num, white_id, black_id, result)
    return await round_table_fragment(request, tid, round_num)

# Submit bye/forfeit
@app.post("/tournament/{tid}/round/{round_num}/bye", response_class=HTMLResponse)
async def submit_bye(
    request: Request,
    tid: int,
    round_num: int,
    player_id: int = Form(...),
    bye_type: str = Form(...),  # full, half, zero
    is_forfeit: bool = Form(False),
    opponent_id: Optional[int] = Form(None)
):
    if is_forfeit and opponent_id:
        result_str = "1F-0F" if bye_type == "full" else "0F-1F"
        record_result(tid, round_num, player_id, opponent_id, result_str)
    else:
        record_result(tid, round_num, white_id=player_id, is_bye=True, bye_type=bye_type)
    return await round_table_fragment(request, tid, round_num)

# Generate next round
@app.post("/tournament/{tid}/next-round", response_class=HTMLResponse)
async def generate_next_round(request: Request, tid: int):
    tournament = get_tournament(tid)
    if not tournament:
        raise HTTPException(404)
    current = tournament.get("current_round", 0)
    next_r = current + 1

    # Build TRF with only the completed rounds (before advancing current_round)
    trf_text = build_trf(tid, rounds_to_include=current)
    rank_map = get_player_rank_map(tid)

    trf_fd, trf_path = tempfile.mkstemp(suffix=".trf")
    out_path = trf_path + ".out"
    try:
        with os.fdopen(trf_fd, "w") as f:
            f.write(trf_text)

        proc = subprocess.run(
            [BBP_PATH, "--dutch", trf_path, "-p", out_path],
            capture_output=True, text=True, timeout=30
        )
        if proc.returncode != 0:
            raise HTTPException(500, detail=f"bbpPairings error: {proc.stderr.strip() or proc.stdout.strip()}")

        with open(out_path) as f:
            pairing_lines = f.read().strip().splitlines()

        update_current_round(tid, next_r)

        for line in pairing_lines:
            parts = line.strip().split()
            if len(parts) == 2:
                w_rank, b_rank = int(parts[0]), int(parts[1])
                white_id = rank_map.get(w_rank)
                black_id = rank_map.get(b_rank) if b_rank != 0 else None
                if white_id:
                    store_pairing(tid, next_r, white_id, black_id)
    finally:
        Path(trf_path).unlink(missing_ok=True)
        Path(out_path).unlink(missing_ok=True)

    return await round_table_fragment(request, tid, next_r)

# Download TRF-2026
@app.get("/tournament/{tid}/trf")
async def download_trf(tid: int):
    trf_text = build_trf(tid)
    file_path = f"trf_{tid}.trf"
    Path(file_path).write_text(trf_text)
    return FileResponse(file_path, media_type="text/plain", filename=f"tournament_{tid}.trf")

# Standings
@app.get("/tournament/{tid}/standings", response_class=HTMLResponse)
async def view_standings(request: Request, tid: int):
    tournament = get_tournament(tid)
    if not tournament:
        raise HTTPException(404)
    standings = get_standings(tid)
    return templates.TemplateResponse(request=request, name="standings.html", context={
        "tournament": tournament,
        "standings": standings
    })

@app.get("/uscf-db", response_class=HTMLResponse)
async def uscf_db_page(request: Request, imported: Optional[int] = None):
    count = get_uscf_db_count()
    return templates.TemplateResponse(request=request, name="uscf_db.html", context={
        "count": count,
        "imported": imported,
    })

@app.post("/uscf-db/upload")
async def uscf_db_upload(file: UploadFile = File(...)):
    content = await file.read()
    text = content.decode("utf-8", errors="replace")
    count = import_uscf_members(text.splitlines())
    return RedirectResponse(f"/uscf-db?imported={count}", status_code=303)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
