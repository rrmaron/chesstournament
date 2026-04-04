from fastapi import FastAPI, Form, Request, HTTPException, UploadFile, File, Depends
from fastapi.responses import HTMLResponse, RedirectResponse, FileResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware
import subprocess
import os
from pathlib import Path
import tempfile
import html
import re
import httpx
from typing import Optional, List
import stripe

from database import (
    get_tournaments, create_tournament, get_tournament,
    add_player, get_players, delete_player,
    record_result, get_pairings_for_round, get_standings,
    update_current_round, store_pairing, get_player_rank_map,
    import_uscf_members, search_uscf_members, lookup_uscf_member, get_uscf_db_count,
    get_user_by_username, get_user_by_email, get_user_by_phone,
    verify_password, create_user, create_pending_user, list_users,
    delete_user, update_user_password,
    create_verification_token, check_and_consume_token, activate_user,
    get_setting, set_setting,
    update_tournament_settings, get_player,
    register_player_public, set_player_status,
    update_player_payment, update_player_bye_request,
    get_user_profile, update_user_profile,
)
from trf_builder import build_trf
from auth import get_current_user, require_login, require_td, require_admin
from notify import send_verification_email, send_verification_sms
from fide import calculate_rating, generate_pdf as fide_generate_pdf

SECRET_KEY = os.environ.get("SECRET_KEY", "dev-secret-key-change-in-production")
STRIPE_SECRET_KEY = os.environ.get("STRIPE_SECRET_KEY", "")

app = FastAPI(title="MyChessRating Pairings")
app.add_middleware(SessionMiddleware, secret_key=SECRET_KEY)
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")

BBP_PATH = "./bbpPairings"
if os.name == "nt":
    BBP_PATH += ".exe"


# ---------------------------------------------------------------------------
# Exception handlers
# ---------------------------------------------------------------------------

@app.exception_handler(401)
async def not_authenticated(request: Request, exc: HTTPException):
    if request.headers.get("HX-Request"):
        return Response(headers={"HX-Redirect": "/login"}, status_code=200)
    if request.session.get("pending_user_id"):
        return RedirectResponse("/verify", status_code=303)
    next_url = request.url.path
    return RedirectResponse(url=f"/login?next={next_url}", status_code=303)

@app.exception_handler(403)
async def forbidden(request: Request, exc: HTTPException):
    return HTMLResponse(
        f'<div class="alert alert-danger">Access denied: {html.escape(exc.detail or "Insufficient permissions")}</div>',
        status_code=403
    )


# ---------------------------------------------------------------------------
# Auth routes
# ---------------------------------------------------------------------------

@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request, next: str = "/"):
    user = get_current_user(request)
    if user:
        return RedirectResponse("/", status_code=303)
    return templates.TemplateResponse(request=request, name="login.html", context={
        "next": next,
        "error": None,
        "login_message": get_setting("login_message", ""),
    })

@app.post("/login", response_class=HTMLResponse)
async def login_submit(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    next: str = Form("/"),
):
    user = get_user_by_username(username)
    if not user or not verify_password(password, user["password_hash"]):
        return templates.TemplateResponse(request=request, name="login.html",
                                          context={"next": next, "error": "Invalid username or password"})
    if user.get("status") == "pending":
        # Account exists but not verified — resend OTP and send to verify page
        contact = user.get("email") or user.get("phone")
        channel = "email" if user.get("email") else "sms"
        token = create_verification_token(user["id"], channel, contact)
        try:
            if channel == "email":
                await send_verification_email(contact, token)
            else:
                await send_verification_sms(contact, token)
        except Exception:
            pass
        request.session["pending_user_id"] = user["id"]
        request.session["pending_contact"] = contact
        request.session["pending_channel"] = channel
        return RedirectResponse("/verify", status_code=303)
    request.session["user"] = {"id": user["id"], "username": user["username"], "role": user["role"]}
    return RedirectResponse(next if next.startswith("/") else "/", status_code=303)

@app.post("/logout")
async def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/login", status_code=303)


# ---------------------------------------------------------------------------
# Self-registration
# ---------------------------------------------------------------------------

@app.get("/register", response_class=HTMLResponse)
async def register_page(request: Request):
    if get_current_user(request):
        return RedirectResponse("/", status_code=303)
    return templates.TemplateResponse(request=request, name="register.html", context={
        "error": None,
        "reg_method": get_setting("registration_method", "both"),
    })

