import asyncio
import uuid
import time
from fastapi import FastAPI, Form, Request, HTTPException, UploadFile, File, Depends
from fastapi.responses import HTMLResponse, RedirectResponse, FileResponse, Response, JSONResponse
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
    delete_user, update_user_password, update_user_info,
    create_verification_token, check_and_consume_token, activate_user,
    create_password_reset_token, check_and_consume_reset_token,
    get_setting, set_setting,
    update_tournament_settings, get_player,
    register_player_public, set_player_status,
    update_player_payment, update_player_bye_request,
    get_user_profile, update_user_profile,
    save_user_tournament, list_user_tournaments, update_user_tournament, delete_user_tournament,
    update_user_contact,
    add_featured_tournament, list_featured_tournaments, update_featured_tournament, delete_featured_tournament,
)
from trf_builder import build_trf
from auth import get_current_user, require_login, require_td, require_admin
from notify import send_verification_email, send_verification_sms, send_password_reset_email
from fide import calculate_rating, generate_pdf as fide_generate_pdf

SECRET_KEY = os.environ.get("SECRET_KEY", "dev-secret-key-change-in-production")
STRIPE_SECRET_KEY = os.environ.get("STRIPE_SECRET_KEY", "")

# ---------------------------------------------------------------------------
# Simple in-memory TTL cache for external API responses
# ---------------------------------------------------------------------------
_cache: dict = {}

def _cache_get(key: str):
    entry = _cache.get(key)
    if entry and time.monotonic() < entry[1]:
        return entry[0]
    return None

def _cache_set(key: str, value, ttl: int = 300):
    _cache[key] = (value, time.monotonic() + ttl)

app = FastAPI(title="MyChessRating Pairings")
app.add_middleware(SessionMiddleware, secret_key=SECRET_KEY)
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")
templates.env.filters["from_json"] = __import__("json").loads

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
        "featured_tournaments": list_featured_tournaments(active_only=True),
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
# Forgot / Reset password
# ---------------------------------------------------------------------------

@app.get("/forgot-password", response_class=HTMLResponse)
async def forgot_password_page(request: Request):
    return templates.TemplateResponse(request=request, name="forgot_password.html", context={})

@app.post("/forgot-password", response_class=HTMLResponse)
async def forgot_password_submit(request: Request, email: str = Form(...)):
    email = email.strip().lower()
    user = get_user_by_email(email)
    # Always show the same message to prevent email enumeration
    msg = "If that email is registered, a reset link has been sent. Check your inbox."
    if user and user.get("status") == "active":
        try:
            token = create_password_reset_token(user["id"])
            base = str(request.base_url).rstrip("/")
            reset_url = f"{base}/reset-password?token={token}"
            await send_password_reset_email(email, reset_url)
        except Exception:
            pass
    return templates.TemplateResponse(request=request, name="forgot_password.html",
                                      context={"sent": True, "message": msg})

@app.get("/reset-password", response_class=HTMLResponse)
async def reset_password_page(request: Request, token: str = ""):
    if not token:
        return RedirectResponse("/forgot-password", status_code=303)
    return templates.TemplateResponse(request=request, name="reset_password.html",
                                      context={"token": token})

@app.post("/reset-password", response_class=HTMLResponse)
async def reset_password_submit(
    request: Request,
    token: str = Form(...),
    password: str = Form(...),
    confirm: str = Form(...),
):
    error = None
    if len(password) < 6:
        error = "Password must be at least 6 characters."
    elif password != confirm:
        error = "Passwords do not match."
    if error:
        return templates.TemplateResponse(request=request, name="reset_password.html",
                                          context={"token": token, "error": error})
    uid = check_and_consume_reset_token(token)
    if not uid:
        return templates.TemplateResponse(request=request, name="reset_password.html",
                                          context={"token": token,
                                                   "error": "This reset link has expired or already been used. Please request a new one."})
    update_user_password(uid, password)
    return templates.TemplateResponse(request=request, name="reset_password.html",
                                      context={"token": "", "success": True})


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

@app.post("/users/{uid}/edit")
async def edit_user_route(
    uid: int,
    username: str = Form(...),
    email: Optional[str] = Form(None),
    phone: Optional[str] = Form(None),
    role: str = Form("viewer"),
    status: str = Form("active"),
    current: dict = Depends(require_admin),
):
    update_user_info(uid, username, email, phone, role, status)
    return RedirectResponse("/users", status_code=303)


# ---------------------------------------------------------------------------
# Public JSON API (used by mobile app — no auth required)
# ---------------------------------------------------------------------------

@app.get("/api/public/player-search")
async def public_player_search(name: str = ""):
    q = name.strip()
    if len(q) < 2:
        return []
    cached = _cache_get(f"search_{q.lower()}")
    if cached is not None:
        return cached
    local, live = await asyncio.gather(
        asyncio.get_event_loop().run_in_executor(None, search_uscf_members, q),
        _uscf_live_search(q),
    )
    local_ids = {p["uscf_id"] for p in local}
    merged = local + [p for p in live if p["uscf_id"] not in local_ids]
    result = [
        {"uscf_id": p["uscf_id"], "name": _format_uscf_name(p["name"]), "rating": p.get("rating") or 0}
        for p in merged[:12]
    ]
    _cache_set(f"search_{q.lower()}", result, ttl=120)
    return result


