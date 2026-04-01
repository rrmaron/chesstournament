from fastapi import FastAPI, Form, Request, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
import subprocess
import os
from pathlib import Path
import tempfile
from typing import Optional

from database import (
    get_tournaments, create_tournament, get_tournament,
    add_player, get_players, delete_player,
    record_result, get_pairings_for_round, get_standings,
    update_current_round, store_pairing, get_player_rank_map
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
    return templates.TemplateResponse("tournament_list.html", {"request": request, "tournaments": tournaments})

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
    return templates.TemplateResponse("tournament_detail.html", {
        "request": request,
        "tournament": tournament,
        "players": players,
        "current_round": current_round
    })

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
    return templates.TemplateResponse("fragments/round_table.html", {
        "request": request,
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
    return templates.TemplateResponse("standings.html", {
        "request": request,
        "tournament": tournament,
        "standings": standings
    })

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