@app.post("/register", response_class=HTMLResponse)
async def register_submit(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    confirm: str = Form(...),
    channel: str = Form(...),       # "email" or "sms"
    contact: str = Form(...),       # email address or phone number
):
    error = None
    username = username.strip()
    contact = contact.strip()
    reg_method = get_setting("registration_method", "both")

    if reg_method != "both" and channel != reg_method:
        channel = reg_method  # silently correct if client sent wrong value

    if password != confirm:
        error = "Passwords do not match."
    elif len(password) < 6:
        error = "Password must be at least 6 characters."
    elif get_user_by_username(username):
        error = "Username already taken."
    elif channel == "email" and get_user_by_email(contact):
        error = "An account with that email already exists."
    elif channel == "sms" and get_user_by_phone(contact):
        error = "An account with that phone number already exists."

    if error:
        return templates.TemplateResponse(request=request, name="register.html",
                                          context={"error": error, "reg_method": reg_method})

    email = contact if channel == "email" else None
    phone = contact if channel == "sms" else None

    if get_setting("require_verification", "1") == "0":
        # Verification disabled — activate immediately
        uid = create_pending_user(username, password, email=email, phone=phone)
        activate_user(uid)
        from database import DB_FILE
        import sqlite3 as _sq
        row = _sq.connect(DB_FILE).execute("SELECT id, username, role FROM users WHERE id=?", (uid,)).fetchone()
        if row:
            request.session["user"] = {"id": row[0], "username": row[1], "role": row[2]}
        return RedirectResponse("/", status_code=303)

    uid = create_pending_user(username, password, email=email, phone=phone)
    token = create_verification_token(uid, channel, contact)

    try:
        if channel == "email":
            await send_verification_email(contact, token)
        else:
            await send_verification_sms(contact, token)
    except Exception as e:
        error = str(e)
        return templates.TemplateResponse(request=request, name="register.html",
                                          context={"error": error, "reg_method": reg_method})

    request.session["pending_user_id"] = uid
    request.session["pending_contact"] = contact
    request.session["pending_channel"] = channel
    return RedirectResponse("/verify", status_code=303)

@app.get("/verify", response_class=HTMLResponse)
async def verify_page(request: Request):
    if get_current_user(request):
        return RedirectResponse("/", status_code=303)
    if not request.session.get("pending_user_id"):
        return RedirectResponse("/register", status_code=303)
    return templates.TemplateResponse(request=request, name="verify.html", context={
        "contact": request.session.get("pending_contact"),
        "channel": request.session.get("pending_channel"),
        "error": None,
    })

@app.post("/verify", response_class=HTMLResponse)
async def verify_submit(request: Request, code: str = Form(...)):
    uid = request.session.get("pending_user_id")
    if not uid:
        return RedirectResponse("/register", status_code=303)

    if not check_and_consume_token(uid, code.strip()):
        return templates.TemplateResponse(request=request, name="verify.html", context={
            "contact": request.session.get("pending_contact"),
            "channel": request.session.get("pending_channel"),
            "error": "Invalid or expired code. Request a new one.",
        })

    activate_user(uid)
    from database import DB_FILE
    import sqlite3 as _sq
    conn = _sq.connect(DB_FILE)
    row = conn.execute("SELECT id, username, role FROM users WHERE id=?", (uid,)).fetchone()
    conn.close()

    for key in ("pending_user_id", "pending_contact", "pending_channel"):
        request.session.pop(key, None)
    if row:
        request.session["user"] = {"id": row[0], "username": row[1], "role": row[2]}
    return RedirectResponse("/", status_code=303)

@app.post("/verify/resend")
async def verify_resend(request: Request):
    uid = request.session.get("pending_user_id")
    channel = request.session.get("pending_channel")
    contact = request.session.get("pending_contact")
    if uid and channel and contact:
        token = create_verification_token(uid, channel, contact)
        try:
            if channel == "email":
                await send_verification_email(contact, token)
            else:
                await send_verification_sms(contact, token)
        except Exception:
            pass
    return RedirectResponse("/verify", status_code=303)


# ---------------------------------------------------------------------------
# User management (admin only)
# ---------------------------------------------------------------------------

@app.get("/users", response_class=HTMLResponse)
async def users_page(request: Request, saved: Optional[str] = None, user: dict = Depends(require_admin)):
    users = list_users()
    return templates.TemplateResponse(request=request, name="users.html", context={
        "users": users,
        "current_user": user,
        "reg_method": get_setting("registration_method", "both"),
        "login_message": get_setting("login_message", ""),
        "require_verification": get_setting("require_verification", "1") == "1",
        "saved": saved == "1",
    })

@app.post("/admin/settings")
async def save_settings(
    request: Request,
    registration_method: str = Form(...),
    _user: dict = Depends(require_admin),
):
    if registration_method in ("email", "sms", "both"):
        set_setting("registration_method", registration_method)
    form = await request.form()
    set_setting("require_verification", "1" if form.get("require_verification") else "0")
    return RedirectResponse("/users?saved=1", status_code=303)

@app.post("/admin/settings/login-message")
async def save_login_message(
    login_message: str = Form(""),
    _user: dict = Depends(require_admin),
):
    set_setting("login_message", login_message.strip())
    return RedirectResponse("/users?saved=1", status_code=303)