@app.get("/api/public/player-details")
async def public_player_details(uscf_id: str = ""):
    uscf_id = uscf_id.strip()
    if not uscf_id:
        return {}

    cached = _cache_get(f"pub_details_{uscf_id}")
    if cached is not None:
        return cached

    name, rating, fide_id, expiry = "", 0, "", ""
    local = lookup_uscf_member(uscf_id)
    if local:
        name    = _format_uscf_name(local["name"])
        rating  = local.get("rating") or 0
        fide_id = local.get("fide_id") or ""
        expiry  = local.get("expiry") or ""

    api_headers = {"User-Agent": "Mozilla/5.0", "Accept": "application/json", "Origin": "https://ratings.uschess.org"}
    fide_headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}

    async def _fetch_sections():
        cached = _cache_get(f"uscf_sections_{uscf_id}")
        if cached is not None:
            return cached
        try:
            async with httpx.AsyncClient(timeout=6, follow_redirects=True) as client:
                r = await client.get(f"https://ratings-api.uschess.org/api/v1/members/{uscf_id}/sections", headers=api_headers)
            val = r.json() if r.status_code == 200 else {}
        except Exception:
            val = {}
        _cache_set(f"uscf_sections_{uscf_id}", val, ttl=240)
        return val

    async def _fetch_fide():
        if not fide_id:
            return 0
        cached = _cache_get(f"fide_{fide_id}")
        if cached is not None:
            return cached
        try:
            async with httpx.AsyncClient(timeout=7, follow_redirects=True) as client:
                r = await client.get(f"https://ratings.fide.com/profile/{fide_id}", headers=fide_headers)
            m = re.search(r'class="profile-standart[^"]*"[^>]*>.*?<p>(\d+)</p>', r.text, re.DOTALL) if r.status_code == 200 else None
            val = int(m.group(1)) if m else 0
        except Exception:
            val = 0
        _cache_set(f"fide_{fide_id}", val, ttl=600)
        return val

    sections_data, fide_rating = await asyncio.gather(_fetch_sections(), _fetch_fide())

    live_rating = 0
    for section in sections_data.get("items", []):
        for record in section.get("ratingRecords", []):
            if record.get("ratingSource") == "R":
                live_rating = record.get("postRating", 0)
                break
        if live_rating:
            break

    if not name:
        return {}

    result = {
        "name": name, "uscf_id": uscf_id, "uscf_rating": rating,
        "live_uscf_rating": live_rating, "fide_id": fide_id,
        "fide_rating": fide_rating, "expiry": expiry,
    }
    _cache_set(f"pub_details_{uscf_id}", result, ttl=240)
    return result


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

async def _uscf_live_search(q: str) -> list:
    """Search USCF thin2.php for players not yet in the local DB (e.g. new registrations)."""
    try:
        headers = {"User-Agent": "Mozilla/5.0 (compatible; MyChessRating/1.0)"}
        parts = q.split()
        data = {"memln": parts[-1], "memfn": " ".join(parts[:-1]) if len(parts) > 1 else "", "mode": "Search"}
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
                "fide_id": None,
            })
        return results
    except Exception:
        return []


def _parse_uscf_thin3(body: str) -> dict:
    """Parse name, ratings, and game counts from USCF thin3.php HTML response.

    Rating field format: '{rating}/{games} {date}' for provisional,
                         '{rating} {date}' or '{rating}* {date}' for established.
    """
    name_m = re.search(r"name=memname[^>]+value='([^']+)'", body)
    name = ""
    if name_m:
        raw = name_m.group(1).strip()
        if ", " in raw:
            parts = raw.split(", ", 1)
            name = f"{parts[1]} {parts[0]}".title()
        else:
            name = raw.title()

    def _extract_rating_full(field: str) -> dict:
        m = re.search(rf"name={field}[^>]+value='([^']+)'", body)
        if not m:
            return {"rating": 0, "games": None, "provisional": False}
        val = m.group(1).strip()
        # Provisional format: "1156/16 2005-04-01"
        prov_m = re.match(r"(\d+)/(\d+)", val)
        if prov_m:
            return {"rating": int(prov_m.group(1)), "games": int(prov_m.group(2)), "provisional": True}
        # Established format: "1500 2024-01-01" or "1500* 2024-01-01"
        est_m = re.match(r"(\d+)", val)
        if est_m:
            return {"rating": int(est_m.group(1)), "games": None, "provisional": False}
        return {"rating": 0, "games": None, "provisional": False}

    r1 = _extract_rating_full("rating1")
    r2 = _extract_rating_full("rating2")
    r3 = _extract_rating_full("rating3")

    return {
        "name":              name,
        "rating":            r1["rating"],
        "rating_games":      r1["games"],
        "rating_provisional": r1["provisional"],
        "quick":             r2["rating"],
        "quick_games":       r2["games"],
        "quick_provisional": r2["provisional"],
        "blitz":             r3["rating"],
        "blitz_games":       r3["games"],
        "blitz_provisional": r3["provisional"],
    }

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

@app.get("/api/uscf-player-status")
async def uscf_player_status(uscf_id: str = "", _user: dict = Depends(require_login)):
    """Return current rating, provisional status, and game count for a USCF ID.

    Uses ratings-api.uschess.org/api/v1/members which returns the current
    published monthly rating and isProvisional flag directly — much more
    accurate than thin3.php which can lag several months behind.
    """
    uscf_id = uscf_id.strip()
    if len(uscf_id) < 5:
        return {"error": "Invalid USCF ID"}
    try:
        api_headers = {
            "User-Agent": "Mozilla/5.0 (compatible; MyChessRating/1.0)",
            "Accept": "application/json",
            "Origin": "https://ratings.uschess.org",
        }
        async with httpx.AsyncClient(timeout=10, follow_redirects=True) as client:
            member_r, sections_r = await asyncio.gather(
                client.get(f"https://ratings-api.uschess.org/api/v1/members/{uscf_id}", headers=api_headers),
                client.get(f"https://ratings-api.uschess.org/api/v1/members/{uscf_id}/sections", headers=api_headers),
            )

        if member_r.status_code != 200:
            return {"error": "USCF ID not found"}
        m = member_r.json()

        name = f"{m.get('firstName', '')} {m.get('lastName', '')}".strip().title()
        if not name:
            return {"error": "USCF ID not found"}

        def _find_rating(system: str) -> dict:
            for entry in m.get("ratings", []):
                if entry.get("ratingSystem") == system:
                    return entry
            return {}

        reg   = _find_rating("R")
        quick = _find_rating("Q")
        blitz = _find_rating("B")

        # Live ratings from sections API
        live_rating = live_quick = live_blitz = 0
        if sections_r.status_code == 200:
            for section in sections_r.json().get("items", []):
                for record in section.get("ratingRecords", []):
                    src = record.get("ratingSource")
                    val = record.get("postRating", 0)
                    if src == "R" and not live_rating:   live_rating = val
                    elif src == "Q" and not live_quick:  live_quick  = val
                    elif src == "B" and not live_blitz:  live_blitz  = val

        return {
            "name":              name,
            "rating":            reg.get("rating") or 0,
            "live_rating":       live_rating or reg.get("rating") or 0,
            "provisional":       reg.get("isProvisional", False),
            "games":             reg.get("gamesPlayed"),
            "floor":             reg.get("floor"),
            "quick":             quick.get("rating") or 0,
            "live_quick":        live_quick or quick.get("rating") or 0,
            "quick_provisional": quick.get("isProvisional", False),
            "quick_games":       quick.get("gamesPlayed"),
            "blitz":             blitz.get("rating") or 0,
            "live_blitz":        live_blitz or blitz.get("rating") or 0,
            "blitz_provisional": blitz.get("isProvisional", False),
            "blitz_games":       blitz.get("gamesPlayed"),
            "fide_id":           m.get("fideId") or "",
        }
    except Exception:
        return {"error": "Lookup failed"}


