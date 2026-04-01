from fastapi import FastAPI, Form, Request, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
import subprocess, os, tempfile
from pathlib import Path
import httpx

from database import get_tournaments, create_tournament, get_tournament, add_player, get_players, delete_player
from database import record_result, get_pairings_for_round, get_standings
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

# ... (add all other routes from previous messages: new tournament, player registration, round table fragment, result submit, bye submit, standings, next-round, download_trf, etc.)

# Example: Download TRF
@app.get("/tournament/{tid}/trf")
async def download_trf(tid: int):
    trf_text = build_trf(tid)
    file_path = f"trf_{tid}.trf"
    Path(file_path).write_text(trf_text)
    return FileResponse(file_path, media_type="text/plain", filename=f"tournament_{tid}.trf")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