@app.post("/users")
async def create_user_route(
    username: str = Form(...),
    password: str = Form(...),
    role: str = Form("viewer"),
    _user: dict = Depends(require_admin),
):
    try:
        create_user(username, password, role)
    except Exception:
        pass  # duplicate username — silently ignore for now
    return RedirectResponse("/users", status_code=303)

@app.post("/users/{uid}/delete")
async def delete_user_route(uid: int, current: dict = Depends(require_admin)):
    if uid != current["id"]:  # prevent self-deletion
        delete_user(uid)
    return RedirectResponse("/users", status_code=303)

@app.post("/users/{uid}/password")
async def change_password_route(
    uid: int,
    new_password: str = Form(...),
    _user: dict = Depends(require_admin),
):
    update_user_password(uid, new_password)
    return RedirectResponse("/users", status_code=303)


# ---------------------------------------------------------------------------
# Main app routes
# ---------------------------------------------------------------------------

@app.get("/", response_class=HTMLResponse)
async def home(request: Request, user: dict = Depends(require_login)):
    return RedirectResponse("/player-lookup", status_code=302)

@app.get("/tournaments", response_class=HTMLResponse)
async def tournaments_home(request: Request, user: dict = Depends(require_td)):
    tournaments = get_tournaments()
    return templates.TemplateResponse(request=request, name="tournament_list.html",
                                      context={"tournaments": tournaments, "current_user": user})

@app.post("/tournament")
async def new_tournament(
    name: str = Form(...),
    rounds: int = Form(5),
    system: str = Form("dutch"),
    entry_fee: float = Form(0),
    _user: dict = Depends(require_td),
):
    tid = create_tournament(name, rounds, system, entry_fee)
    return RedirectResponse(f"/tournament/{tid}", status_code=303)