@app.get("/api/player-fide-rating")
async def player_fide_rating(fide_id: str = "", _user: dict = Depends(require_login)):
    fide_id = fide_id.strip()
    if not fide_id:
        return {"fide_rating": 0, "fide_id": ""}
    try:
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124 Safari/537.36"}
        async with httpx.AsyncClient(timeout=10, follow_redirects=True) as client:
            r = await client.get(f"https://ratings.fide.com/profile/{fide_id}", headers=headers)
        if r.status_code == 200:
            m = re.search(r'class="profile-standart[^"]*"[^>]*>.*?<p>(\d+)</p>', r.text, re.DOTALL)
            if m:
                return {"fide_rating": int(m.group(1)), "fide_id": fide_id}
    except Exception:
        pass
    return {"fide_rating": 0, "fide_id": fide_id}


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
# Admin — Featured Tournaments
# ---------------------------------------------------------------------------

@app.get("/admin/tournaments", response_class=HTMLResponse)
async def admin_tournaments_page(request: Request, user: dict = Depends(require_admin)):
    return templates.TemplateResponse(request=request, name="admin_tournaments.html",
                                      context={"tournaments": list_featured_tournaments()})

@app.post("/admin/tournaments")
async def admin_add_tournament(
    request: Request,
    name: str = Form(...),
    subtitle: str = Form(""),
    description: str = Form(""),
    info_url: str = Form(""),
    pairings_url: str = Form(""),
    source: str = Form("manual"),
    source_url: str = Form(""),
    display_order: int = Form(0),
    _user: dict = Depends(require_admin),
):
    add_featured_tournament(
        name=name.strip(),
        subtitle=subtitle.strip() or None,
        description=description.strip() or None,
        info_url=info_url.strip() or None,
        pairings_url=pairings_url.strip() or None,
        source=source or "manual",
        source_url=source_url.strip() or None,
        display_order=display_order,
    )
    return RedirectResponse("/admin/tournaments", status_code=303)

@app.post("/admin/tournaments/{fid}/toggle")
async def admin_toggle_tournament(fid: int, _user: dict = Depends(require_admin)):
    conn = __import__("sqlite3").connect(__import__("database").DB_FILE)
    row = conn.execute("SELECT active FROM featured_tournaments WHERE id=?", (fid,)).fetchone()
    conn.close()
    if row is not None:
        update_featured_tournament(fid, active=0 if row[0] else 1)
    return RedirectResponse("/admin/tournaments", status_code=303)

@app.post("/admin/tournaments/{fid}/delete")
async def admin_delete_tournament(fid: int, _user: dict = Depends(require_admin)):
    delete_featured_tournament(fid)
    return RedirectResponse("/admin/tournaments", status_code=303)

@app.post("/admin/tournaments/{fid}/edit")
async def admin_edit_tournament(
    fid: int,
    name: str = Form(...),
    subtitle: str = Form(""),
    description: str = Form(""),
    info_url: str = Form(""),
    pairings_url: str = Form(""),
    display_order: int = Form(0),
    _user: dict = Depends(require_admin),
):
    update_featured_tournament(
        fid,
        name=name.strip(),
        subtitle=subtitle.strip() or None,
        description=description.strip() or None,
        info_url=info_url.strip() or None,
        pairings_url=pairings_url.strip() or None,
        display_order=display_order,
    )
    return RedirectResponse("/admin/tournaments", status_code=303)

@app.get("/admin/fetch-url")
async def admin_fetch_url(url: str, _user: dict = Depends(require_admin)):
    """Fetch a URL and return the page title for auto-fill when importing from external sources."""
    if not url.startswith(("http://", "https://")):
        return JSONResponse({"error": "Invalid URL"}, status_code=400)
    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=10) as client:
            resp = await client.get(url, headers={"User-Agent": "Mozilla/5.0 (compatible; MyChessRating/1.0)"})
        title_match = re.search(r"<title[^>]*>(.*?)</title>", resp.text, re.IGNORECASE | re.DOTALL)
        title = re.sub(r"\s+", " ", title_match.group(1)).strip() if title_match else ""
        # strip common suffixes like " - CaissaLive" or " | US Chess"
        title = re.sub(r"\s*[-|–]\s*(CaissaLive|US Chess|uschess\.org).*$", "", title, flags=re.IGNORECASE).strip()
        return {"title": title, "url": str(resp.url)}
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=502)

# ---------------------------------------------------------------------------
# USCF Tournament History
# ---------------------------------------------------------------------------

def _parse_uscf_crosstable(html: str, uscf_id: str) -> list:
    """Extract a player's game results from a USCF cross-table HTML page."""
    from html.parser import HTMLParser

    class CTParser(HTMLParser):
        def __init__(self):
            super().__init__()
            self._rows = []
            self._cells = []
            self._cell_text = None
            self._cell_links = []

        def handle_starttag(self, tag, attrs):
            attrs_d = dict(attrs)
            if tag == 'tr':
                self._cells = []
            elif tag in ('td', 'th'):
                self._cell_text = ''
                self._cell_links = []
            elif tag == 'a' and self._cell_text is not None:
                href = attrs_d.get('href', '')
                if href:
                    self._cell_links.append(href)
            elif tag == 'br' and self._cell_text is not None:
                self._cell_text += ' '

        def handle_endtag(self, tag):
            if tag in ('td', 'th') and self._cell_text is not None:
                self._cells.append({'text': ' '.join(self._cell_text.split()), 'links': self._cell_links[:]})
                self._cell_text = None
                self._cell_links = []
            elif tag == 'tr' and self._cells:
                self._rows.append(self._cells[:])
                self._cells = []

        def handle_data(self, data):
            if self._cell_text is not None:
                self._cell_text += data

    parser = CTParser()
    parser.feed(html)

    players = {}   # pair_num -> {name, uscf_id, pre_rating}
    target_row = None

    for row in parser._rows:
        if len(row) < 4:
            continue
        pair_cell = row[0]
        # Pair number must be numeric
        pair_m = re.match(r'^(\d+)$', pair_cell['text'])
        if not pair_m:
            continue
        pair_num = int(pair_m.group(1))

        name_cell = row[1]
        cell_text = name_cell['text']

        # USCF ID from MbrDtlMain link or XtblPlr link
        player_uscf = None
        for href in name_cell['links']:
            m = re.search(r'MbrDtlMain\.php\?(\d+)', href)
            if m:
                player_uscf = m.group(1)
                break
        if not player_uscf:
            for href in pair_cell['links']:
                m = re.search(r'XtblPlr\.php\?[^-]+-\d+-(\d+)', href)
                if m:
                    player_uscf = m.group(1)
                    break
        if not player_uscf:
            m = re.search(r'\b(\d{7,8})\b', cell_text)
            if m:
                player_uscf = m.group(1)

        # Name: text before state code, USCF ID, or rating
        player_name = re.split(r'\s+[A-Z]{2}\s+\||\s+\d{7,8}|\s+R:', cell_text)[0].strip().title()

        # Pre-rating: "R: 1894 ->1898"
        pre_rating = None
        rm = re.search(r'R:\s*P?(\d{3,4})\s*->', cell_text)
        if rm:
            pre_rating = int(rm.group(1))

        players[pair_num] = {'name': player_name, 'uscf_id': player_uscf, 'pre_rating': pre_rating}
        if player_uscf == uscf_id:
            target_row = row

    if not target_row:
        return []

    games = []
    result_map = {'W': '1', 'D': '0.5', 'L': '0'}
    for cell in target_row[3:]:   # skip pair, name, score columns
        txt = cell['text'].strip().upper()
        m = re.match(r'^([WDLHFUBXE])\s*(\d+)', txt)
        if not m:
            continue
        code, opp_pair = m.group(1), int(m.group(2))
        result = result_map.get(code)
        if result is None:
            continue
        if opp_pair in players:
            opp = players[opp_pair]
            games.append({
                'name': opp['name'],
                'rating': opp['pre_rating'],
                'uscfId': opp['uscf_id'],
                'result': result,
                'provisional': None,
                'games': None,
            })
    return games


@app.get("/api/uscf-tournament-history")
async def api_uscf_tournament_history(uscf_id: str, _user: dict = Depends(require_login)):
    uscf_id = uscf_id.strip()
    if not re.match(r'^\d{5,8}$', uscf_id):
        raise HTTPException(400, "Invalid USCF ID")

    cache_key = f"uscf_hist_{uscf_id}"
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached

    api_headers = {
        "User-Agent": "Mozilla/5.0 (compatible; MyChessRating/1.0)",
        "Accept": "application/json",
        "Origin": "https://ratings.uschess.org",
    }

    all_sections = []
    offset = 0
    async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
        while True:
            try:
                r = await client.get(
                    f"https://ratings-api.uschess.org/api/v1/members/{uscf_id}/sections?pageSize=100&offset={offset}",
                    headers=api_headers,
                )
                if r.status_code != 200:
                    break
                data = r.json()
                items = data.get("items", [])
                all_sections.extend(items)
                if not items or not data.get("hasNextPage"):
                    break
                offset += 100
            except Exception:
                break

    if not all_sections:
        result: list = []
        _cache_set(cache_key, result, ttl=300)
        return result

    # Group by event ID; for each event prefer R > Q > B
    from collections import defaultdict
    events: dict = defaultdict(list)
    for s in all_sections:
        eid = (s.get("event") or {}).get("id")
        if eid:
            events[eid].append(s)

    sys_priority = {"R": 0, "Q": 1, "B": 2}
    tournaments = []
    for eid, sections in events.items():
        sections.sort(key=lambda s: sys_priority.get(s.get("ratingSystem", "R"), 9))
        primary = sections[0]
        rec = primary.get("ratingRecords") or []
        ev = primary.get("event") or {}
        pre  = rec[0].get("preRating")  if rec else None
        post = rec[0].get("postRating") if rec else None
        tournaments.append({
            "event_id":     eid,
            "section_num":  primary.get("sectionNumber"),
            "name":         ev.get("name", "Unknown Tournament"),
            "date":         ev.get("endDate") or ev.get("startDate"),
            "state":        ev.get("stateCode"),
            "rating_system": primary.get("ratingSystem", "R"),
            "pre_rating":   pre,
            "post_rating":  post,
        })

    tournaments.sort(key=lambda t: t["date"] or "", reverse=True)
    _cache_set(cache_key, tournaments, ttl=300)
    return tournaments