@app.get("/tournament/{tid}", response_class=HTMLResponse)
async def tournament_detail(request: Request, tid: int, imported: Optional[int] = None,
                             user: dict = Depends(require_login)):
    import json as _json
    tournament = get_tournament(tid)
    if not tournament:
        raise HTTPException(404)
    players_raw = get_players(tid)
    players = []
    for p in players_raw:
        p = dict(p)
        try:
            p["byes_list"] = _json.loads(p.get("requested_byes") or "[]")
        except Exception:
            p["byes_list"] = []
        players.append(p)
    current_round = tournament.get("current_round", 0) or 1
    return templates.TemplateResponse(request=request, name="tournament_detail.html", context={
        "tournament": tournament,
        "players": players,
        "current_round": current_round,
        "imported": imported,
        "current_user": user,
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

def _lookup_oob(full_name: str, rating: int, source: str = "", fide_id: str = "", expiry: str = "") -> str:
    safe_name = html.escape(full_name)
    src = f" <span class='text-muted'>({html.escape(source)})</span>" if source else ""
    fide_str = f" · FIDE ID: {html.escape(fide_id)}" if fide_id else ""
    exp_str = f" · Expires: {html.escape(expiry)}" if expiry else ""
    preview = f'<div id="uscf-preview"><span class="text-success small">✓ {safe_name} — Rating: {rating or "Unrated"}{fide_str}{exp_str}{src}</span></div>'
    name_oob = f'<input type="text" id="player-name" name="name" class="form-control" value="{safe_name}" required placeholder="Full name" hx-swap-oob="true">'
    rating_oob = f'<input type="number" id="player-rating" name="rating" class="form-control" value="{html.escape(str(rating))}" placeholder="Optional" hx-swap-oob="true">'
    fide_oob = f'<input type="text" id="player-fide-id" name="fide_id" class="form-control" value="{html.escape(fide_id)}" placeholder="Auto-filled" hx-swap-oob="true">'
    return preview + name_oob + rating_oob + fide_oob

@app.get("/api/uscf-lookup", response_class=HTMLResponse)
async def uscf_lookup(uscf_id: str = "", _user: dict = Depends(require_login)):
    uscf_id = uscf_id.strip()
    empty = '<div id="uscf-preview"></div>'
    if len(uscf_id) < 7:
        return HTMLResponse(empty)
    # 1. Try local DB first
    local = lookup_uscf_member(uscf_id)
    if local:
        name = _format_uscf_name(local["name"])
        return HTMLResponse(_lookup_oob(name, local["rating"], "local DB", local.get("fide_id") or "", local.get("expiry") or ""))
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
        fide_id = r.get("fide_id") or ""
        dn = html.escape(display)
        items += (
            f'<button type="button" class="list-group-item list-group-item-action py-1 small"'
            f' data-name="{dn}" data-id="{html.escape(r["uscf_id"])}" data-rating="{html.escape(str(rating))}"'
            f' data-fide="{html.escape(fide_id)}"'
            f' onclick="fillUscfPlayer(this)">'
            f'{dn} <span class="text-muted">{html.escape(r["uscf_id"])}</span>'
            f'{" — " + str(rating) if rating else ""}'
            f'{" · FIDE " + fide_id if fide_id else ""}'
            f'</button>'
        )
    return (
        '<div id="uscf-suggestions">'
        '<div class="list-group mt-1" style="max-height:220px;overflow-y:auto;position:absolute;z-index:100;width:100%">'
        f'{items}</div></div>'
    )

@app.get("/api/uscf-search", response_class=HTMLResponse)
async def uscf_search(name: str = "", _user: dict = Depends(require_login)):
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
                          rating: Optional[int] = Form(None), email: Optional[str] = Form(None),
                          fide_id: Optional[str] = Form(None), _user: dict = Depends(require_td)):
    add_player(tid, name, uscf_id, rating, email, fide_id or None)
    return RedirectResponse(f"/tournament/{tid}", status_code=303)

@app.post("/tournament/{tid}/import-players")
async def import_players_csv(tid: int, file: UploadFile = File(...), _user: dict = Depends(require_td)):
    content = await file.read()
    text = content.decode("utf-8-sig", errors="replace")
    added = 0
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        # Split on comma or tab; find USCF ID (7-8 digits) and/or name
        parts = [p.strip() for p in re.split(r'[,\t]', line) if p.strip()]
        uscf_id = None
        name = None
        rating = None
        for part in parts:
            if re.match(r'^\d{7,8}$', part):
                uscf_id = part
                break
        name_parts = [p for p in parts if not re.match(r'^\d{7,8}$', p)]
        if name_parts:
            name = name_parts[0]

        fide_id = None
        if uscf_id:
            local = lookup_uscf_member(uscf_id)
            if local:
                name = _format_uscf_name(local["name"])
                rating = local["rating"]
                fide_id = local.get("fide_id")
            else:
                try:
                    headers = {"User-Agent": "Mozilla/5.0 (compatible; MyChessRating/1.0)"}
                    async with httpx.AsyncClient(timeout=8, follow_redirects=True) as client:
                        r = await client.get(f"http://www.uschess.org/msa/thin3.php?{uscf_id}", headers=headers)
                    if r.status_code == 200 and "memname" in r.text:
                        data = _parse_uscf_thin3(r.text)
                        name = data["name"] or name
                        rating = data["rating"]
                except Exception:
                    pass
            if not name:
                continue
        elif name:
            results = search_uscf_members(name, limit=1)
            if results:
                top = results[0]
                uscf_id = top["uscf_id"]
                name = _format_uscf_name(top["name"])
                rating = top["rating"]
                fide_id = top.get("fide_id")
        else:
            continue

        add_player(tid, name, uscf_id, rating, fide_id=fide_id)
        added += 1

    return RedirectResponse(f"/tournament/{tid}?imported={added}", status_code=303)

@app.post("/player/{pid}/delete")
async def remove_player(pid: int, _user: dict = Depends(require_td)):
    tid = delete_player(pid)
    return RedirectResponse(f"/tournament/{tid}" if tid else "/", status_code=303)

# HTMX: Round table fragment
@app.get("/tournament/{tid}/round/{round_num}/table", response_class=HTMLResponse)
async def round_table_fragment(request: Request, tid: int, round_num: int,
                                user: dict = Depends(require_login)):
    tournament = get_tournament(tid)
    if not tournament:
        raise HTTPException(404)
    pairings = get_pairings_for_round(tid, round_num)
    standings = get_standings(tid)
    return templates.TemplateResponse(request=request, name="fragments/round_table.html", context={
        "tournament": tournament,
        "round_num": round_num,
        "pairings": pairings,
        "standings": standings,
        "current_user": user,
    })

# Submit normal result (HTMX)
@app.post("/result/submit", response_class=HTMLResponse)
async def submit_result_htmx(
    request: Request,
    tid: int = Form(...),
    round_num: int = Form(...),
    white_id: int = Form(...),
    black_id: int = Form(...),
    result: str = Form(...),
    _user: dict = Depends(require_td),
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
    opponent_id: Optional[int] = Form(None),
    _user: dict = Depends(require_td),
):
    if is_forfeit and opponent_id:
        result_str = "1F-0F" if bye_type == "full" else "0F-1F"
        record_result(tid, round_num, player_id, opponent_id, result_str)
    else:
        record_result(tid, round_num, white_id=player_id, is_bye=True, bye_type=bye_type)
    return await round_table_fragment(request, tid, round_num)

# Generate next round
@app.post("/tournament/{tid}/next-round", response_class=HTMLResponse)
async def generate_next_round(request: Request, tid: int, _user: dict = Depends(require_td)):
    tournament = get_tournament(tid)
    if not tournament:
        raise HTTPException(404)
    current = tournament.get("current_round", 0)
    next_r = current + 1

    # Build TRF with only the completed rounds (before advancing current_round)
    try:
        trf_text = build_trf(tid, rounds_to_include=current)
    except Exception as e:
        return HTMLResponse(f'<div class="alert alert-danger">TRF build error: {html.escape(str(e))}</div>')
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
            err = proc.stderr.strip() or proc.stdout.strip() or f"exit code {proc.returncode}"
            return HTMLResponse(f'<div class="alert alert-danger">Pairing error: {html.escape(err)}<pre>{html.escape(trf_text)}</pre></div>')

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
async def download_trf(tid: int, _user: dict = Depends(require_login)):
    trf_text = build_trf(tid)
    file_path = f"trf_{tid}.trf"
    Path(file_path).write_text(trf_text)
    return FileResponse(file_path, media_type="text/plain", filename=f"tournament_{tid}.trf")

@app.get("/tournament/{tid}/trf-debug")
async def trf_debug(tid: int, _user: dict = Depends(require_admin)):
    results = {}
    # bbpPairings version
    proc = subprocess.run([BBP_PATH, "--help"], capture_output=True, text=True, timeout=5)
    results["bbp_help"] = (proc.stdout + proc.stderr)[:500]

    def run_trf(trf_text):
        fd, path = tempfile.mkstemp(suffix=".trf")
        out = path + ".out"
        try:
            with os.fdopen(fd, "w") as f:
                f.write(trf_text)
            p = subprocess.run([BBP_PATH, "--dutch", path, "-p", out],
                               capture_output=True, text=True, timeout=10)
            return {"rc": p.returncode, "err": p.stderr.strip(), "out": p.stdout.strip()}
        finally:
            Path(path).unlink(missing_ok=True)
            Path(out).unlink(missing_ok=True)

    # Test A: 2 players with same format as real TRF
    trf_2p = build_trf(tid, rounds_to_include=0)
    lines = trf_2p.splitlines()
    trf_2p_short = "\n".join(lines[:6] + lines[5:7]) + "\n"  # header + first 2 players
    results["test_2players"] = run_trf(trf_2p_short)
    results["test_2players_trf"] = trf_2p_short

    # Test B: full real TRF
    results["test_full"] = run_trf(trf_2p)
    return results

# Standings
@app.get("/tournament/{tid}/standings", response_class=HTMLResponse)
async def view_standings(request: Request, tid: int, user: dict = Depends(require_login)):
    tournament = get_tournament(tid)
    if not tournament:
        raise HTTPException(404)
    standings = get_standings(tid)
    return templates.TemplateResponse(request=request, name="standings.html", context={
        "tournament": tournament,
        "standings": standings,
        "current_user": user,
    })

@app.get("/uscf-db", response_class=HTMLResponse)
async def uscf_db_page(request: Request, imported: Optional[int] = None,
                        user: dict = Depends(require_admin)):
    count = get_uscf_db_count()
    return templates.TemplateResponse(request=request, name="uscf_db.html", context={
        "count": count,
        "imported": imported,
        "current_user": user,
    })

@app.post("/uscf-db/upload")
async def uscf_db_upload(file: UploadFile = File(...), _user: dict = Depends(require_admin)):
    import asyncio
    content = await file.read()
    loop = asyncio.get_event_loop()
    count = await loop.run_in_executor(None, import_uscf_members, content)
    return {"imported": count}

@app.get("/api/uscf-col-debug")
async def uscf_col_debug(_user: dict = Depends(require_admin)):
    import json
    from database import DB_FILE
    debug_path = DB_FILE.replace(".db", "_col_debug.json")
    try:
        with open(debug_path) as f:
            return json.load(f)
    except FileNotFoundError:
        return {"error": "re-upload the allratings file to generate this"}

# ---------------------------------------------------------------------------
# Player Rating Lookup
# ---------------------------------------------------------------------------

@app.get("/player-lookup", response_class=HTMLResponse)
async def player_lookup_page(request: Request, user: dict = Depends(require_login)):
    return templates.TemplateResponse(request=request, name="player_lookup.html",
                                      context={"current_user": user})

@app.get("/api/player-lookup-search", response_class=HTMLResponse)
async def player_lookup_search(name: str = "", _user: dict = Depends(require_login)):
    q = name.strip()
    empty = '<div id="lookup-suggestions"></div>'
    if len(q) < 2:
        return HTMLResponse(empty)

    local = search_uscf_members(q)
    if not local:
        # Fall back to USCF thin2.php
        try:
            headers = {"User-Agent": "Mozilla/5.0 (compatible; MyChessRating/1.0)"}
            parts = q.split()
            data = {"memln": parts[-1], "memfn": " ".join(parts[:-1]), "mode": "Search"}
            async with httpx.AsyncClient(timeout=8, follow_redirects=True) as client:
                r = await client.post("http://www.uschess.org/msa/thin2.php", data=data, headers=headers)
            rows = re.findall(r'<td>(\d{5,8})</td>\s*<td>([^<]+)</td>\s*<td>([^<]+)</td>', r.text)
            for uid, raw_name, info in rows[:12]:
                rating_m = re.search(r'(\d{3,4})\*?(?:\s|$)', info)
                local.append({
                    "uscf_id": uid,
                    "name": raw_name.strip(),
                    "rating": int(rating_m.group(1)) if rating_m else 0,
                    "fide_id": None,
                })
        except Exception:
            pass

    if not local:
        return HTMLResponse('<div id="lookup-suggestions"><p class="text-muted small mt-1">No results found.</p></div>')

    items = ""
    for r in local:
        display = html.escape(_format_uscf_name(r["name"]))
        rating_str = f' — {r["rating"]}' if r.get("rating") else ""
        items += (
            f'<button type="button" class="list-group-item list-group-item-action py-1 small"'
            f' onclick="selectPlayer(\'{html.escape(display)}\', \'{r["uscf_id"]}\')">'
            f'{display} <span class="text-muted">{r["uscf_id"]}</span>{html.escape(rating_str)}'
            f'</button>'
        )
    return HTMLResponse(
        f'<div id="lookup-suggestions">'
        f'<div class="list-group mt-1" style="position:absolute;z-index:100;width:100%;max-height:240px;overflow-y:auto">'
        f'{items}</div></div>'
    )

@app.get("/api/player-details", response_class=HTMLResponse)
async def player_details(request: Request, uscf_id: str = "", _user: dict = Depends(require_login)):
    uscf_id = uscf_id.strip()
    if not uscf_id:
        return HTMLResponse("")

    # 1. Local DB
    name, rating, fide_id, expiry = "", 0, "", ""
    local = lookup_uscf_member(uscf_id)
    if local:
        name    = _format_uscf_name(local["name"])
        rating  = local.get("rating") or 0
        fide_id = local.get("fide_id") or ""
        expiry  = local.get("expiry") or ""
    else:
        try:
            headers = {"User-Agent": "Mozilla/5.0 (compatible; MyChessRating/1.0)"}
            async with httpx.AsyncClient(timeout=8, follow_redirects=True) as client:
                r = await client.get(f"http://www.uschess.org/msa/thin3.php?{uscf_id}", headers=headers)
            if r.status_code == 200 and "memname" in r.text:
                data = _parse_uscf_thin3(r.text)
                name   = data["name"]
                rating = data["rating"]
        except Exception:
            pass

    # 2. Live USCF rating
    live_rating = 0
    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (compatible; MyChessRating/1.0)",
            "Accept": "application/json",
            "Origin": "https://ratings.uschess.org",
        }
        async with httpx.AsyncClient(timeout=10, follow_redirects=True) as client:
            r = await client.get(
                f"https://ratings-api.uschess.org/api/v1/members/{uscf_id}/sections",
                headers=headers,
            )
        if r.status_code == 200:
            for section in r.json().get("items", []):
                for record in section.get("ratingRecords", []):
                    if record.get("ratingSource") == "R":
                        live_rating = record.get("postRating", 0)
                        break
                if live_rating:
                    break
    except Exception:
        pass

    # 3. FIDE rating
    fide_rating = 0
    if fide_id:
        try:
            headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124 Safari/537.36"}
            async with httpx.AsyncClient(timeout=10, follow_redirects=True) as client:
                r = await client.get(f"https://ratings.fide.com/profile/{fide_id}", headers=headers)
            if r.status_code == 200:
                m = re.search(r'class="profile-standart[^"]*"[^>]*>.*?<p>(\d+)</p>', r.text, re.DOTALL)
                if m:
                    fide_rating = int(m.group(1))
        except Exception:
            pass

    if not name:
        return HTMLResponse('<div class="alert alert-warning">Player not found.</div>')

    return templates.TemplateResponse(request=request, name="fragments/player_details.html", context={
        "name": name, "uscf_id": uscf_id, "rating": rating,
        "fide_id": fide_id, "expiry": expiry,
        "live_rating": live_rating, "fide_rating": fide_rating,
    })


# ---------------------------------------------------------------------------
# Rating impact calculator
# ---------------------------------------------------------------------------

def _elo_impact(my_rating: int, opp_rating: int, k: int) -> dict:
    expected = 1 / (1 + 10 ** ((opp_rating - my_rating) / 400))
    return {
        "win":  round(k * (1.0 - expected)),
        "draw": round(k * (0.5 - expected)),
        "loss": round(k * (0.0 - expected)),
        "expected_pct": round(expected * 100),
    }

def _uscf_k(rating: int) -> int:
    if rating < 2100: return 32
    if rating < 2400: return 24
    return 16

def _fide_k(rating: int) -> int:
    if rating < 1600: return 40
    if rating < 2400: return 20
    return 10


@app.get("/api/rating-impact", response_class=HTMLResponse)
async def rating_impact_api(
    request: Request,
    opp_uscf: Optional[int] = None,
    opp_fide: Optional[int] = None,
    opp_name: Optional[str] = None,
):
    user = get_current_user(request)
    profile = get_user_profile(user["id"]) if user else None
    my_uscf = profile.get("uscf_rating") if profile else None
    my_fide = profile.get("fide_rating") if profile else None

    uscf_impact = (
        _elo_impact(my_uscf, opp_uscf, _uscf_k(my_uscf))
        if my_uscf and opp_uscf else None
    )
    fide_impact = (
        _elo_impact(my_fide, opp_fide, _fide_k(my_fide))
        if my_fide and opp_fide else None
    )

    return templates.TemplateResponse(
        request=request,
        name="fragments/rating_impact.html",
        context={
            "opp_name": opp_name,
            "my_uscf": my_uscf,
            "my_fide": my_fide,
            "opp_uscf": opp_uscf,
            "opp_fide": opp_fide,
            "uscf_impact": uscf_impact,
            "fide_impact": fide_impact,
            "has_profile": bool(profile and (my_uscf or my_fide)),
            "logged_in": bool(user),
        },
    )