@app.get("/api/uscf-tournament-games")
async def api_uscf_tournament_games(
    event_id: str, section_num: int, uscf_id: str, _user: dict = Depends(require_login)
):
    uscf_id = uscf_id.strip()
    if not re.match(r'^\d{5,8}$', uscf_id) or not re.match(r'^\w+$', event_id):
        raise HTTPException(400, "Invalid parameters")

    cache_key = f"uscf_games_{event_id}_{section_num}_{uscf_id}"
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached

    url = f"https://www.uschess.org/msa/XtblMain.php?{event_id}.{section_num}-{uscf_id}"
    try:
        async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
            resp = await client.get(url, headers={"User-Agent": "Mozilla/5.0 (compatible; MyChessRating/1.0)"})
        games = _parse_uscf_crosstable(resp.text, uscf_id)
    except Exception:
        games = []

    result = {"games": games}
    _cache_set(cache_key, result, ttl=600)
    return result

# ---------------------------------------------------------------------------
# Player Rating Lookup
# ---------------------------------------------------------------------------

@app.get("/player-lookup", response_class=HTMLResponse)
async def player_lookup_page(request: Request, user: dict = Depends(require_login)):
    return templates.TemplateResponse(request=request, name="player_lookup.html",
                                      context={"current_user": user,
                                               "featured_tournaments": list_featured_tournaments(active_only=True)})

@app.get("/api/player-lookup-search", response_class=HTMLResponse)
async def player_lookup_search(name: str = "", _user: dict = Depends(require_login)):
    q = name.strip()
    empty = '<div id="lookup-suggestions"></div>'
    if len(q) < 2:
        return HTMLResponse(empty)

    local, live = await asyncio.gather(
        asyncio.get_event_loop().run_in_executor(None, search_uscf_members, q),
        _uscf_live_search(q),
    )
    local_ids = {p["uscf_id"] for p in local}
    local = local + [p for p in live if p["uscf_id"] not in local_ids]

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

    # 1. Local DB for name/fide_id/expiry; member API for current monthly rating
    name, rating, fide_id, expiry = "", 0, "", ""
    local = lookup_uscf_member(uscf_id)
    if local:
        name    = _format_uscf_name(local["name"])
        fide_id = local.get("fide_id") or ""
        expiry  = local.get("expiry") or ""

    # 2. Member API + sections API + FIDE — all in parallel, with caching
    api_headers = {
        "User-Agent": "Mozilla/5.0 (compatible; MyChessRating/1.0)",
        "Accept": "application/json",
        "Origin": "https://ratings.uschess.org",
    }

    async def _fetch_member():
        cached = _cache_get(f"uscf_member_{uscf_id}")
        if cached is not None:
            return cached
        try:
            async with httpx.AsyncClient(timeout=6, follow_redirects=True) as client:
                r = await client.get(f"https://ratings-api.uschess.org/api/v1/members/{uscf_id}", headers=api_headers)
            val = r.json() if r.status_code == 200 else {}
        except Exception:
            val = {}
        _cache_set(f"uscf_member_{uscf_id}", val, ttl=300)
        return val

    async def _fetch_sections():
        cached = _cache_get(f"uscf_sections_{uscf_id}")
        if cached is not None:
            return cached
        try:
            async with httpx.AsyncClient(timeout=6, follow_redirects=True) as client:
                r = await client.get(f"https://ratings-api.uschess.org/api/v1/members/{uscf_id}/sections", headers=api_headers)
            val = r.json() if r.status_code == 200 else {}
        except Exception:
            val = {}
        _cache_set(f"uscf_sections_{uscf_id}", val, ttl=240)
        return val

    async def _fetch_fide():
        if not fide_id:
            return 0
        cached = _cache_get(f"fide_{fide_id}")
        if cached is not None:
            return cached
        try:
            fide_headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
            async with httpx.AsyncClient(timeout=7, follow_redirects=True) as client:
                r = await client.get(f"https://ratings.fide.com/profile/{fide_id}", headers=fide_headers)
            m = re.search(r'class="profile-standart[^"]*"[^>]*>.*?<p>(\d+)</p>', r.text, re.DOTALL) if r.status_code == 200 else None
            val = int(m.group(1)) if m else 0
        except Exception:
            val = 0
        _cache_set(f"fide_{fide_id}", val, ttl=600)
        return val

    member_data, sections_data, fide_rating = await asyncio.gather(
        _fetch_member(), _fetch_sections(), _fetch_fide()
    )

    live_rating = 0
    if member_data:
        if not name:
            name = f"{member_data.get('firstName', '')} {member_data.get('lastName', '')}".strip().title()
        for entry in member_data.get("ratings", []):
            if entry.get("ratingSystem") == "R" and entry.get("rating"):
                rating = entry["rating"]
                break
    for section in sections_data.get("items", []):
        for record in section.get("ratingRecords", []):
            if record.get("ratingSource") == "R":
                live_rating = record.get("postRating", 0)
                break
        if live_rating:
            break

    if not name:
        return HTMLResponse('<div class="alert alert-warning">Player not found.</div>')

    return templates.TemplateResponse(request=request, name="fragments/player_details.html", context={
        "name": name, "uscf_id": uscf_id, "rating": rating,
        "fide_id": fide_id, "expiry": expiry,
        "live_rating": live_rating, "fide_rating": fide_rating,
    })


# ---------------------------------------------------------------------------
# Quick / Blitz on-demand loader
# ---------------------------------------------------------------------------