@app.get("/profile", response_class=HTMLResponse)
async def profile_page(request: Request, saved: Optional[str] = None, user: dict = Depends(require_login)):
    profile = get_user_profile(user["id"])
    return templates.TemplateResponse(request=request, name="profile.html", context={
        "profile": profile,
        "saved": saved == "1",
    })


@app.post("/profile")
async def update_profile(
    uscf_id: Optional[str] = Form(None),
    fide_id: Optional[str] = Form(None),
    uscf_rating: Optional[int] = Form(None),
    fide_rating: Optional[int] = Form(None),
    user: dict = Depends(require_login),
):
    update_user_profile(
        user["id"],
        uscf_id.strip() if uscf_id else None,
        fide_id.strip() if fide_id else None,
        uscf_rating,
        fide_rating,
    )
    return RedirectResponse("/profile?saved=1", status_code=303)


@app.post("/profile/populate", response_class=HTMLResponse)
async def profile_populate(
    uscf_id: Optional[str] = Form(None),
    fide_id: Optional[str] = Form(None),
    uscf_rating: Optional[int] = Form(None),
    fide_rating: Optional[int] = Form(None),
    user: dict = Depends(require_login),
):
    update_user_profile(
        user["id"],
        uscf_id.strip() if uscf_id else None,
        fide_id.strip() if fide_id else None,
        uscf_rating or None,
        fide_rating or None,
    )
    return HTMLResponse('<span class="text-success fw-semibold">&#10003; Profile updated</span>')