@app.get("/api/player-quick-blitz", response_class=HTMLResponse)
async def player_quick_blitz(
    request: Request,
    uscf_id: str = "",
    fide_id: str = "",
    _user: dict = Depends(require_login),
):
    uscf_id = uscf_id.strip()
    fide_id = fide_id.strip()

    # USCF quick/blitz + FIDE rapid/blitz — reuse cached API data, fetch in parallel
    monthly_quick = monthly_blitz = live_quick = live_blitz = 0
    fide_rapid = fide_blitz = 0

    api_headers = {
        "User-Agent": "Mozilla/5.0 (compatible; MyChessRating/1.0)",
        "Accept": "application/json",
        "Origin": "https://ratings.uschess.org",
    }

    async def _qb_member():
        if not uscf_id:
            return {}
        cached = _cache_get(f"uscf_member_{uscf_id}")
        if cached is not None:
            return cached
        try:
            async with httpx.AsyncClient(timeout=6, follow_redirects=True) as client:
                r = await client.get(f"https://ratings-api.uschess.org/api/v1/members/{uscf_id}", headers=api_headers)
            val = r.json() if r.status_code == 200 else {}
        except Exception:
            val = {}
        _cache_set(f"uscf_member_{uscf_id}", val, ttl=300)
        return val

    async def _qb_sections():
        if not uscf_id:
            return {}
        cached = _cache_get(f"uscf_sections_{uscf_id}")
        if cached is not None:
            return cached
        try:
            async with httpx.AsyncClient(timeout=6, follow_redirects=True) as client:
                r = await client.get(f"https://ratings-api.uschess.org/api/v1/members/{uscf_id}/sections", headers=api_headers)
            val = r.json() if r.status_code == 200 else {}
        except Exception:
            val = {}
        _cache_set(f"uscf_sections_{uscf_id}", val, ttl=240)
        return val

    async def _qb_fide():
        if not fide_id:
            return None
        cached = _cache_get(f"fide_page_{fide_id}")
        if cached is not None:
            return cached
        try:
            fide_headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
            async with httpx.AsyncClient(timeout=7, follow_redirects=True) as client:
                r = await client.get(f"https://ratings.fide.com/profile/{fide_id}", headers=fide_headers)
            val = r.text if r.status_code == 200 else ""
        except Exception:
            val = ""
        _cache_set(f"fide_page_{fide_id}", val, ttl=600)
        return val

    member_data, sections_data, fide_page = await asyncio.gather(
        _qb_member(), _qb_sections(), _qb_fide()
    )

    for entry in member_data.get("ratings", []):
        rs, val = entry.get("ratingSystem"), entry.get("rating", 0)
        if rs == "Q" and val:
            monthly_quick = val
        elif rs == "B" and val:
            monthly_blitz = val
    for section in sections_data.get("items", []):
        for record in section.get("ratingRecords", []):
            src, val = record.get("ratingSource"), record.get("postRating", 0)
            if src == "Q" and not live_quick:
                live_quick = val
            elif src == "B" and not live_blitz:
                live_blitz = val
    if not live_quick:
        live_quick = monthly_quick
    if not live_blitz:
        live_blitz = monthly_blitz

    if fide_page:
        ms = re.findall(r'class="profile-standart[^"]*"[^>]*>.*?<p>(\d+)</p>', fide_page, re.DOTALL)
        if len(ms) > 1:
            fide_rapid = int(ms[1])
        if len(ms) > 2:
            fide_blitz = int(ms[2])

    return templates.TemplateResponse(request=request, name="fragments/player_quick_blitz.html", context={
        "uscf_id": uscf_id, "fide_id": fide_id,
        "monthly_quick": monthly_quick, "monthly_blitz": monthly_blitz,
        "live_quick": live_quick, "live_blitz": live_blitz,
        "fide_rapid": fide_rapid, "fide_blitz": fide_blitz,
    })


# ---------------------------------------------------------------------------
# Rating impact calculator
# ---------------------------------------------------------------------------

def _elo_impact(my_rating: int, opp_rating: int, k: int) -> dict:
    expected = 1 / (1 + 10 ** ((opp_rating - my_rating) / 400))
    return {
        "win":  round(k * (1.0 - expected), 1),
        "draw": round(k * (0.5 - expected), 1),
        "loss": round(k * (0.0 - expected), 1),
        "expected_pct": round(expected * 100, 1),
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
    opp_uscf_quick: Optional[int] = None,
    opp_uscf_blitz: Optional[int] = None,
    opp_fide: Optional[int] = None,
    opp_fide_rapid: Optional[int] = None,
    opp_fide_blitz: Optional[int] = None,
    opp_name: Optional[str] = None,
):
    user = get_current_user(request)
    profile = get_user_profile(user["id"]) if user else None
    my_uscf       = profile.get("uscf_rating")       if profile else None
    my_uscf_quick = profile.get("uscf_quick_rating") if profile else None
    my_uscf_blitz = profile.get("uscf_blitz_rating") if profile else None
    my_fide       = profile.get("fide_rating")       if profile else None
    my_fide_rapid = profile.get("fide_rapid_rating") if profile else None
    my_fide_blitz = profile.get("fide_blitz_rating") if profile else None

    uscf_impact       = _elo_impact(my_uscf,       opp_uscf,       _uscf_k(my_uscf))       if my_uscf and opp_uscf             else None
    uscf_quick_impact = _elo_impact(my_uscf_quick, opp_uscf_quick, _uscf_k(my_uscf_quick)) if my_uscf_quick and opp_uscf_quick else None
    uscf_blitz_impact = _elo_impact(my_uscf_blitz, opp_uscf_blitz, _uscf_k(my_uscf_blitz)) if my_uscf_blitz and opp_uscf_blitz else None
    fide_impact       = _elo_impact(my_fide,       opp_fide,       _fide_k(my_fide))       if my_fide and opp_fide             else None
    fide_rapid_impact = _elo_impact(my_fide_rapid, opp_fide_rapid, _fide_k(my_fide_rapid)) if my_fide_rapid and opp_fide_rapid else None
    fide_blitz_impact = _elo_impact(my_fide_blitz, opp_fide_blitz, _fide_k(my_fide_blitz)) if my_fide_blitz and opp_fide_blitz else None

    has_any = bool(profile and any([my_uscf, my_uscf_quick, my_uscf_blitz, my_fide, my_fide_rapid, my_fide_blitz]))
    my_name = (profile.get("player_name") or profile.get("username") or "You") if profile else "You"

    return templates.TemplateResponse(
        request=request,
        name="fragments/rating_impact.html",
        context={
            "my_name": my_name, "opp_name": opp_name,
            "my_uscf": my_uscf, "my_uscf_quick": my_uscf_quick, "my_uscf_blitz": my_uscf_blitz,
            "my_fide": my_fide, "my_fide_rapid": my_fide_rapid, "my_fide_blitz": my_fide_blitz,
            "opp_uscf": opp_uscf, "opp_uscf_quick": opp_uscf_quick, "opp_uscf_blitz": opp_uscf_blitz,
            "opp_fide": opp_fide, "opp_fide_rapid": opp_fide_rapid, "opp_fide_blitz": opp_fide_blitz,
            "uscf_impact": uscf_impact, "uscf_quick_impact": uscf_quick_impact, "uscf_blitz_impact": uscf_blitz_impact,
            "fide_impact": fide_impact, "fide_rapid_impact": fide_rapid_impact, "fide_blitz_impact": fide_blitz_impact,
            "has_profile": has_any,
            "logged_in": bool(user),
        },
    )