# ---------------------------------------------------------------------------
# FIDE Initial Rating Calculator
# ---------------------------------------------------------------------------

@app.get("/fide-calculator", response_class=HTMLResponse)
async def fide_calculator_page(request: Request, user: dict = Depends(require_login)):
    return templates.TemplateResponse(request=request, name="fide_calculator.html",
                                      context={"current_user": user})

@app.post("/fide-calculator/calculate")
async def fide_calculate(request: Request, _user: dict = Depends(require_login)):
    body = await request.json()
    result = calculate_rating(body.get("opponents", []), body.get("results", []))
    return result or {"error": "Need 5+ valid games against FIDE-rated opponents"}

@app.post("/fide-calculator/pdf")
async def fide_pdf(
    request: Request,
    name: str = Form(""),
    opponents: str = Form(""),
    results: str = Form(""),
    _user: dict = Depends(require_login),
):
    import json
    from fastapi.responses import StreamingResponse
    try:
        opps = json.loads(opponents)
        res  = json.loads(results)
    except Exception:
        raise HTTPException(400, "Invalid game data")
    data = calculate_rating(opps, res)
    if not data:
        raise HTTPException(400, "Need 5+ valid games to generate certificate")
    buf = fide_generate_pdf(data, name.strip())
    return StreamingResponse(
        buf, media_type="application/pdf",
        headers={"Content-Disposition": f"attachment; filename=FIDE_Rating_{data['rating']}.pdf"},
    )


# ---------------------------------------------------------------------------
# Tournament settings (TD only)
# ---------------------------------------------------------------------------

@app.post("/tournament/{tid}/settings")
async def tournament_settings(
    tid: int,
    entry_fee: float = Form(0),
    registration_open: str = Form(None),
    _user: dict = Depends(require_td),
):
    update_tournament_settings(tid, entry_fee, 1 if registration_open else 0)
    return RedirectResponse(f"/tournament/{tid}", status_code=303)


# ---------------------------------------------------------------------------
# Public entry list (no auth)
# ---------------------------------------------------------------------------

@app.get("/tournament/{tid}/entries", response_class=HTMLResponse)
async def entry_list_page(request: Request, tid: int):
    import json as _json
    tournament = get_tournament(tid)
    if not tournament:
        raise HTTPException(404)
    players_raw = get_players(tid)
    players = []
    for p in players_raw:
        p = dict(p)
        try:
            p["byes_list"] = _json.loads(p.get("requested_byes") or "[]")
        except Exception:
            p["byes_list"] = []
        players.append(p)
    return templates.TemplateResponse(request=request, name="entry_list.html",
                                      context={"tournament": tournament, "players": players})