@app.get("/profile", response_class=HTMLResponse)
async def profile_page(request: Request, saved: Optional[str] = None, user: dict = Depends(require_login)):
    profile = get_user_profile(user["id"])
    uscf_refreshed = False
    uscf_id = (profile.get("uscf_id") or "").strip()
    if uscf_id:
        try:
            api_headers = {
                "User-Agent": "Mozilla/5.0 (compatible; MyChessRating/1.0)",
                "Accept": "application/json",
                "Origin": "https://ratings.uschess.org",
            }
            async with httpx.AsyncClient(timeout=8, follow_redirects=True) as client:
                member_r, sections_r = await asyncio.gather(
                    client.get(f"https://ratings-api.uschess.org/api/v1/members/{uscf_id}", headers=api_headers),
                    client.get(f"https://ratings-api.uschess.org/api/v1/members/{uscf_id}/sections", headers=api_headers),
                )
            live_regular = monthly_quick = monthly_blitz = live_quick = live_blitz = 0
            if member_r.status_code == 200:
                for entry in member_r.json().get("ratings", []):
                    rs = entry.get("ratingSystem")
                    val = entry.get("rating", 0)
                    if rs == "Q" and val:
                        monthly_quick = val
                    elif rs == "B" and val:
                        monthly_blitz = val
            if sections_r.status_code == 200:
                for section in sections_r.json().get("items", []):
                    for record in section.get("ratingRecords", []):
                        src = record.get("ratingSource")
                        val = record.get("postRating", 0)
                        if src == "R" and not live_regular:
                            live_regular = val
                        elif src == "Q" and not live_quick:
                            live_quick = val
                        elif src == "B" and not live_blitz:
                            live_blitz = val
            new_uscf = live_regular or profile.get("uscf_rating")
            new_quick = (live_quick or monthly_quick) or profile.get("uscf_quick_rating")
            new_blitz = (live_blitz or monthly_blitz) or profile.get("uscf_blitz_rating")
            if any([new_uscf, new_quick, new_blitz]):
                update_user_profile(
                    user["id"],
                    profile.get("uscf_id"), profile.get("fide_id"),
                    new_uscf, profile.get("fide_rating"),
                    new_quick, new_blitz,
                    profile.get("fide_rapid_rating"), profile.get("fide_blitz_rating"),
                    profile.get("player_name"),
                )
                profile = get_user_profile(user["id"])
                uscf_refreshed = True
        except Exception:
            pass
    return templates.TemplateResponse(request=request, name="profile.html", context={
        "profile": profile,
        "saved": saved == "1",
        "uscf_refreshed": uscf_refreshed,
    })


@app.post("/profile/contact")
async def update_contact(
    email: Optional[str] = Form(None),
    phone: Optional[str] = Form(None),
    user: dict = Depends(require_login),
):
    update_user_contact(user["id"],
                        email.strip() if email else None,
                        phone.strip() if phone else None)
    return RedirectResponse("/profile?saved=1", status_code=303)


@app.post("/profile")
async def update_profile(
    player_name: Optional[str] = Form(None),
    uscf_id: Optional[str] = Form(None),
    fide_id: Optional[str] = Form(None),
    uscf_rating: Optional[int] = Form(None),
    fide_rating: Optional[int] = Form(None),
    uscf_quick_rating: Optional[int] = Form(None),
    uscf_blitz_rating: Optional[int] = Form(None),
    fide_rapid_rating: Optional[int] = Form(None),
    fide_blitz_rating: Optional[int] = Form(None),
    user: dict = Depends(require_login),
):
    update_user_profile(
        user["id"],
        uscf_id.strip() if uscf_id else None,
        fide_id.strip() if fide_id else None,
        uscf_rating, fide_rating,
        uscf_quick_rating, uscf_blitz_rating, fide_rapid_rating, fide_blitz_rating,
        player_name.strip() if player_name else None,
    )
    return RedirectResponse("/profile?saved=1", status_code=303)


@app.post("/profile/populate", response_class=HTMLResponse)
async def profile_populate(
    player_name: Optional[str] = Form(None),
    uscf_id: Optional[str] = Form(None),
    fide_id: Optional[str] = Form(None),
    uscf_rating: Optional[str] = Form(None),
    fide_rating: Optional[str] = Form(None),
    uscf_quick_rating: Optional[str] = Form(None),
    uscf_blitz_rating: Optional[str] = Form(None),
    fide_rapid_rating: Optional[str] = Form(None),
    fide_blitz_rating: Optional[str] = Form(None),
    user: dict = Depends(require_login),
):
    def _int(v):
        try: return int(v) if v and v.strip() else None
        except (ValueError, TypeError): return None
    update_user_profile(
        user["id"],
        uscf_id.strip() if uscf_id else None,
        fide_id.strip() if fide_id else None,
        _int(uscf_rating), _int(fide_rating),
        _int(uscf_quick_rating), _int(uscf_blitz_rating),
        _int(fide_rapid_rating), _int(fide_blitz_rating),
        player_name.strip() if player_name else None,
    )
    return HTMLResponse('<span class="text-success fw-semibold">&#10003; Profile updated</span>')


# ---------------------------------------------------------------------------
# USCF Tournament Rating Calculator
# ---------------------------------------------------------------------------

@app.get("/uscf-calculator", response_class=HTMLResponse)
async def uscf_calculator_page(request: Request, user: dict = Depends(require_login)):
    profile = get_user_profile(user["id"])
    saved = list_user_tournaments(user["id"])
    return templates.TemplateResponse(request=request, name="uscf_calculator.html",
                                      context={"profile": profile, "saved_tournaments": saved,
                                               "featured_tournaments": list_featured_tournaments(active_only=True)})

@app.post("/api/uscf-tournaments")
async def api_save_tournament(request: Request, user: dict = Depends(require_login)):
    body = await request.json()
    name = (body.get("name") or "").strip()
    if not name:
        return JSONResponse({"error": "Tournament name is required"}, status_code=400)
    import json
    new_id = save_user_tournament(
        user_id=user["id"],
        name=name,
        start_rating=body.get("start_rating"),
        end_rating=body.get("end_rating"),
        games_json=json.dumps(body.get("games", [])),
        tournament_type=body.get("tournament_type") or "standard",
    )
    return {"id": new_id, "name": name}

@app.patch("/api/uscf-tournaments/{tid}")
async def api_update_tournament(tid: int, request: Request, user: dict = Depends(require_login)):
    body = await request.json()
    name = (body.get("name") or "").strip()
    if not name:
        return JSONResponse({"error": "Tournament name is required"}, status_code=400)
    import json
    ok = update_user_tournament(
        tournament_id=tid,
        user_id=user["id"],
        name=name,
        start_rating=body.get("start_rating"),
        end_rating=body.get("end_rating"),
        games_json=json.dumps(body.get("games", [])),
        tournament_type=body.get("tournament_type") or "standard",
    )
    if not ok:
        return JSONResponse({"error": "Not found"}, status_code=404)
    return {"updated": tid}

@app.delete("/api/uscf-tournaments/{tid}")
async def api_delete_tournament(tid: int, user: dict = Depends(require_login)):
    ok = delete_user_tournament(tid, user["id"])
    if not ok:
        return JSONResponse({"error": "Not found"}, status_code=404)
    return {"deleted": tid}


@app.post("/api/pgn/upload")
async def upload_pgn_file(file: UploadFile = File(...), user: dict = Depends(require_login)):
    import boto3
    from botocore.exceptions import BotoCoreError, ClientError

    bucket  = os.environ.get("AWS_S3_BUCKET", "occ-webhook-photos-2026")
    region  = os.environ.get("AWS_REGION", "us-west-2")
    key_id  = os.environ.get("AWS_ACCESS_KEY_ID")
    secret  = os.environ.get("AWS_SECRET_ACCESS_KEY")
    if not key_id or not secret:
        raise HTTPException(500, "S3 credentials not configured on server")

    suffix = Path(file.filename).suffix.lower() if file.filename else ""
    if suffix not in {".pgn", ".txt", ".jpg", ".jpeg", ".png", ".gif", ".webp", ".heic"}:
        suffix = ".pgn"
    key = f"pgn/{user['id']}/{uuid.uuid4().hex}{suffix}"

    content      = await file.read()
    content_type = file.content_type or ("image/jpeg" if suffix in {".jpg", ".jpeg", ".heic"} else "text/plain")

    try:
        s3 = boto3.client("s3", region_name=region,
                          aws_access_key_id=key_id, aws_secret_access_key=secret)
        s3.put_object(Bucket=bucket, Key=key, Body=content,
                      ContentType=content_type)
    except (BotoCoreError, ClientError) as exc:
        raise HTTPException(500, f"S3 upload failed: {exc}")

    return {"url": f"https://{bucket}.s3.amazonaws.com/{key}"}


@app.get("/api/pgn/presign")
async def pgn_presign(ext: str = ".jpg", user: dict = Depends(require_login)):
    import boto3
    from botocore.exceptions import BotoCoreError, ClientError

    bucket = os.environ.get("AWS_S3_BUCKET", "occ-webhook-photos-2026")
    region = os.environ.get("AWS_REGION", "us-west-2")
    key_id = os.environ.get("AWS_ACCESS_KEY_ID")
    secret = os.environ.get("AWS_SECRET_ACCESS_KEY")
    if not key_id or not secret:
        raise HTTPException(500, "S3 credentials not configured on server")

    safe_ext = ext.lower() if ext.lower() in {".jpg", ".jpeg", ".png", ".webp", ".pgn", ".txt"} else ".jpg"
    ct_map = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
              ".webp": "image/webp", ".pgn": "text/plain", ".txt": "text/plain"}
    content_type = ct_map.get(safe_ext, "image/jpeg")

    key = f"pgn/{user['id']}/{uuid.uuid4().hex}{safe_ext}"
    try:
        s3 = boto3.client("s3", region_name=region,
                          aws_access_key_id=key_id, aws_secret_access_key=secret)
        upload_url = s3.generate_presigned_url(
            "put_object",
            Params={"Bucket": bucket, "Key": key, "ContentType": content_type},
            ExpiresIn=300,
        )
    except (BotoCoreError, ClientError) as exc:
        raise HTTPException(500, f"Presign failed: {exc}")

    return {"upload_url": upload_url, "public_url": f"https://{bucket}.s3.amazonaws.com/{key}"}


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


@app.get("/privacy", response_class=HTMLResponse)
async def privacy(request: Request):
    return templates.TemplateResponse(request=request, name="privacy.html")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