# ---------------------------------------------------------------------------
# Public registration form (no auth)
# ---------------------------------------------------------------------------

@app.get("/tournament/{tid}/register", response_class=HTMLResponse)
async def tournament_register_page(request: Request, tid: int, cancelled: Optional[str] = None):
    tournament = get_tournament(tid)
    if not tournament:
        raise HTTPException(404)
    current_user = get_current_user(request)
    return templates.TemplateResponse(request=request, name="tournament_register.html", context={
        "tournament": tournament,
        "current_user": current_user,
        "error": "Payment was cancelled — please try again." if cancelled else None,
    })


@app.post("/tournament/{tid}/register")
async def tournament_register_submit(
    request: Request,
    tid: int,
    name: str = Form(...),
    uscf_id: Optional[str] = Form(None),
    rating: Optional[int] = Form(None),
    email: Optional[str] = Form(None),
    phone: Optional[str] = Form(None),
    fide_id: Optional[str] = Form(None),
    requested_byes: List[int] = Form(default=[]),
):
    import json as _json
    tournament = get_tournament(tid)
    if not tournament:
        raise HTTPException(404)
    entry_fee = float(tournament.get("entry_fee") or 0)
    local = lookup_uscf_member(uscf_id) if uscf_id else None
    expiry = local.get("expiry") if local else None
    player_id = register_player_public(
        tid, name.strip(), uscf_id or None, rating or 0,
        email or None, phone or None, fide_id or None, expiry,
        requested_byes=requested_byes,
        payment_status="pending" if entry_fee > 0 else "waived",
    )
    if entry_fee > 0 and STRIPE_SECRET_KEY:
        stripe.api_key = STRIPE_SECRET_KEY
        base_url = str(request.base_url).rstrip("/")
        session = stripe.checkout.Session.create(
            payment_method_types=["card"],
            line_items=[{"price_data": {"currency": "usd",
                "product_data": {"name": f"Entry fee: {tournament['name']}"},
                "unit_amount": int(entry_fee * 100)}, "quantity": 1}],
            mode="payment",
            success_url=f"{base_url}/tournament/{tid}/register/success?session_id={{CHECKOUT_SESSION_ID}}&player_id={player_id}",
            cancel_url=f"{base_url}/tournament/{tid}/register/cancel?player_id={player_id}",
            customer_email=email or None,
        )
        return RedirectResponse(session.url, status_code=303)
    elif entry_fee > 0:
        import logging
        logging.warning(f"[DEV] Stripe not configured — skipping payment for player {player_id}")
        update_player_payment(player_id, "waived")
    return RedirectResponse(f"/tournament/{tid}/register/success?player_id={player_id}", status_code=303)


# ---------------------------------------------------------------------------
# Stripe callbacks (no auth)
# ---------------------------------------------------------------------------

@app.get("/tournament/{tid}/register/success", response_class=HTMLResponse)
async def register_success(request: Request, tid: int, player_id: int, session_id: Optional[str] = None):
    import json as _json
    tournament = get_tournament(tid)
    player = get_player(player_id)
    if session_id and STRIPE_SECRET_KEY:
        stripe.api_key = STRIPE_SECRET_KEY
        try:
            s = stripe.checkout.Session.retrieve(session_id)
            if s.payment_status == "paid":
                update_player_payment(player_id, "paid", session_id)
                player = get_player(player_id)
        except Exception:
            pass
    if player:
        try:
            player["byes_list"] = _json.loads(player.get("requested_byes") or "[]")
        except Exception:
            player["byes_list"] = []
    return templates.TemplateResponse(request=request, name="tournament_register_success.html",
                                      context={"tournament": tournament, "player": player})


@app.get("/tournament/{tid}/register/cancel")
async def register_cancel(tid: int, player_id: Optional[int] = None):
    if player_id:
        delete_player(player_id)
    return RedirectResponse(f"/tournament/{tid}/register?cancelled=1", status_code=303)


# ---------------------------------------------------------------------------
# Withdraw / restore player (TD)
# ---------------------------------------------------------------------------

@app.post("/player/{pid}/withdraw")
async def withdraw_player(pid: int, _user: dict = Depends(require_td)):
    tid = set_player_status(pid, "withdrawn")
    return RedirectResponse(f"/tournament/{tid}", status_code=303)


@app.post("/player/{pid}/restore")
async def restore_player(pid: int, _user: dict = Depends(require_td)):
    tid = set_player_status(pid, "active")
    return RedirectResponse(f"/tournament/{tid}", status_code=303)


# ---------------------------------------------------------------------------
# Bye request management (TD)
# ---------------------------------------------------------------------------

@app.post("/player/{pid}/bye-request")
async def update_bye_request(
    pid: int,
    round_num: int = Form(...),
    action: str = Form(...),
    _user: dict = Depends(require_td),
):
    tid = update_player_bye_request(pid, round_num, action)
    return RedirectResponse(f"/tournament/{tid}", status_code=303)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
