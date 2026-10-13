import asyncio
import ipaddress
import logging
import socket
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
import anthropic
import json
from typing import Optional, List
import stripe

from database import (
    get_tournaments, create_tournament, get_tournament,
    add_player, get_players, delete_player,
    record_result, get_pairings_for_round, get_standings,
    update_current_round, store_pairing, get_player_rank_map,
    import_uscf_members, search_uscf_members, lookup_uscf_member, get_uscf_db_count,
    save_uscf_ratings, get_uscf_rating_snapshot,
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
    list_deleted_user_tournaments, undelete_user_tournament,
    update_user_contact,
    add_featured_tournament, list_featured_tournaments, list_featured_tournaments_for_user,
    update_featured_tournament, delete_featured_tournament,
    get_user_location,
    upsert_research_players, list_research_players, delete_research_player,
    delete_research_by_source, list_research_sources,
    get_research_players_by_rank_range, get_research_player_count,
    upsert_player_chessbase, get_player_chessbase,
    get_research_player_fide_id_by_user,
    update_research_player_links, get_research_player_by_id,
    store_round_results, get_round_results, list_imported_rounds,
    get_player_scores_after_round, get_research_players_by_start_nos, get_round_urls,
    store_standings, get_round_standings, list_imported_standings,
    list_federations, list_countries, get_federations_for_country,
    get_federation, create_federation, update_federation, delete_federation,
    list_cities, create_city, delete_city, update_user_location,
    list_pgn_organizers, add_pgn_organizer, delete_pgn_organizer,
    list_pgn_sources, get_pgn_source, upsert_pgn_source, update_pgn_source_fetched,
    list_live_pgn_sources, delete_pgn_source,
    insert_pgn_games, list_pgn_games, count_pgn_games, get_pgn_game_raw, download_pgn_games,
    list_pgn_events, count_pgn_games_for_source, lichess_round_already_known,
    get_pgn_games_by_fide_id,
    report_online_id, list_online_ids, set_online_id_status, get_confirmed_online_id,
    get_coach_profile, update_coach_profile, set_coach_stripe_account,
    add_coach_student, remove_coach_student, is_coach_student, list_coach_students, list_coaches_for_student,
    create_coach_session, delete_coach_session, list_coach_sessions, list_student_sessions,
    create_homework, get_homework, delete_homework, list_coach_homework, list_student_homework,
    get_assignment, list_homework_assignments, submit_assignment, grade_assignment, mark_puzzle_solved,
    create_course, set_course_active, get_course, list_courses, list_coach_courses,
    create_enrollment, get_enrollment, get_user_enrollment, mark_enrollment_paid, list_user_enrollments,
    search_users,
    ensure_pgn_source, get_pgn_game_ids_by_hashes, find_pgn_games_by_surname, find_pgn_games_by_fide_id,
    link_player_games, get_player_game_link, set_player_game_link_status, list_player_games,
    count_player_games, download_player_games_pgn,
)
from trf_builder import build_trf
from auth import get_current_user, require_login, require_td, require_admin, require_coach
from notify import send_verification_email, send_verification_sms, send_password_reset_email, send_student_invite_email
from fide import calculate_rating, generate_pdf as fide_generate_pdf
import pgn_harvester as ph
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired

SECRET_KEY = os.environ.get("SECRET_KEY")
if not SECRET_KEY:
    if os.environ.get("FLY_APP_NAME"):
        # Deployed on Fly.io with no SECRET_KEY set — refuse to start with a
        # known, publicly-committed default that would let anyone forge
        # signed session cookies (including admin sessions).
        raise RuntimeError(
            "SECRET_KEY environment variable must be set in production "
            "(e.g. `fly secrets set SECRET_KEY=...`)"
        )
    logging.warning("SECRET_KEY not set — using insecure default for local development only")
    SECRET_KEY = "dev-secret-key-change-in-production"
STRIPE_SECRET_KEY = os.environ.get("STRIPE_SECRET_KEY", "")
STRIPE_WEBHOOK_SECRET = os.environ.get("STRIPE_WEBHOOK_SECRET", "")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
# Workspace to bill Claude usage to. With a static key that is not workspace-scoped
# it is sent as the anthropic-workspace-id header; with federation it scopes the
# minted token (may be the literal "default").
ANTHROPIC_WORKSPACE_ID = os.environ.get("ANTHROPIC_WORKSPACE_ID", "")
# Workload Identity Federation: no static key. The Fly machine mints an OIDC JWT
# (POST /v1/tokens/oidc on the local Fly API socket) and the SDK exchanges it for a
# short-lived Anthropic token acting as the rule's service account. Configure the
# issuer https://oidc.fly.io/<org-slug> and a rule matching subject prefix
# <org-slug>:<app-name>: in the Claude Console (Settings -> Workload identity).
ANTHROPIC_FEDERATION_RULE_ID = os.environ.get("ANTHROPIC_FEDERATION_RULE_ID", "")
ANTHROPIC_ORGANIZATION_ID = os.environ.get("ANTHROPIC_ORGANIZATION_ID", "")
ANTHROPIC_SERVICE_ACCOUNT_ID = os.environ.get("ANTHROPIC_SERVICE_ACCOUNT_ID", "")
# Alternative token source for non-Fly hosts (Kubernetes projected token etc.).
ANTHROPIC_IDENTITY_TOKEN_FILE = os.environ.get("ANTHROPIC_IDENTITY_TOKEN_FILE", "")
# Optional "aud" claim to request from Fly, if the federation rule matches on audience.
ANTHROPIC_IDENTITY_TOKEN_AUDIENCE = os.environ.get("ANTHROPIC_IDENTITY_TOKEN_AUDIENCE", "")
FLY_API_SOCKET = os.environ.get("FLY_API_SOCKET", "/.fly/api")
if ANTHROPIC_API_KEY and ANTHROPIC_FEDERATION_RULE_ID:
    logging.warning("Both ANTHROPIC_API_KEY and ANTHROPIC_FEDERATION_RULE_ID are set; "
                    "the static key takes precedence, so federation is not in use")
if STRIPE_SECRET_KEY and not STRIPE_WEBHOOK_SECRET:
    logging.error(
        "STRIPE_SECRET_KEY is set but STRIPE_WEBHOOK_SECRET is not — payment "
        "confirmation relies solely on the payer's browser returning to the "
        "success page and will be lost if it never does. Add a webhook "
        "endpoint for /stripe/webhook in the Stripe dashboard and set "
        "STRIPE_WEBHOOK_SECRET."
    )

_cancel_serializer = URLSafeTimedSerializer(SECRET_KEY, salt="register-cancel")

def _make_cancel_token(player_id: int) -> str:
    return _cancel_serializer.dumps(player_id)

def _verify_cancel_token(token: str, max_age: int = 86400) -> Optional[int]:
    try:
        return int(_cancel_serializer.loads(token, max_age=max_age))
    except (BadSignature, SignatureExpired, ValueError, TypeError):
        return None

# Unguessable receipt token for the registration success/receipt page — a
# sequential player_id alone must never be enough to view someone else's
# registration (name/email/phone/payment status).
_view_serializer = URLSafeTimedSerializer(SECRET_KEY, salt="register-view")

def _make_view_token(player_id: int) -> str:
    return _view_serializer.dumps(player_id)

def _verify_view_token(token: str) -> Optional[int]:
    try:
        return int(_view_serializer.loads(token))
    except (BadSignature, SignatureExpired, ValueError, TypeError):
        return None

# ---------------------------------------------------------------------------
# Simple in-memory brute-force lockout for login / OTP verification.
#
# Single-process in-memory state is good enough here: the app runs as one
# uvicorn worker (see Dockerfile) and the goal is slowing down automated
# guessing, not perfect distributed rate limiting.
# ---------------------------------------------------------------------------
_RATE_LIMIT_THRESHOLD = 5
_RATE_LIMIT_WINDOW = 15 * 60  # seconds
_failed_attempts: dict = {}  # key -> (count, window_start_monotonic)

def _rate_limit_allows(key: str) -> bool:
    entry = _failed_attempts.get(key)
    if not entry:
        return True
    count, window_start = entry
    if time.monotonic() - window_start > _RATE_LIMIT_WINDOW:
        del _failed_attempts[key]
        return True
    return count < _RATE_LIMIT_THRESHOLD

def _rate_limit_record_failure(key: str):
    now = time.monotonic()
    count, window_start = _failed_attempts.get(key, (0, now))
    if now - window_start > _RATE_LIMIT_WINDOW:
        count, window_start = 0, now
    _failed_attempts[key] = (count + 1, window_start)

def _rate_limit_clear(key: str):
    _failed_attempts.pop(key, None)

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

def _user_tournament_context(user_id: int) -> dict:
    """Return featured_tournaments and federation info scoped to the user's location."""
    loc = get_user_location(user_id)
    federation = get_federation(loc["federation_id"]) if loc.get("federation_id") else None
    tournaments = list_featured_tournaments_for_user(user_id)
    return {
        "featured_tournaments": tournaments,
        "user_federation": federation,
        "user_location": loc,
    }

app = FastAPI(title="MyChessRating Pairings")
app.add_middleware(SessionMiddleware, secret_key=SECRET_KEY)
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")
templates.env.filters["from_json"] = __import__("json").loads

# ---------------------------------------------------------------------------
# CSRF defense-in-depth: reject state-changing requests whose Origin/Referer
# names a different site. SameSite=Lax session cookies (Starlette's default)
# already block most cross-site POSTs, but this doesn't depend on that
# default staying in place. Browsers send Origin on virtually all POSTs, so
# this only trips on an actual cross-site request — plain non-browser clients
# that omit both headers entirely are left alone.
# ---------------------------------------------------------------------------
_CSRF_SAFE_METHODS = {"GET", "HEAD", "OPTIONS", "TRACE"}
_CSRF_EXEMPT_PATHS = {"/stripe/webhook"}  # authenticated by Stripe signature instead


@app.middleware("http")
async def csrf_origin_check(request: Request, call_next):
    if request.method not in _CSRF_SAFE_METHODS and request.url.path not in _CSRF_EXEMPT_PATHS:
        from urllib.parse import urlparse
        source = request.headers.get("origin") or request.headers.get("referer")
        host_header = (request.headers.get("host") or "").split(":")[0].lower()
        if source and host_header:
            source_host = (urlparse(source).hostname or "").lower()
            if source_host and source_host != host_header:
                return JSONResponse({"error": "Cross-site request blocked"}, status_code=403)
    return await call_next(request)

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
def login_page(request: Request, next: str = "/"):
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
    client_ip = request.client.host if request.client else "unknown"
    rl_key = f"login:{client_ip}:{username.strip().lower()}"
    if not _rate_limit_allows(rl_key):
        return templates.TemplateResponse(request=request, name="login.html",
                                          context={"next": next, "error": "Too many failed attempts. Please try again in a few minutes."},
                                          status_code=429)
    user = get_user_by_username(username)
    if not user or not verify_password(password, user["password_hash"]):
        _rate_limit_record_failure(rl_key)
        return templates.TemplateResponse(request=request, name="login.html",
                                          context={"next": next, "error": "Invalid username or password"})
    _rate_limit_clear(rl_key)
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
def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/login", status_code=303)


# ---------------------------------------------------------------------------
# Self-registration
# ---------------------------------------------------------------------------

@app.get("/register", response_class=HTMLResponse)
def register_page(request: Request):
    if get_current_user(request):
        return RedirectResponse("/", status_code=303)
    return templates.TemplateResponse(request=request, name="register.html", context={
        "error": None,
        "reg_method": get_setting("registration_method", "both"),
        "countries": list_countries(),
        "federations": list_federations(active_only=True),
    })

@app.post("/register", response_class=HTMLResponse)
async def register_submit(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    confirm: str = Form(...),
    channel: str = Form(...),       # "email" or "sms"
    contact: str = Form(...),       # email address or phone number
    country_code: str = Form(""),
    city: str = Form(""),
    federation_id: str = Form(""),
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
                                          context={"error": error, "reg_method": reg_method,
                                                   "countries": list_countries(),
                                                   "federations": list_federations(active_only=True)})

    email = contact if channel == "email" else None
    phone = contact if channel == "sms" else None
    fed_id = int(federation_id) if federation_id.isdigit() else None

    if get_setting("require_verification", "1") == "0":
        # Verification disabled — activate immediately
        uid = create_pending_user(username, password, email=email, phone=phone)
        activate_user(uid)
        if country_code or city:
            update_user_location(uid, country_code.strip(), city.strip(), fed_id)
        from database import DB_FILE
        import sqlite3 as _sq
        row = _sq.connect(DB_FILE).execute("SELECT id, username, role FROM users WHERE id=?", (uid,)).fetchone()
        if row:
            request.session["user"] = {"id": row[0], "username": row[1], "role": row[2]}
        return RedirectResponse("/", status_code=303)

    uid = create_pending_user(username, password, email=email, phone=phone)
    if country_code or city:
        update_user_location(uid, country_code.strip(), city.strip(), fed_id)
    token = create_verification_token(uid, channel, contact)

    try:
        if channel == "email":
            await send_verification_email(contact, token)
        else:
            await send_verification_sms(contact, token)
    except Exception as e:
        error = str(e)
        return templates.TemplateResponse(request=request, name="register.html",
                                          context={"error": error, "reg_method": reg_method,
                                                   "countries": list_countries(),
                                                   "federations": list_federations(active_only=True)})

    request.session["pending_user_id"] = uid
    request.session["pending_contact"] = contact
    request.session["pending_channel"] = channel
    return RedirectResponse("/verify", status_code=303)

@app.get("/verify", response_class=HTMLResponse)
def verify_page(request: Request):
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
def verify_submit(request: Request, code: str = Form(...)):
    uid = request.session.get("pending_user_id")
    if not uid:
        return RedirectResponse("/register", status_code=303)

    rl_key = f"verify:{uid}"
    if not _rate_limit_allows(rl_key):
        return templates.TemplateResponse(request=request, name="verify.html", context={
            "contact": request.session.get("pending_contact"),
            "channel": request.session.get("pending_channel"),
            "error": "Too many incorrect attempts. Request a new code.",
        }, status_code=429)

    if not check_and_consume_token(uid, code.strip()):
        _rate_limit_record_failure(rl_key)
        return templates.TemplateResponse(request=request, name="verify.html", context={
            "contact": request.session.get("pending_contact"),
            "channel": request.session.get("pending_channel"),
            "error": "Invalid or expired code. Request a new one.",
        })
    _rate_limit_clear(rl_key)

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
def forgot_password_page(request: Request):
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
def reset_password_page(request: Request, token: str = ""):
    if not token:
        return RedirectResponse("/forgot-password", status_code=303)
    return templates.TemplateResponse(request=request, name="reset_password.html",
                                      context={"token": token})

@app.post("/reset-password", response_class=HTMLResponse)
def reset_password_submit(
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
def users_page(request: Request, saved: Optional[str] = None, user: dict = Depends(require_admin)):
    users = list_users()
    return templates.TemplateResponse(request=request, name="users.html", context={
        "users": users,
        "current_user": user,
        "reg_method": get_setting("registration_method", "both"),
        "login_message": get_setting("login_message", ""),
        "require_verification": get_setting("require_verification", "1") == "1",
        "platform_fee": _platform_fee_percent(),
        "saved": saved == "1",
    })

@app.post("/admin/settings/platform-fee")
def save_platform_fee(platform_fee_percent: str = Form("15"), _user: dict = Depends(require_admin)):
    try:
        pct = max(0, min(100, int(platform_fee_percent)))
    except ValueError:
        pct = 15
    set_setting("platform_fee_percent", str(pct))
    return RedirectResponse("/users?saved=1", status_code=303)

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
def save_login_message(
    login_message: str = Form(""),
    _user: dict = Depends(require_admin),
):
    set_setting("login_message", login_message.strip())
    return RedirectResponse("/users?saved=1", status_code=303)

@app.post("/users")
def create_user_route(
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
def delete_user_route(uid: int, current: dict = Depends(require_admin)):
    if uid != current["id"]:  # prevent self-deletion
        delete_user(uid)
    return RedirectResponse("/users", status_code=303)

@app.post("/users/{uid}/password")
def change_password_route(
    uid: int,
    new_password: str = Form(...),
    _user: dict = Depends(require_admin),
):
    update_user_password(uid, new_password)
    return RedirectResponse("/users", status_code=303)

@app.post("/users/{uid}/edit")
def edit_user_route(
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

    live_rating = live_quick = live_blitz = 0
    for section in sections_data.get("items", []):
        for record in section.get("ratingRecords", []):
            src = record.get("ratingSource")
            val = record.get("postRating", 0)
            if src == "R" and not live_rating:   live_rating = val
            elif src == "Q" and not live_quick:  live_quick  = val
            elif src == "B" and not live_blitz:  live_blitz  = val

    if not name:
        return {}

    result = {
        "name": name, "uscf_id": uscf_id, "uscf_rating": rating,
        "live_uscf_rating": live_rating,
        "live_quick_rating": live_quick,
        "live_blitz_rating": live_blitz,
        "fide_id": fide_id,
        "fide_rating": fide_rating, "expiry": expiry,
    }
    _cache_set(f"pub_details_{uscf_id}", result, ttl=240)
    return result


# ---------------------------------------------------------------------------
# Main app routes
# ---------------------------------------------------------------------------

@app.get("/", response_class=HTMLResponse)
def home(request: Request, user: dict = Depends(require_login)):
    return RedirectResponse("/player-lookup", status_code=302)

@app.get("/tournaments", response_class=HTMLResponse)
def tournaments_home(request: Request, user: dict = Depends(require_td)):
    tournaments = get_tournaments()
    return templates.TemplateResponse(request=request, name="tournament_list.html",
                                      context={"tournaments": tournaments, "current_user": user})

@app.post("/tournament")
def new_tournament(
    name: str = Form(...),
    rounds: int = Form(5),
    system: str = Form("dutch"),
    entry_fee: float = Form(0),
    _user: dict = Depends(require_td),
):
    tid = create_tournament(name, rounds, system, entry_fee)
    return RedirectResponse(f"/tournament/{tid}", status_code=303)

@app.get("/tournament/{tid}", response_class=HTMLResponse)
def tournament_detail(request: Request, tid: int, imported: Optional[int] = None,
                             unresolved: Optional[str] = None,
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
        "unresolved": unresolved,
        "current_user": user,
    })

async def _uscf_live_search(q: str) -> list:
    """Search USCF thin2.php for players not yet in the local DB (e.g. new registrations)."""
    try:
        headers = {"User-Agent": "Mozilla/5.0 (compatible; MyChessRating/1.0)"}
        parts = q.split()
        data = {"memln": parts[-1], "memfn": " ".join(parts[:-1]) if len(parts) > 1 else "", "mode": "Search"}
        async with httpx.AsyncClient(timeout=8, follow_redirects=True) as client:
            r = await client.post("https://www.uschess.org/msa/thin2.php", data=data, headers=headers)
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
            r = await client.get(f"https://www.uschess.org/msa/thin3.php?{uscf_id}", headers=headers)
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
            r = await client.post("https://www.uschess.org/msa/thin2.php", data=data, headers=headers)
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
def register_player(tid: int, name: str = Form(...), uscf_id: Optional[str] = Form(None),
                          rating: Optional[int] = Form(None), email: Optional[str] = Form(None),
                          fide_id: Optional[str] = Form(None), _user: dict = Depends(require_td)):
    add_player(tid, name, uscf_id, rating, email, fide_id or None)
    return RedirectResponse(f"/tournament/{tid}", status_code=303)

@app.post("/tournament/{tid}/import-players")
async def import_players_csv(tid: int, file: UploadFile = File(...), _user: dict = Depends(require_td)):
    content = await file.read()
    text = content.decode("utf-8-sig", errors="replace")
    added = 0
    unresolved = []  # names added without a USCF match, needing manual TD review
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
                        r = await client.get(f"https://www.uschess.org/msa/thin3.php?{uscf_id}", headers=headers)
                    if r.status_code == 200 and "memname" in r.text:
                        data = _parse_uscf_thin3(r.text)
                        name = data["name"] or name
                        rating = data["rating"]
                except Exception:
                    pass
            if not name:
                continue
        elif name:
            # Rows with only a name (no USCF ID) are ambiguous: search_uscf_members
            # ranks by rating, so a common surname like "Smith" would otherwise
            # silently bind to the highest-rated Smith in the whole USCF database.
            # Only auto-bind when exactly one candidate's formatted name matches
            # the CSV name exactly; otherwise add the player unrated/unlinked and
            # flag the row for the TD to resolve by hand.
            candidates = search_uscf_members(name, limit=5)
            exact = [c for c in candidates
                     if _format_uscf_name(c["name"]).strip().lower() == name.strip().lower()]
            if len(exact) == 1:
                top = exact[0]
                uscf_id = top["uscf_id"]
                name = _format_uscf_name(top["name"])
                rating = top["rating"]
                fide_id = top.get("fide_id")
            else:
                unresolved.append(name)
        else:
            continue

        add_player(tid, name, uscf_id, rating, fide_id=fide_id)
        added += 1

    url = f"/tournament/{tid}?imported={added}"
    if unresolved:
        from urllib.parse import quote
        url += f"&unresolved={quote(', '.join(unresolved))}"
    return RedirectResponse(url, status_code=303)

@app.post("/player/{pid}/delete")
def remove_player(pid: int, _user: dict = Depends(require_td)):
    tid = delete_player(pid)
    return RedirectResponse(f"/tournament/{tid}" if tid else "/", status_code=303)

# HTMX: Round table fragment
@app.get("/tournament/{tid}/round/{round_num}/table", response_class=HTMLResponse)
def round_table_fragment(request: Request, tid: int, round_num: int,
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
    return round_table_fragment(request, tid, round_num)

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
    return round_table_fragment(request, tid, round_num)

# Generate next round
@app.post("/tournament/{tid}/next-round", response_class=HTMLResponse)
async def generate_next_round(request: Request, tid: int, _user: dict = Depends(require_td)):
    tournament = get_tournament(tid)
    if not tournament:
        raise HTTPException(404)
    current = tournament.get("current_round", 0)
    total_rounds = tournament.get("rounds", 0)
    if current >= total_rounds:
        return HTMLResponse(
            f'<div class="alert alert-warning">This tournament is already at its final round ({total_rounds}).</div>'
        )
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

        # Run off the event loop thread: bbpPairings can take several seconds
        # on a large field, and this route is the single worst blocking call
        # in the app (see mychesspairings-859) — a plain subprocess.run here
        # would stall every other concurrent request on the single uvicorn worker.
        proc = await asyncio.to_thread(
            subprocess.run,
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

    return round_table_fragment(request, tid, next_r)

# Download TRF-2026
@app.get("/tournament/{tid}/trf")
def download_trf(tid: int, _user: dict = Depends(require_login)):
    trf_text = build_trf(tid)
    file_path = f"trf_{tid}.trf"
    Path(file_path).write_text(trf_text)
    return FileResponse(file_path, media_type="text/plain", filename=f"tournament_{tid}.trf")

@app.get("/tournament/{tid}/trf-debug")
def trf_debug(tid: int, _user: dict = Depends(require_admin)):
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
def view_standings(request: Request, tid: int, user: dict = Depends(require_login)):
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
def uscf_db_page(request: Request, imported: Optional[int] = None,
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
def uscf_col_debug(_user: dict = Depends(require_admin)):
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
def admin_tournaments_page(request: Request, user: dict = Depends(require_admin)):
    return templates.TemplateResponse(request=request, name="admin_tournaments.html",
                                      context={"tournaments": list_featured_tournaments(),
                                               "countries": list_countries()})

@app.post("/admin/tournaments")
def admin_add_tournament(
    request: Request,
    name: str = Form(...),
    subtitle: str = Form(""),
    description: str = Form(""),
    info_url: str = Form(""),
    pairings_url: str = Form(""),
    source: str = Form("manual"),
    source_url: str = Form(""),
    display_order: int = Form(0),
    country_code: str = Form(""),
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
        country_code=country_code.strip(),
    )
    return RedirectResponse("/admin/tournaments", status_code=303)

@app.post("/admin/tournaments/{fid}/toggle")
def admin_toggle_tournament(fid: int, _user: dict = Depends(require_admin)):
    conn = __import__("sqlite3").connect(__import__("database").DB_FILE)
    row = conn.execute("SELECT active FROM featured_tournaments WHERE id=?", (fid,)).fetchone()
    conn.close()
    if row is not None:
        update_featured_tournament(fid, active=0 if row[0] else 1)
    return RedirectResponse("/admin/tournaments", status_code=303)

@app.post("/admin/tournaments/{fid}/delete")
def admin_delete_tournament(fid: int, _user: dict = Depends(require_admin)):
    delete_featured_tournament(fid)
    return RedirectResponse("/admin/tournaments", status_code=303)

@app.post("/admin/tournaments/{fid}/edit")
def admin_edit_tournament(
    fid: int,
    name: str = Form(...),
    subtitle: str = Form(""),
    description: str = Form(""),
    info_url: str = Form(""),
    pairings_url: str = Form(""),
    display_order: int = Form(0),
    country_code: str = Form(""),
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
        country_code=country_code.strip(),
    )
    return RedirectResponse("/admin/tournaments", status_code=303)

@app.get("/api/federations-by-country/{country_code}")
def api_federations_by_country(country_code: str):
    return get_federations_for_country(country_code)

@app.get("/api/cities/{federation_id}")
def api_cities(federation_id: int):
    return list_cities(federation_id)

# ---------------------------------------------------------------------------
# Admin — Chess Federations
# ---------------------------------------------------------------------------

@app.get("/admin/federations", response_class=HTMLResponse)
def admin_federations_page(request: Request, q: str = "", _user: dict = Depends(require_admin)):
    all_feds = list_federations()
    total_count = len(all_feds)
    if q:
        q_lower = q.lower()
        feds = [f for f in all_feds if q_lower in f["name"].lower() or q_lower in f["country_name"].lower()
                or q_lower in (f["abbreviation"] or "").lower() or q_lower in (f["country_code"] or "").lower()]
    else:
        feds = all_feds
    # Only load cities when searching (avoids 190+ queries on unfiltered view)
    feds_with_cities = [{**f, "cities": list_cities(f["id"]) if q else []} for f in feds]
    return templates.TemplateResponse(request=request, name="admin_federations.html",
                                      context={"federations": feds_with_cities, "q": q,
                                               "total_count": total_count})

@app.post("/admin/federations")
def admin_add_federation(
    request: Request,
    name: str = Form(...),
    abbreviation: str = Form(""),
    country_code: str = Form(""),
    country_name: str = Form(""),
    rating_system: str = Form(""),
    website_url: str = Form(""),
    tournaments_url: str = Form(""),
    display_order: int = Form(0),
    _user: dict = Depends(require_admin),
):
    create_federation(name.strip(), abbreviation.strip(), country_code.strip().upper(),
                      country_name.strip(), rating_system.strip(),
                      website_url.strip(), tournaments_url.strip(), display_order)
    return RedirectResponse("/admin/federations", status_code=303)

@app.post("/admin/federations/{fid}/toggle")
def admin_toggle_federation(fid: int, _user: dict = Depends(require_admin)):
    fed = get_federation(fid)
    if fed:
        update_federation(fid, active=0 if fed["active"] else 1)
    return RedirectResponse("/admin/federations", status_code=303)

@app.post("/admin/federations/{fid}/delete")
def admin_delete_federation(fid: int, _user: dict = Depends(require_admin)):
    delete_federation(fid)
    return RedirectResponse("/admin/federations", status_code=303)

@app.post("/admin/federations/{fid}/edit")
def admin_edit_federation(
    fid: int,
    name: str = Form(...),
    abbreviation: str = Form(""),
    country_code: str = Form(""),
    country_name: str = Form(""),
    rating_system: str = Form(""),
    website_url: str = Form(""),
    tournaments_url: str = Form(""),
    display_order: int = Form(0),
    _user: dict = Depends(require_admin),
):
    update_federation(fid, name=name.strip(), abbreviation=abbreviation.strip(),
                      country_code=country_code.strip().upper(), country_name=country_name.strip(),
                      rating_system=rating_system.strip(), website_url=website_url.strip(),
                      tournaments_url=tournaments_url.strip(), display_order=display_order)
    return RedirectResponse("/admin/federations", status_code=303)

@app.post("/admin/federations/{fid}/add-city")
def admin_add_city(
    fid: int,
    city_name: str = Form(...),
    region: str = Form(""),
    display_order: int = Form(0),
    _user: dict = Depends(require_admin),
):
    create_city(fid, city_name.strip(), region.strip(), display_order)
    return RedirectResponse("/admin/federations", status_code=303)

@app.post("/admin/federations/cities/{cid}/delete")
def admin_delete_city(cid: int, _user: dict = Depends(require_admin)):
    delete_city(cid)
    return RedirectResponse("/admin/federations", status_code=303)

def _is_safe_external_url(url: str) -> bool:
    """Reject URLs that would make the server fetch something internal
    (loopback, private/link-local ranges, cloud metadata endpoints, etc)."""
    from urllib.parse import urlparse
    try:
        parsed = urlparse(url)
    except ValueError:
        return False
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        return False
    hostname = parsed.hostname.lower()
    if hostname in ("localhost",) or hostname.endswith(".localhost"):
        return False
    try:
        addrs = socket.getaddrinfo(hostname, None)
    except socket.gaierror:
        return False
    for family, _, _, _, sockaddr in addrs:
        ip = ipaddress.ip_address(sockaddr[0])
        if (ip.is_private or ip.is_loopback or ip.is_link_local or
                ip.is_multicast or ip.is_reserved or ip.is_unspecified):
            return False
    return True


async def _fetch_url_safely(url: str, max_redirects: int = 5) -> httpx.Response:
    """Like httpx's follow_redirects=True, but re-validates every hop so a
    redirect can't be used to bounce the request at an internal address."""
    async with httpx.AsyncClient(follow_redirects=False, timeout=10) as client:
        for _ in range(max_redirects + 1):
            if not _is_safe_external_url(url):
                raise ValueError("Blocked request to an internal or non-HTTP(S) address")
            resp = await client.get(url, headers={"User-Agent": "Mozilla/5.0 (compatible; MyChessRating/1.0)"})
            location = resp.headers.get("location")
            if resp.is_redirect and location:
                url = str(httpx.URL(url).join(location))
                continue
            return resp
    raise ValueError("Too many redirects")


@app.get("/admin/fetch-url")
async def admin_fetch_url(url: str, _user: dict = Depends(require_admin)):
    """Fetch a URL and return the page title for auto-fill when importing from external sources."""
    if not url.startswith(("http://", "https://")) or not _is_safe_external_url(url):
        return JSONResponse({"error": "Invalid URL"}, status_code=400)
    try:
        resp = await _fetch_url_safely(url)
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
    """Extract a player's game results from a USCF cross-table HTML page.

    The MSA cross-table is inside a <pre> block with pipe-delimited text and
    embedded HTML anchor tags.  Each player occupies 2-3 lines separated by a
    dashed rule:

        <a href=XtblPlr.php?...>9</a> | <a href=MbrDtlMain.php?31625896>NAKSHWIN</a> |2.5|B |L 12|D  4|W 14|
       TX | 31625896 / R: 1276->1353 | ...

    We parse the raw HTML directly line-by-line to avoid any regex that contains
    literal < > characters (those can get mangled by shell escaping when the
    function runs inside a container invoked via SSH).
    """
    # Locate the <pre> block
    pre_start = html.lower().find('<pre>')
    pre_end   = html.lower().find('</pre>', pre_start + 1)
    if pre_start < 0 or pre_end < 0:
        return []
    pre_text = html[pre_start + 5: pre_end]

    # Strip all HTML tags by removing every <...> span using a simple state machine
    # (avoids regex with angle brackets).
    def strip_html(text):
        out = []
        in_tag = False
        for ch in text:
            if ch == '<':
                in_tag = True
            elif ch == '>':
                in_tag = False
            elif not in_tag:
                out.append(ch)
        return ''.join(out)

    # For each line that contains a MbrDtlMain link, extract the USCF ID from the href.
    # Pattern in raw HTML: href=MbrDtlMain.php?DIGITS
    def extract_mbrdetail_id(raw_line):
        marker = 'MbrDtlMain.php?'
        idx = raw_line.find(marker)
        if idx < 0:
            return None
        rest = raw_line[idx + len(marker):]
        digits = []
        for ch in rest:
            if ch.isdigit():
                digits.append(ch)
            else:
                break
        return ''.join(digits) if digits else None

    # For each line that contains an XtblPlr link, extract the pair number
    # (it is the text content between >PAIRNUM</a>).
    def extract_pair_num(raw_line):
        marker = 'XtblPlr.php?'
        idx = raw_line.find(marker)
        if idx < 0:
            return None
        # find the closing > of this anchor tag
        close = raw_line.find('>', idx)
        if close < 0:
            return None
        # text until next <
        end = raw_line.find('<', close + 1)
        text = raw_line[close + 1: end if end >= 0 else close + 10].strip()
        return int(text) if text.isdigit() else None

    # Split pre_text into per-player blocks on dashed separator lines
    lines_raw = pre_text.split('\n')
    blocks = []
    current = []
    for raw_line in lines_raw:
        stripped = raw_line.strip()
        if stripped.startswith('-') and len(stripped) >= 20 and stripped.replace('-', '') == '':
            if current:
                blocks.append(current)
            current = []
        else:
            current.append(raw_line)
    if current:
        blocks.append(current)

    players = {}
    target_pair = None

    for block in blocks:
        # Find the line with XtblPlr (the player-row line)
        plr_line_raw = None
        for raw_line in block:
            if 'XtblPlr.php?' in raw_line:
                plr_line_raw = raw_line
                break
        if plr_line_raw is None:
            continue

        pair_num = extract_pair_num(plr_line_raw)
        if pair_num is None:
            continue

        player_uscf = extract_mbrdetail_id(plr_line_raw)

        # Name: strip HTML from player line, then take the text between first and second pipe
        plain_plr = strip_html(plr_line_raw)
        cols = plain_plr.split('|')
        player_name = cols[1].strip().title() if len(cols) > 1 else ''

        # Pre-rating from the rating line (contains " / R: NNN->NNN")
        pre_rating = None
        if player_uscf is None:
            # fall back: look for USCF ID in the rating line
            for raw_line in block:
                if ' / R:' in raw_line or '/ R:' in raw_line:
                    m = re.search(r'\b(\d{7,8})\b', raw_line)
                    if m:
                        player_uscf = m.group(1)
                    break
        for raw_line in block:
            rm = re.search(r'R:\s*P?(\d{3,4})\s*->', raw_line)
            if rm:
                pre_rating = int(rm.group(1))
                break

        # Round result cells: cols[3:] of the stripped player line
        results_cols = cols[3:] if len(cols) > 3 else []

        players[pair_num] = {
            'name': player_name,
            'uscf_id': player_uscf,
            'pre_rating': pre_rating,
            'results': results_cols,
        }
        if player_uscf == uscf_id:
            target_pair = pair_num

    if target_pair is None:
        return []

    target = players[target_pair]
    games = []
    result_map = {'W': '1', 'D': '0.5', 'L': '0'}

    for col in target['results']:
        txt = col.strip().upper()
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


async def _fetch_games_from_ratings_api(
    event_id: str, section_num: int, uscf_id: str, client: httpx.AsyncClient
) -> list:
    """Fallback: fetch game results from the new ratings-api.uschess.org for recent events."""
    headers = {
        "User-Agent": "Mozilla/5.0 (compatible; MyChessRating/1.0)",
        "Origin": "https://ratings.uschess.org",
    }
    standings_url = f"https://ratings-api.uschess.org/api/v1/rated-events/{event_id}/sections/{section_num}/standings?pageSize=200"

    try:
        standings_resp = await client.get(standings_url, headers=headers)
        standings_data = standings_resp.json() if standings_resp.status_code == 200 else {}
    except Exception:
        return []

    standings_items = standings_data.get("items", standings_data.get("data", []))
    if not standings_items:
        return []

    # Find the target player in standings to get rating system and round-ordered games
    target_player = next(
        (p for p in standings_items if str(p.get("memberId", "")) == uscf_id), None
    )
    if not target_player:
        return []

    # Determine rating system from player's ratings entry
    rating_system = "R"
    player_pre_rating = None
    for r in target_player.get("ratings", []):
        rating_system = r.get("ratingSystem", "R")
        player_pre_rating = r.get("preRating") or None
        break

    # Build opponent pre-rating lookup from all players in standings
    ratings_map = {}      # memberId → preRating
    post_ratings_map = {} # memberId → postRating
    for player in standings_items:
        member_id = str(player.get("memberId", ""))
        if not member_id:
            continue
        for r in player.get("ratings", []):
            if r.get("ratingSystem") == rating_system:
                pre = r.get("preRating")
                post = r.get("postRating")
                if pre:
                    ratings_map[member_id] = pre
                if post:
                    post_ratings_map[member_id] = post
                break

    # Build name lookup from standings (memberId → display name)
    name_map = {}
    for player in standings_items:
        mid = str(player.get("memberId", ""))
        if mid:
            name_map[mid] = f"{player.get('firstName', '')} {player.get('lastName', '')}".strip()

    # Use roundOutcomes sorted by roundNumber to get correct game order
    round_outcomes = sorted(
        target_player.get("roundOutcomes", []),
        key=lambda ro: ro.get("roundNumber", 0)
    )

    result = []
    for ro in round_outcomes:
        outcome = ro.get("outcome", "")
        # Score-string convention ('1'/'0.5'/'0'), matching _parse_uscf_crosstable
        # and every consumer of this endpoint's games in uscf_calculator.html
        # (the result <select> options, parseFloat(g.result) score sums, bye
        # detection). Returning literal 'W'/'L'/'D' here (as this fallback used
        # to) made imported games silently fail to populate their W/D/L
        # dropdown whenever the primary crosstable scrape failed and this
        # ratings-api fallback ran instead.
        if outcome == "Win":
            game_result = "1"
        elif outcome == "Loss":
            game_result = "0"
        else:
            game_result = "0.5"
        opp_id = str(ro.get("opponentMemberId", ""))
        opp_name = (
            name_map.get(opp_id)
            or f"{ro.get('opponentFirstName', '')} {ro.get('opponentLastName', '')}".strip()
        )
        result.append({
            "name": opp_name,
            "rating": ratings_map.get(opp_id),
            "post_rating": post_ratings_map.get(opp_id),
            "uscfId": opp_id,
            "result": game_result,
            "provisional": None,
            "games": None,
        })

    # Attach player pre-rating as metadata on first item so caller can surface it
    if result and player_pre_rating:
        result[0]["_player_pre_rating"] = player_pre_rating

    return result


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
            if not games:
                games = await _fetch_games_from_ratings_api(event_id, section_num, uscf_id, client)
    except Exception:
        games = []

    # Extract player pre-rating if present (injected by ratings API fallback)
    pre_rating = None
    if games and "_player_pre_rating" in games[0]:
        pre_rating = games[0].pop("_player_pre_rating")

    result = {"games": games, "pre_rating": pre_rating}
    _cache_set(cache_key, result, ttl=600)
    return result

# ---------------------------------------------------------------------------
# Player Rating Lookup
# ---------------------------------------------------------------------------

@app.get("/player-lookup", response_class=HTMLResponse)
def player_lookup_page(request: Request, user: dict = Depends(require_login)):
    return templates.TemplateResponse(request=request, name="player_lookup.html",
                                      context={"current_user": user,
                                               **_user_tournament_context(user["id"])})

async def _fetch_uscf_sections(uscf_id: str) -> dict:
    key = f"uscf_sections_{uscf_id}"
    data = _cache_get(key)
    if data is None:
        headers = {
            "User-Agent": "Mozilla/5.0 (compatible; MyChessRating/1.0)",
            "Accept": "application/json",
            "Origin": "https://ratings.uschess.org",
        }
        try:
            async with httpx.AsyncClient(timeout=6, follow_redirects=True) as client:
                r = await client.get(f"https://ratings-api.uschess.org/api/v1/members/{uscf_id}/sections", headers=headers)
            data = r.json() if r.status_code == 200 else {}
        except Exception:
            data = {}
        _cache_set(key, data, ttl=240)
    return data


async def _live_regular_rating(uscf_id: str) -> int:
    """Latest post-tournament over-the-board Regular rating; 0 if unavailable."""
    data = await _fetch_uscf_sections(uscf_id)
    for section in data.get("items", []):
        for record in section.get("ratingRecords", []):
            if record.get("ratingSource") == "R" and record.get("postRating"):
                return record["postRating"]
    return 0


async def _live_online_regular_rating(uscf_id: str) -> int:
    """Latest post-event USCF Online Regular rating (ratingSource 'OR'); 0 if unavailable."""
    data = await _fetch_uscf_sections(uscf_id)
    for section in data.get("items", []):
        for record in section.get("ratingRecords", []):
            if record.get("ratingSource") == "OR" and record.get("postRating"):
                return record["postRating"]
    return 0


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

    local = local[:12]
    live_ratings, online_ratings = await asyncio.gather(
        asyncio.gather(*(_live_regular_rating(r["uscf_id"]) for r in local)),
        asyncio.gather(*(_live_online_regular_rating(r["uscf_id"]) for r in local)),
    )

    save_uscf_ratings((r["uscf_id"], r.get("rating"), lr) for r, lr in zip(local, live_ratings))

    items = ""
    for r, live_rating, online_rating in zip(local, live_ratings, online_ratings):
        display = html.escape(_format_uscf_name(r["name"]))
        rating_str = f' — {r["rating"]}' if r.get("rating") else ""
        live_str = (f' <span class="badge text-bg-success">Live {live_rating}</span>'
                    if live_rating else "")
        online_str = (f' <span class="badge text-bg-info">Online {online_rating}</span>'
                      if online_rating else "")
        items += (
            f'<button type="button" class="list-group-item list-group-item-action py-1 small"'
            f' onclick="selectPlayer(\'{html.escape(display)}\', \'{r["uscf_id"]}\')">'
            f'{display} <span class="text-muted">{r["uscf_id"]}</span>{html.escape(rating_str)}{live_str}{online_str}'
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
    monthly_online_rating = 0
    if member_data:
        if not name:
            name = f"{member_data.get('firstName', '')} {member_data.get('lastName', '')}".strip().title()
        for entry in member_data.get("ratings", []):
            if entry.get("ratingSystem") == "R" and entry.get("rating"):
                rating = entry["rating"]
            elif entry.get("ratingSystem") == "OR" and entry.get("rating"):
                monthly_online_rating = entry["rating"]
    live_online_rating = 0
    for section in sections_data.get("items", []):
        for record in section.get("ratingRecords", []):
            if record.get("ratingSource") == "R" and not live_rating:
                live_rating = record.get("postRating", 0)
            elif record.get("ratingSource") == "OR" and not live_online_rating:
                live_online_rating = record.get("postRating", 0)

    if not name:
        return HTMLResponse('<div class="alert alert-warning">Player not found.</div>')

    online_rating = live_online_rating or monthly_online_rating
    save_uscf_ratings([(uscf_id, rating, live_rating)])

    return templates.TemplateResponse(request=request, name="fragments/player_details.html", context={
        "name": name, "uscf_id": uscf_id, "rating": rating,
        "fide_id": fide_id, "expiry": expiry,
        "live_rating": live_rating, "fide_rating": fide_rating,
        "online_rating": online_rating,
        "monthly_online_rating": monthly_online_rating,
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
    monthly_online_quick = monthly_online_blitz = live_online_quick = live_online_blitz = 0
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
        elif rs == "OQ" and val:
            monthly_online_quick = val
        elif rs == "OB" and val:
            monthly_online_blitz = val
    for section in sections_data.get("items", []):
        for record in section.get("ratingRecords", []):
            src, val = record.get("ratingSource"), record.get("postRating", 0)
            if src == "Q" and not live_quick:
                live_quick = val
            elif src == "B" and not live_blitz:
                live_blitz = val
            elif src == "OQ" and not live_online_quick:
                live_online_quick = val
            elif src == "OB" and not live_online_blitz:
                live_online_blitz = val
    if not live_quick:
        live_quick = monthly_quick
    if not live_blitz:
        live_blitz = monthly_blitz
    if not live_online_quick:
        live_online_quick = monthly_online_quick
    if not live_online_blitz:
        live_online_blitz = monthly_online_blitz

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
        "live_online_quick": live_online_quick, "live_online_blitz": live_online_blitz,
        "fide_rapid": fide_rapid, "fide_blitz": fide_blitz,
    })


# ---------------------------------------------------------------------------
# Player research (preview feature -- not yet billed; see mychesspairings
# issue for wiring up a per-lookup Stripe charge once this report is
# validated with real users)
# ---------------------------------------------------------------------------

async def _fetch_lichess_games_pgn(username: str, max_games: int = 40) -> list:
    """Recent public games for a Lichess username, each tagged with which
    color `username` played so the caller never has to re-derive it."""
    username = username.strip()
    if not username:
        return []
    headers = {
        "User-Agent": "Mozilla/5.0 (compatible; MyChessRating/1.0)",
        "Accept": "application/x-chess-pgn",
    }
    try:
        async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
            r = await client.get(
                f"https://lichess.org/api/games/user/{username}",
                params={"max": max_games, "opening": "true"},
                headers=headers,
            )
        if r.status_code != 200:
            return []
        games = []
        for pgn in ph.split_pgn(r.text):
            h = ph.parse_pgn_headers(pgn)
            white, black = h.get("White", ""), h.get("Black", "")
            if white.lower() == username.lower():
                color = "white"
            elif black.lower() == username.lower():
                color = "black"
            else:
                continue
            games.append({
                "color": color, "result": h.get("Result", ""),
                "eco": h.get("ECO", ""), "opening": h.get("Opening", ""),
                "date": h.get("UTCDate") or h.get("Date", ""),
            })
        return games
    except Exception:
        return []


async def _fetch_chesscom_games_pgn(username: str, months: int = 2, max_games: int = 40) -> list:
    """Recent public games for a Chess.com username (last `months` monthly
    archives), each tagged with which color `username` played."""
    username = username.strip()
    if not username:
        return []
    headers = {"User-Agent": "Mozilla/5.0 (compatible; MyChessRating/1.0)"}
    try:
        async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
            r = await client.get(f"https://api.chess.com/pub/player/{username}/games/archives", headers=headers)
            if r.status_code != 200:
                return []
            archives = r.json().get("archives", [])[-months:]
            responses = await asyncio.gather(*(client.get(u, headers=headers) for u in archives))
        games = []
        for resp in responses:
            if resp.status_code != 200:
                continue
            for g in resp.json().get("games", []):
                pgn = g.get("pgn", "")
                if not pgn:
                    continue
                h = ph.parse_pgn_headers(pgn)
                white, black = h.get("White", ""), h.get("Black", "")
                if white.lower() == username.lower():
                    color = "white"
                elif black.lower() == username.lower():
                    color = "black"
                else:
                    continue
                opening = h.get("Opening", "")
                if not opening and h.get("ECOUrl"):
                    opening = h["ECOUrl"].rstrip("/").rsplit("/", 1)[-1].replace("-", " ")
                games.append({
                    "color": color, "result": h.get("Result", ""),
                    "eco": h.get("ECO", ""), "opening": opening,
                    "date": h.get("UTCDate") or h.get("Date", ""),
                })
        games.sort(key=lambda g: g.get("date", ""), reverse=True)
        return games[:max_games]
    except Exception:
        return []


def _summarize_research_side(entries: list) -> dict:
    total = len(entries)
    if not total:
        return {"total": 0, "score_pct": None, "openings": []}
    score_sum = sum(e["score"] for e in entries)
    by_opening = {}
    for e in entries:
        d = by_opening.setdefault(e["opening"], {"count": 0, "score": 0.0})
        d["count"] += 1
        d["score"] += e["score"]
    openings = sorted(
        ({"name": name, "count": d["count"], "score_pct": round(100 * d["score"] / d["count"])}
         for name, d in by_opening.items()),
        key=lambda o: (-o["count"], o["name"])
    )[:5]
    return {"total": total, "score_pct": round(100 * score_sum / total), "openings": openings}


def _aggregate_player_research(display_name: str, games: list) -> dict:
    """Build a scouting report from pre-colored games (each already tagged
    'color': 'white'/'black' for the player being researched)."""
    sides = {"white": [], "black": []}
    for g in games:
        color = g.get("color")
        if color not in ("white", "black"):
            continue
        result = g.get("result", "")
        if result == "1-0":
            score = 1.0 if color == "white" else 0.0
        elif result == "0-1":
            score = 0.0 if color == "white" else 1.0
        elif result in ("1/2-1/2", "0.5-0.5"):
            score = 0.5
        else:
            continue
        opening = (g.get("opening") or "").strip() or (f"ECO {g['eco']}" if g.get("eco") else "Unspecified opening")
        sides[color].append({"opening": opening, "score": score})

    white = _summarize_research_side(sides["white"])
    black = _summarize_research_side(sides["black"])

    tips = []
    if white["openings"]:
        top = white["openings"][0]
        tips.append(
            f"As White, {display_name} plays {top['name']} most often "
            f"({top['count']} game{'s' if top['count'] != 1 else ''} seen, scoring {top['score_pct']}%) "
            f"— prepare a concrete response to this before the game."
        )
        if len(white["openings"]) > 1:
            weak = min(white["openings"], key=lambda o: o["score_pct"])
            if weak["name"] != top["name"] and weak["score_pct"] < 50:
                tips.append(
                    f"They've scored only {weak['score_pct']}% with {weak['name']} as White — "
                    f"steering into that line may favor you if you get the choice."
                )
    if black["openings"]:
        top = black["openings"][0]
        tips.append(
            f"As Black, {display_name} most often answers with {top['name']} "
            f"({top['count']} game{'s' if top['count'] != 1 else ''} seen, scoring {top['score_pct']}%)."
        )
        if len(black["openings"]) > 1:
            weak = min(black["openings"], key=lambda o: o["score_pct"])
            if weak["name"] != top["name"] and weak["score_pct"] < 50:
                tips.append(
                    f"They've scored only {weak['score_pct']}% against {weak['name']} as Black — "
                    f"that's a reasonable opening to head for if you're playing White against them."
                )
    if not tips:
        tips.append("Not enough game data was found to spot a pattern yet.")

    return {"white": white, "black": black, "tips": tips, "total_games": white["total"] + black["total"]}


@app.get("/api/player-research", response_class=HTMLResponse)
async def player_research(
    request: Request,
    name: str = "",
    uscf_id: str = "",
    fide_id: str = "",
    lichess_username: str = "",
    chesscom_username: str = "",
    _user: dict = Depends(require_login),
):
    display_name = name.strip() or "This player"
    uscf_id = uscf_id.strip()
    lichess_username = lichess_username.strip()
    chesscom_username = chesscom_username.strip()
    fide_id = fide_id.strip()

    # No explicit username typed in -- fall back to a crowd-confirmed one, if any.
    if uscf_id:
        if not lichess_username:
            confirmed = await asyncio.get_event_loop().run_in_executor(
                None, get_confirmed_online_id, uscf_id, "lichess")
            lichess_username = confirmed or ""
        if not chesscom_username:
            confirmed = await asyncio.get_event_loop().run_in_executor(
                None, get_confirmed_online_id, uscf_id, "chesscom")
            chesscom_username = confirmed or ""

    # Games already found and linked to this player by "Find all games".
    linked = await asyncio.get_event_loop().run_in_executor(
        None, list_player_games, _player_key(uscf_id, fide_id, display_name))
    linked_games = [{"color": g["color"], "result": g["result"], "eco": g["eco"],
                     "opening": g["opening"], "date": g["date"]} for g in linked]

    if not lichess_username and not chesscom_username and not fide_id and not linked_games:
        return HTMLResponse(
            '<div class="alert alert-secondary small mb-0">'
            'No confirmed Lichess/Chess.com username yet for this player. '
            'Report one below, enter a username above and click Research, or click "Find all games".</div>'
        )

    lichess_games, chesscom_games, db_games = await asyncio.gather(
        _fetch_lichess_games_pgn(lichess_username),
        _fetch_chesscom_games_pgn(chesscom_username),
        asyncio.get_event_loop().run_in_executor(None, get_pgn_games_by_fide_id, fide_id) if fide_id else _immediate([]),
    )

    report = _aggregate_player_research(display_name, lichess_games + chesscom_games + db_games + linked_games)

    return templates.TemplateResponse(request=request, name="fragments/player_research.html", context={
        "display_name": display_name,
        "report": report,
        "used_lichess": bool(lichess_username),
        "used_chesscom": bool(chesscom_username),
        "used_db": bool(fide_id and db_games),
        "used_archive": len(linked_games),
        "lichess_username": lichess_username,
        "chesscom_username": chesscom_username,
    })


async def _immediate(value):
    return value


# ---------------------------------------------------------------------------
# Find all games for a player: local archive, Lichess/Chess.com, broadcast
# search, uploads -- deduped into pgn_games and linked per player.
# ---------------------------------------------------------------------------

_NAME_TITLES = re.compile(r"\b(GM|IM|FM|WGM|WIM|WFM|CM|WCM|NM|Dr|Mr|Mrs|Ms)\.?\b", re.I)


def _player_key(uscf_id: str, fide_id: str, name: str) -> str:
    if (uscf_id or "").strip():
        return f"uscf:{uscf_id.strip()}"
    if (fide_id or "").strip():
        return f"fide:{fide_id.strip()}"
    return "name:" + re.sub(r"[^a-z]", "", (name or "").lower())


def _name_parts(name: str) -> tuple:
    """(surname, first names) lowercased, from 'Last, First' or 'First Last'."""
    n = _NAME_TITLES.sub(" ", name or "")
    n = re.sub(r"[^\w,\s'-]", " ", n).strip()
    if "," in n:
        last, first = (p.strip() for p in n.split(",", 1))
    else:
        parts = n.split()
        if not parts:
            return "", ""
        last, first = parts[-1], " ".join(parts[:-1])
    return last.lower(), first.lower()


def _name_matches(player_name: str, other: str) -> bool:
    pl, pf = _name_parts(player_name)
    ol, of = _name_parts(other)
    if not pl or pl != ol:
        return False
    if not pf or not of:
        return True
    a, b = pf.split()[0], of.split()[0]
    return a == b or a.startswith(b) or b.startswith(a)


def _matcher_by_name(player_name: str):
    def match(white: str, black: str):
        if _name_matches(player_name, white):
            return "white"
        if _name_matches(player_name, black):
            return "black"
        return None
    return match


def _matcher_by_username(username: str):
    u = username.strip().lower()

    def match(white: str, black: str):
        if white.strip().lower() == u:
            return "white"
        if black.strip().lower() == u:
            return "black"
        return None
    return match


def _store_pgn_text(raw: str, source_id: int) -> list:
    """Insert every game in `raw` (duplicates ignored); returns [(game_id, white, black)]."""
    dicts = [d for d in (ph.pgn_to_game_dict(p, source_id) for p in ph.split_pgn(raw or "")) if d]
    if not dicts:
        return []
    insert_pgn_games(dicts)
    ids = get_pgn_game_ids_by_hashes([d["game_hash"] for d in dicts])
    return [(ids[d["game_hash"]], d["white"], d["black"]) for d in dicts if d["game_hash"] in ids]


def _store_and_link(raw: str, source_id: int, player_key: str, matcher, status: str, reason: str,
                    created_by: Optional[int]) -> dict:
    stored = _store_pgn_text(raw, source_id)
    links = []
    for gid, white, black in stored:
        color = matcher(white, black)
        if color:
            links.append((gid, color, status, reason))
    new = link_player_games(player_key, links, created_by)
    update_pgn_source_fetched(source_id, len(stored))
    return {"stored": len(stored), "matched": len(links), "new": new}


def _guess_usernames(name: str) -> list:
    last, first = _name_parts(name)
    if not last or not first:
        return []
    f = re.sub(r"[^a-z0-9]", "", first.split()[0])
    l = re.sub(r"[^a-z0-9]", "", last)
    if not f or not l:
        return []
    cands = [f + l, f + "_" + l, f + "." + l, l + f, f[0] + l, f + l[0], l + "_" + f, f + "-" + l]
    seen, out = set(), []
    for c in cands:
        if len(c) >= 3 and c not in seen:
            seen.add(c)
            out.append(c)
    return out


async def _suggest_online_ids(name: str) -> dict:
    """Guess Lichess/Chess.com usernames from the name; keep only accounts whose
    public profile name matches, so the user has something concrete to confirm."""
    cands = _guess_usernames(name)
    if not cands:
        return {"lichess": [], "chesscom": []}
    lichess_users, chesscom_profiles = await asyncio.gather(
        ph.lichess_users_by_ids(cands),
        asyncio.gather(*(ph.chesscom_profile(c) for c in cands)),
    )
    lichess = []
    for u in lichess_users or []:
        prof = u.get("profile") or {}
        real = " ".join(x for x in (prof.get("firstName"), prof.get("lastName")) if x) or prof.get("realName", "")
        if real and _name_matches(name, real):
            lichess.append(u.get("username") or u.get("id"))
    chesscom = [p.get("username") for p in chesscom_profiles if p.get("name") and _name_matches(name, p["name"])]
    return {"lichess": lichess, "chesscom": [c for c in chesscom if c]}


async def _import_broadcasts_by_name(name: str, max_tours: int = 4, max_rounds: int = 12) -> dict:
    last, _ = _name_parts(name)
    if not last:
        return {"tours": 0, "rounds": 0, "stored": 0}
    run = asyncio.get_event_loop().run_in_executor
    tours = await ph.lichess_search_broadcasts(name if "," not in name else last)
    tours = [t for t in tours if last in ((t.get("info") or {}).get("players") or "").lower()
             or last in (t.get("name") or "").lower()][:max_tours]
    rounds_done = stored = 0
    for tour in tours:
        rounds = (await ph.lichess_broadcast_all_rounds(tour.get("id", ""))) or []
        for idx, rnd in enumerate(rounds[:max_rounds], start=1):
            rid = rnd.get("id", "")
            if not rid or await run(None, lichess_round_already_known, rid):
                continue
            try:
                raw = await ph.lichess_round_pgn(rid, rnd.get("url", ""))
            except Exception as e:
                logging.warning("broadcast search round %s: %s", rid, e)
                continue
            if not raw:
                continue
            sid = await run(None, upsert_pgn_source, "lichess", rnd.get("url", "") or f"https://lichess.org/broadcast/-/-/{rid}",
                            tour.get("name", ""), idx, rnd.get("name", ""), tour.get("id", ""), rid, False, None, True)
            games = await run(None, _store_pgn_text, raw, sid)
            await run(None, update_pgn_source_fetched, sid, len(games))
            stored += len(games)
            rounds_done += 1
    return {"tours": len(tours), "rounds": rounds_done, "stored": stored}


async def _find_player_games(player_key: str, name: str, fide_id: str, lichess_username: str,
                             chesscom_username: str, user_id: int) -> dict:
    run = asyncio.get_event_loop().run_in_executor
    sources = []

    lichess_raw, chesscom_raw, broadcast, suggestions = await asyncio.gather(
        ph.lichess_user_games_pgn(lichess_username) if lichess_username else _immediate(""),
        ph.chesscom_user_games_pgn(chesscom_username) if chesscom_username else _immediate(""),
        _import_broadcasts_by_name(name),
        _suggest_online_ids(name) if not (lichess_username and chesscom_username) else _immediate({"lichess": [], "chesscom": []}),
        return_exceptions=True,
    )
    if isinstance(lichess_raw, Exception):
        logging.warning("find games lichess: %s", lichess_raw); lichess_raw = ""
    if isinstance(chesscom_raw, Exception):
        logging.warning("find games chesscom: %s", chesscom_raw); chesscom_raw = ""
    if isinstance(broadcast, Exception):
        logging.warning("find games broadcasts: %s", broadcast); broadcast = {"tours": 0, "rounds": 0, "stored": 0}
    if isinstance(suggestions, Exception):
        logging.warning("find games suggestions: %s", suggestions); suggestions = {"lichess": [], "chesscom": []}

    if lichess_username:
        sid = await run(None, ensure_pgn_source, "lichess_user", f"https://lichess.org/@/{lichess_username}",
                        f"Lichess games of {lichess_username}")
        r = await run(None, _store_and_link, lichess_raw, sid, player_key, _matcher_by_username(lichess_username),
                      "confirmed", f"lichess:{lichess_username}", user_id)
        sources.append({"name": f"Lichess ({lichess_username})", "found": r["matched"], "new": r["new"]})
    if chesscom_username:
        sid = await run(None, ensure_pgn_source, "chesscom_user", f"https://www.chess.com/member/{chesscom_username}",
                        f"Chess.com games of {chesscom_username}")
        r = await run(None, _store_and_link, chesscom_raw, sid, player_key, _matcher_by_username(chesscom_username),
                      "confirmed", f"chesscom:{chesscom_username}", user_id)
        sources.append({"name": f"Chess.com ({chesscom_username})", "found": r["matched"], "new": r["new"]})
    sources.append({"name": "Lichess broadcasts", "found": broadcast["stored"],
                    "new": None, "detail": f"{broadcast['tours']} event(s), {broadcast['rounds']} new round(s) imported"})

    # Name pass over everything now in the archive (TWIC, broadcasts, uploads, ...)
    last, _ = _name_parts(name)
    matcher = _matcher_by_name(name)
    archive_links = []
    if last:
        for row in await run(None, find_pgn_games_by_surname, last):
            color = matcher(row["white"], row["black"])
            if color:
                archive_links.append((row["id"], color, "probable", "name"))
    if fide_id:
        archive_links += [(gid, color, "confirmed", "fide_id") for gid, color in
                          await run(None, find_pgn_games_by_fide_id, fide_id)]
    new = await run(None, link_player_games, player_key, archive_links, user_id)
    sources.insert(0, {"name": "Games archive", "found": len(archive_links), "new": new})

    return {"sources": sources, "suggestions": suggestions}


def _can_review_games(user: dict) -> bool:
    return user.get("role") in ("coach", "admin")


def _render_player_games(request: Request, name: str, uscf_id: str, fide_id: str, user: dict, summary=None):
    key = _player_key(uscf_id, fide_id, name)
    return templates.TemplateResponse(request=request, name="fragments/player_games.html", context={
        "name": name, "uscf_id": uscf_id, "fide_id": fide_id, "player_key": key,
        "games": list_player_games(key, include_rejected=True),
        "counts": count_player_games(key),
        "summary": summary,
        "can_review": _can_review_games(user),
    })


async def _resolve_online_usernames(uscf_id: str, lichess_username: str, chesscom_username: str) -> tuple:
    run = asyncio.get_event_loop().run_in_executor
    if uscf_id and not lichess_username:
        lichess_username = (await run(None, get_confirmed_online_id, uscf_id, "lichess")) or ""
    if uscf_id and not chesscom_username:
        chesscom_username = (await run(None, get_confirmed_online_id, uscf_id, "chesscom")) or ""
    return lichess_username.strip(), chesscom_username.strip()


@app.get("/api/player-games", response_class=HTMLResponse)
def player_games_list(request: Request, name: str = "", uscf_id: str = "", fide_id: str = "",
                      user: dict = Depends(require_login)):
    return _render_player_games(request, name.strip(), uscf_id.strip(), fide_id.strip(), user)


@app.post("/api/player-games/find", response_class=HTMLResponse)
async def player_games_find(request: Request, name: str = Form(""), uscf_id: str = Form(""), fide_id: str = Form(""),
                            lichess_username: str = Form(""), chesscom_username: str = Form(""),
                            user: dict = Depends(require_login)):
    name, uscf_id, fide_id = name.strip(), uscf_id.strip(), fide_id.strip()
    if not name and not uscf_id:
        raise HTTPException(status_code=400, detail="name or uscf_id required")
    lichess_username, chesscom_username = await _resolve_online_usernames(uscf_id, lichess_username, chesscom_username)
    key = _player_key(uscf_id, fide_id, name)
    summary = await _find_player_games(key, name, fide_id, lichess_username, chesscom_username, user["id"])
    return _render_player_games(request, name, uscf_id, fide_id, user, summary)


@app.post("/api/player-games/{link_id}/status", response_class=HTMLResponse)
def player_games_set_status(request: Request, link_id: int, status: str = Form(...), name: str = Form(""),
                            uscf_id: str = Form(""), fide_id: str = Form(""), user: dict = Depends(require_login)):
    if not _can_review_games(user):
        raise HTTPException(status_code=403, detail="Coach or admin role required")
    link = get_player_game_link(link_id)
    if not link or link["player_key"] != _player_key(uscf_id.strip(), fide_id.strip(), name.strip()):
        raise HTTPException(404)
    set_player_game_link_status(link_id, status)
    return _render_player_games(request, name.strip(), uscf_id.strip(), fide_id.strip(), user)


@app.post("/api/player-games/upload", response_class=HTMLResponse)
async def player_games_upload(request: Request, pgn_file: UploadFile = File(...), name: str = Form(""),
                              uscf_id: str = Form(""), fide_id: str = Form(""), user: dict = Depends(require_login)):
    name, uscf_id, fide_id = name.strip(), uscf_id.strip(), fide_id.strip()
    data = await pgn_file.read()
    if not data or len(data) > 5 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="PGN must be between 1 byte and 5 MB")
    raw = data.decode("utf-8", errors="replace")
    key = _player_key(uscf_id, fide_id, name)
    run = asyncio.get_event_loop().run_in_executor
    sid = await run(None, ensure_pgn_source, "upload", f"upload:{user['id']}:{uuid.uuid4().hex}",
                    f"Uploaded by {user['username']} for {name or uscf_id}")
    result = await run(None, _store_and_link, raw, sid, key, _matcher_by_name(name), "probable", "upload", user["id"])
    summary = {"sources": [{"name": "Your upload", "found": result["matched"], "new": result["new"],
                            "detail": f"{result['stored']} game(s) in file"}],
               "suggestions": {"lichess": [], "chesscom": []}}
    return _render_player_games(request, name, uscf_id, fide_id, user, summary)


@app.get("/api/player-games/download")
def player_games_download(name: str = "", uscf_id: str = "", fide_id: str = "", user: dict = Depends(require_login)):
    key = _player_key(uscf_id.strip(), fide_id.strip(), name.strip())
    pgn = download_player_games_pgn(key)
    if not pgn:
        raise HTTPException(404, "No games found for this player yet")
    slug = re.sub(r"[^A-Za-z0-9]+", "_", name.strip() or uscf_id.strip() or "player").strip("_") or "player"
    return Response(content=pgn, media_type="application/x-chess-pgn",
                    headers={"Content-Disposition": f'attachment; filename="{slug}_games.pgn"'})


@app.get("/api/player-games/game/{gid}")
def player_games_game_raw(gid: int, user: dict = Depends(require_login)):
    raw = get_pgn_game_raw(gid)
    if raw is None:
        raise HTTPException(404, "Game not found")
    return Response(content=raw, media_type="text/plain")


# ---------------------------------------------------------------------------
# TWIC (The Week in Chess) weekly archive import -- feeds the games archive
# ---------------------------------------------------------------------------

_twic_state = {"running": False, "message": ""}
_TWIC_MAX_ISSUES_PER_RUN = 60


async def _twic_import_issue(n: int) -> int:
    raw = await ph.twic_issue_pgn(n)
    if not raw:
        return 0
    run = asyncio.get_event_loop().run_in_executor
    sid = await run(None, ensure_pgn_source, "twic", ph.TWIC_ZIP_URL.format(n=n), f"TWIC {n}")
    games = await run(None, _store_pgn_text, raw, sid)
    await run(None, update_pgn_source_fetched, sid, len(games))
    last = int(get_setting("twic_last_issue", "0") or 0)
    if n > last:
        set_setting("twic_last_issue", str(n))
    return len(games)


async def _twic_import_range(start: int, end: int):
    _twic_state["running"] = True
    total = 0
    try:
        for n in range(start, end + 1):
            _twic_state["message"] = f"Importing TWIC {n} ({n - start + 1}/{end - start + 1})…"
            try:
                total += await _twic_import_issue(n)
            except Exception as e:
                logging.warning("TWIC %s import: %s", n, e)
        _twic_state["message"] = f"Done: TWIC {start}–{end}, {total} games stored."
    finally:
        _twic_state["running"] = False


async def _twic_auto_check():
    if get_setting("twic_auto", "0") != "1" or _twic_state["running"]:
        return
    latest = await ph.twic_latest_issue()
    last = int(get_setting("twic_last_issue", "0") or 0)
    if latest and latest > last:
        await _twic_import_range(max(last + 1, latest - 3), latest)


@app.post("/admin/twic/import")
async def admin_twic_import(from_issue: int = Form(...), to_issue: int = Form(0), _user: dict = Depends(require_admin)):
    if _twic_state["running"]:
        return RedirectResponse("/pgn-database?import_err=TWIC+import+already+running", status_code=303)
    if to_issue <= 0:
        to_issue = await ph.twic_latest_issue() or from_issue
    from_issue = max(1, from_issue)
    to_issue = max(from_issue, min(to_issue, from_issue + _TWIC_MAX_ISSUES_PER_RUN - 1))
    asyncio.create_task(_twic_import_range(from_issue, to_issue))
    return RedirectResponse(f"/pgn-database?import_msg=TWIC+import+{from_issue}-{to_issue}+started", status_code=303)


@app.post("/admin/twic/auto")
def admin_twic_auto(enabled: str = Form("0"), _user: dict = Depends(require_admin)):
    set_setting("twic_auto", "1" if enabled == "1" else "0")
    return RedirectResponse("/pgn-database", status_code=303)


@app.get("/api/player-online-ids", response_class=HTMLResponse)
def player_online_ids(request: Request, uscf_id: str = "", user: dict = Depends(require_login)):
    uscf_id = uscf_id.strip()
    entries = list_online_ids(uscf_id) if uscf_id else []
    return templates.TemplateResponse(request=request, name="fragments/player_online_ids.html", context={
        "uscf_id": uscf_id, "entries": entries, "is_admin": user.get("role") == "admin",
    })


@app.post("/api/player-online-ids", response_class=HTMLResponse)
def report_player_online_id(
    request: Request,
    uscf_id: str = Form(...),
    platform: str = Form(...),
    username: str = Form(...),
    user: dict = Depends(require_login),
):
    report_online_id(uscf_id, platform, username, user["id"])
    entries = list_online_ids(uscf_id)
    return templates.TemplateResponse(request=request, name="fragments/player_online_ids.html", context={
        "uscf_id": uscf_id, "entries": entries, "is_admin": user.get("role") == "admin",
    })


@app.post("/api/player-online-ids/{oid}/confirm", response_class=HTMLResponse)
def confirm_player_online_id(request: Request, oid: int, uscf_id: str = Form(...), user: dict = Depends(require_admin)):
    set_online_id_status(oid, "confirmed")
    entries = list_online_ids(uscf_id)
    return templates.TemplateResponse(request=request, name="fragments/player_online_ids.html", context={
        "uscf_id": uscf_id, "entries": entries, "is_admin": True,
    })


@app.post("/api/player-online-ids/{oid}/unconfirm", response_class=HTMLResponse)
def unconfirm_player_online_id(request: Request, oid: int, uscf_id: str = Form(...), user: dict = Depends(require_admin)):
    set_online_id_status(oid, "reported")
    entries = list_online_ids(uscf_id)
    return templates.TemplateResponse(request=request, name="fragments/player_online_ids.html", context={
        "uscf_id": uscf_id, "entries": entries, "is_admin": True,
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
def rating_impact_api(
    request: Request,
    opp_uscf: Optional[int] = None,
    opp_uscf_quick: Optional[int] = None,
    opp_uscf_blitz: Optional[int] = None,
    opp_uscf_online: Optional[int] = None,
    opp_uscf_online_quick: Optional[int] = None,
    opp_uscf_online_blitz: Optional[int] = None,
    opp_fide: Optional[int] = None,
    opp_fide_rapid: Optional[int] = None,
    opp_fide_blitz: Optional[int] = None,
    opp_name: Optional[str] = None,
):
    user = get_current_user(request)
    profile = get_user_profile(user["id"]) if user else None
    my_uscf              = profile.get("uscf_rating")              if profile else None
    my_uscf_quick        = profile.get("uscf_quick_rating")        if profile else None
    my_uscf_blitz        = profile.get("uscf_blitz_rating")        if profile else None
    my_uscf_online       = profile.get("uscf_online_rating")       if profile else None
    my_uscf_online_quick = profile.get("uscf_online_quick_rating") if profile else None
    my_uscf_online_blitz = profile.get("uscf_online_blitz_rating") if profile else None
    my_fide       = profile.get("fide_rating")       if profile else None
    my_fide_rapid = profile.get("fide_rapid_rating") if profile else None
    my_fide_blitz = profile.get("fide_blitz_rating") if profile else None

    uscf_impact              = _elo_impact(my_uscf,              opp_uscf,              _uscf_k(my_uscf))              if my_uscf and opp_uscf                           else None
    uscf_quick_impact        = _elo_impact(my_uscf_quick,        opp_uscf_quick,        _uscf_k(my_uscf_quick))        if my_uscf_quick and opp_uscf_quick               else None
    uscf_blitz_impact        = _elo_impact(my_uscf_blitz,        opp_uscf_blitz,        _uscf_k(my_uscf_blitz))        if my_uscf_blitz and opp_uscf_blitz               else None
    uscf_online_impact       = _elo_impact(my_uscf_online,       opp_uscf_online,       _uscf_k(my_uscf_online))       if my_uscf_online and opp_uscf_online             else None
    uscf_online_quick_impact = _elo_impact(my_uscf_online_quick, opp_uscf_online_quick, _uscf_k(my_uscf_online_quick)) if my_uscf_online_quick and opp_uscf_online_quick else None
    uscf_online_blitz_impact = _elo_impact(my_uscf_online_blitz, opp_uscf_online_blitz, _uscf_k(my_uscf_online_blitz)) if my_uscf_online_blitz and opp_uscf_online_blitz else None
    fide_impact       = _elo_impact(my_fide,       opp_fide,       _fide_k(my_fide))       if my_fide and opp_fide             else None
    fide_rapid_impact = _elo_impact(my_fide_rapid, opp_fide_rapid, _fide_k(my_fide_rapid)) if my_fide_rapid and opp_fide_rapid else None
    fide_blitz_impact = _elo_impact(my_fide_blitz, opp_fide_blitz, _fide_k(my_fide_blitz)) if my_fide_blitz and opp_fide_blitz else None

    has_any = bool(profile and any([
        my_uscf, my_uscf_quick, my_uscf_blitz, my_uscf_online, my_uscf_online_quick, my_uscf_online_blitz,
        my_fide, my_fide_rapid, my_fide_blitz,
    ]))
    my_name = (profile.get("player_name") or profile.get("username") or "You") if profile else "You"

    return templates.TemplateResponse(
        request=request,
        name="fragments/rating_impact.html",
        context={
            "my_name": my_name, "opp_name": opp_name,
            "my_uscf": my_uscf, "my_uscf_quick": my_uscf_quick, "my_uscf_blitz": my_uscf_blitz,
            "my_uscf_online": my_uscf_online, "my_uscf_online_quick": my_uscf_online_quick, "my_uscf_online_blitz": my_uscf_online_blitz,
            "my_fide": my_fide, "my_fide_rapid": my_fide_rapid, "my_fide_blitz": my_fide_blitz,
            "opp_uscf": opp_uscf, "opp_uscf_quick": opp_uscf_quick, "opp_uscf_blitz": opp_uscf_blitz,
            "opp_uscf_online": opp_uscf_online, "opp_uscf_online_quick": opp_uscf_online_quick, "opp_uscf_online_blitz": opp_uscf_online_blitz,
            "opp_fide": opp_fide, "opp_fide_rapid": opp_fide_rapid, "opp_fide_blitz": opp_fide_blitz,
            "uscf_impact": uscf_impact, "uscf_quick_impact": uscf_quick_impact, "uscf_blitz_impact": uscf_blitz_impact,
            "uscf_online_impact": uscf_online_impact, "uscf_online_quick_impact": uscf_online_quick_impact, "uscf_online_blitz_impact": uscf_online_blitz_impact,
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
            monthly_online_regular = live_online_regular = 0
            monthly_online_quick = monthly_online_blitz = live_online_quick = live_online_blitz = 0
            if member_r.status_code == 200:
                for entry in member_r.json().get("ratings", []):
                    rs = entry.get("ratingSystem")
                    val = entry.get("rating", 0)
                    if rs == "Q" and val:
                        monthly_quick = val
                    elif rs == "B" and val:
                        monthly_blitz = val
                    elif rs == "OR" and val:
                        monthly_online_regular = val
                    elif rs == "OQ" and val:
                        monthly_online_quick = val
                    elif rs == "OB" and val:
                        monthly_online_blitz = val
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
                        elif src == "OR" and not live_online_regular:
                            live_online_regular = val
                        elif src == "OQ" and not live_online_quick:
                            live_online_quick = val
                        elif src == "OB" and not live_online_blitz:
                            live_online_blitz = val
            new_uscf = live_regular or profile.get("uscf_rating")
            new_quick = (live_quick or monthly_quick) or profile.get("uscf_quick_rating")
            new_blitz = (live_blitz or monthly_blitz) or profile.get("uscf_blitz_rating")
            new_online = (live_online_regular or monthly_online_regular) or profile.get("uscf_online_rating")
            new_online_quick = (live_online_quick or monthly_online_quick) or profile.get("uscf_online_quick_rating")
            new_online_blitz = (live_online_blitz or monthly_online_blitz) or profile.get("uscf_online_blitz_rating")
            if any([new_uscf, new_quick, new_blitz, new_online, new_online_quick, new_online_blitz]):
                update_user_profile(
                    user["id"],
                    profile.get("uscf_id"), profile.get("fide_id"),
                    new_uscf, profile.get("fide_rating"),
                    new_quick, new_blitz,
                    profile.get("fide_rapid_rating"), profile.get("fide_blitz_rating"),
                    profile.get("player_name"),
                    new_online, new_online_quick, new_online_blitz,
                )
                profile = get_user_profile(user["id"])
                uscf_refreshed = True
        except Exception:
            pass
    location = get_user_location(user["id"])
    return templates.TemplateResponse(request=request, name="profile.html", context={
        "profile": profile,
        "saved": saved == "1",
        "uscf_refreshed": uscf_refreshed,
        "location": location,
        "user_federation": get_federation(location["federation_id"]) if location.get("federation_id") else None,
        "countries": list_countries(),
        "federations": list_federations(active_only=True),
    })


@app.post("/profile/location")
def update_location(
    country_code: str = Form(""),
    city: str = Form(""),
    federation_id: str = Form(""),
    user: dict = Depends(require_login),
):
    """Change the country / federation / city that scope the home page's local tournaments."""
    country_code = country_code.strip().upper()[:3]
    fed_id = int(federation_id) if federation_id.strip().isdigit() else None
    if fed_id is not None:
        fed = get_federation(fed_id)
        if not fed or (country_code and fed.get("country_code") != country_code):
            fed_id = None
    if fed_id is None and country_code:
        feds = get_federations_for_country(country_code)
        if len(feds) == 1:
            fed_id = feds[0]["id"]
    update_user_location(user["id"], country_code, city.strip()[:80], fed_id)
    return RedirectResponse("/profile?saved=1", status_code=303)


@app.post("/profile/contact")
def update_contact(
    email: Optional[str] = Form(None),
    phone: Optional[str] = Form(None),
    user: dict = Depends(require_login),
):
    update_user_contact(user["id"],
                        email.strip() if email else None,
                        phone.strip() if phone else None)
    return RedirectResponse("/profile?saved=1", status_code=303)


@app.post("/profile")
def update_profile(
    player_name: Optional[str] = Form(None),
    uscf_id: Optional[str] = Form(None),
    fide_id: Optional[str] = Form(None),
    uscf_rating: Optional[int] = Form(None),
    fide_rating: Optional[int] = Form(None),
    uscf_quick_rating: Optional[int] = Form(None),
    uscf_blitz_rating: Optional[int] = Form(None),
    fide_rapid_rating: Optional[int] = Form(None),
    fide_blitz_rating: Optional[int] = Form(None),
    uscf_online_rating: Optional[int] = Form(None),
    uscf_online_quick_rating: Optional[int] = Form(None),
    uscf_online_blitz_rating: Optional[int] = Form(None),
    user: dict = Depends(require_login),
):
    update_user_profile(
        user["id"],
        uscf_id.strip() if uscf_id else None,
        fide_id.strip() if fide_id else None,
        uscf_rating, fide_rating,
        uscf_quick_rating, uscf_blitz_rating, fide_rapid_rating, fide_blitz_rating,
        player_name.strip() if player_name else None,
        uscf_online_rating, uscf_online_quick_rating, uscf_online_blitz_rating,
    )
    return RedirectResponse("/profile?saved=1", status_code=303)


@app.post("/profile/populate", response_class=HTMLResponse)
def profile_populate(
    player_name: Optional[str] = Form(None),
    uscf_id: Optional[str] = Form(None),
    fide_id: Optional[str] = Form(None),
    uscf_rating: Optional[str] = Form(None),
    fide_rating: Optional[str] = Form(None),
    uscf_quick_rating: Optional[str] = Form(None),
    uscf_blitz_rating: Optional[str] = Form(None),
    fide_rapid_rating: Optional[str] = Form(None),
    fide_blitz_rating: Optional[str] = Form(None),
    uscf_online_rating: Optional[str] = Form(None),
    uscf_online_quick_rating: Optional[str] = Form(None),
    uscf_online_blitz_rating: Optional[str] = Form(None),
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
        _int(uscf_online_rating), _int(uscf_online_quick_rating), _int(uscf_online_blitz_rating),
    )
    return HTMLResponse('<span class="text-success fw-semibold">&#10003; Profile updated</span>')


# ---------------------------------------------------------------------------
# USCF Tournament Rating Calculator
# ---------------------------------------------------------------------------

@app.get("/uscf-calculator", response_class=HTMLResponse)
def uscf_calculator_page(request: Request, user: dict = Depends(require_login)):
    profile = get_user_profile(user["id"])
    saved = list_user_tournaments(user["id"])
    return templates.TemplateResponse(request=request, name="uscf_calculator.html",
                                      context={"profile": profile, "saved_tournaments": saved,
                                               **_user_tournament_context(user["id"])})

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
def api_delete_tournament(tid: int, user: dict = Depends(require_login)):
    ok = delete_user_tournament(tid, user["id"])
    if not ok:
        return JSONResponse({"error": "Not found"}, status_code=404)
    return {"deleted": tid}


@app.get("/api/uscf-tournaments/deleted")
def api_list_deleted_tournaments(user: dict = Depends(require_login)):
    return list_deleted_user_tournaments(user["id"])


@app.post("/api/uscf-tournaments/{tid}/undelete")
def api_undelete_tournament(tid: int, user: dict = Depends(require_login)):
    ok = undelete_user_tournament(tid, user["id"])
    if not ok:
        return JSONResponse({"error": "Not found"}, status_code=404)
    return {"restored": tid}


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
def pgn_presign(ext: str = ".jpg", user: dict = Depends(require_login)):
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
def fide_calculator_page(request: Request, user: dict = Depends(require_login)):
    return templates.TemplateResponse(request=request, name="fide_calculator.html",
                                      context={"current_user": user})

@app.post("/fide-calculator/calculate")
async def fide_calculate(request: Request, _user: dict = Depends(require_login)):
    body = await request.json()
    result = calculate_rating(body.get("opponents", []), body.get("results", []))
    return result or {"error": "Need 5+ valid games against FIDE-rated opponents"}

@app.post("/fide-calculator/pdf")
def fide_pdf(
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
def tournament_settings(
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
def entry_list_page(request: Request, tid: int):
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
def tournament_register_page(request: Request, tid: int, cancelled: Optional[str] = None):
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
def tournament_register_submit(
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

    name = name.strip()
    email = (email or "").strip() or None
    phone = (phone or "").strip() or None
    max_round = int(tournament.get("rounds") or 0)
    requested_byes = sorted({r for r in requested_byes if isinstance(r, int) and 1 <= r <= max_round})

    errors = []
    if not name:
        errors.append("Name is required.")
    elif len(name) > 100:
        errors.append("Name is too long (max 100 characters).")
    if email and not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", email):
        errors.append("Please enter a valid email address.")
    if phone and not re.match(r"^[0-9+\-() .]{7,20}$", phone):
        errors.append("Please enter a valid phone number.")
    if errors:
        return templates.TemplateResponse(request=request, name="tournament_register.html", context={
            "tournament": tournament,
            "current_user": get_current_user(request),
            "error": " ".join(errors),
        }, status_code=400)

    entry_fee = float(tournament.get("entry_fee") or 0)
    local = lookup_uscf_member(uscf_id) if uscf_id else None
    expiry = local.get("expiry") if local else None
    player_id = register_player_public(
        tid, name, uscf_id or None, rating or 0,
        email, phone, fide_id or None, expiry,
        requested_byes=requested_byes,
        payment_status="pending" if entry_fee > 0 else "waived",
    )
    view_token = _make_view_token(player_id)
    if entry_fee > 0 and STRIPE_SECRET_KEY:
        stripe.api_key = STRIPE_SECRET_KEY
        base_url = str(request.base_url).rstrip("/")
        session = stripe.checkout.Session.create(
            payment_method_types=["card"],
            line_items=[{"price_data": {"currency": "usd",
                "product_data": {"name": f"Entry fee: {tournament['name']}"},
                "unit_amount": int(entry_fee * 100)}, "quantity": 1}],
            mode="payment",
            success_url=f"{base_url}/tournament/{tid}/register/success?session_id={{CHECKOUT_SESSION_ID}}&player_id={player_id}&token={view_token}",
            cancel_url=f"{base_url}/tournament/{tid}/register/cancel?token={_make_cancel_token(player_id)}",
            customer_email=email or None,
            metadata={"player_id": str(player_id), "tournament_id": str(tid)},
        )
        return RedirectResponse(session.url, status_code=303)
    elif entry_fee > 0:
        import logging
        logging.warning(f"[DEV] Stripe not configured — skipping payment for player {player_id}")
        update_player_payment(player_id, "waived")
    return RedirectResponse(f"/tournament/{tid}/register/success?player_id={player_id}&token={view_token}", status_code=303)


# ---------------------------------------------------------------------------
# Stripe callbacks (no auth)
# ---------------------------------------------------------------------------

@app.post("/stripe/webhook")
async def stripe_webhook(request: Request):
    """Source of truth for payment confirmation — fires server-to-server from
    Stripe regardless of whether the payer's browser ever returns to
    /register/success, unlike that route which only runs on a client redirect."""
    payload = await request.body()
    sig_header = request.headers.get("stripe-signature", "")
    if not STRIPE_WEBHOOK_SECRET:
        logging.error("Received Stripe webhook but STRIPE_WEBHOOK_SECRET is not configured — rejecting")
        raise HTTPException(status_code=500, detail="Webhook not configured")
    try:
        event = stripe.Webhook.construct_event(payload, sig_header, STRIPE_WEBHOOK_SECRET)
    except (ValueError, stripe.SignatureVerificationError):
        raise HTTPException(status_code=400, detail="Invalid webhook signature")

    if event["type"] == "checkout.session.completed":
        session = event["data"]["object"].to_dict()
        if session.get("payment_status") == "paid":
            metadata = session.get("metadata") or {}
            player_id = metadata.get("player_id")
            if player_id:
                update_player_payment(int(player_id), "paid", session.get("id"))
            enrollment_id = metadata.get("enrollment_id")
            if enrollment_id:
                _complete_enrollment(int(enrollment_id), session.get("id"))

    return JSONResponse({"status": "success"})


@app.get("/tournament/{tid}/register/success", response_class=HTMLResponse)
def register_success(request: Request, tid: int, player_id: int, token: Optional[str] = None, session_id: Optional[str] = None):
    import json as _json
    # player_id is a sequential, guessable int — the signed token (handed out
    # only to the person who just submitted this exact registration) is what
    # actually authorizes viewing it, so no one can enumerate other players'
    # names/contact info/payment status by walking player_id.
    if not token or _verify_view_token(token) != player_id:
        raise HTTPException(404)
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
def register_cancel(tid: int, token: Optional[str] = None):
    player_id = _verify_cancel_token(token) if token else None
    if player_id:
        player = get_player(player_id)
        # Only delete the registration this token was actually issued for,
        # still unpaid, and still belonging to this tournament — a valid
        # signature alone isn't enough once the state it describes has moved on.
        if player and player["tournament_id"] == tid and player["payment_status"] == "pending":
            delete_player(player_id)
    return RedirectResponse(f"/tournament/{tid}/register?cancelled=1", status_code=303)


# ---------------------------------------------------------------------------
# Withdraw / restore player (TD)
# ---------------------------------------------------------------------------

@app.post("/player/{pid}/withdraw")
def withdraw_player(pid: int, _user: dict = Depends(require_td)):
    tid = set_player_status(pid, "withdrawn")
    return RedirectResponse(f"/tournament/{tid}", status_code=303)


@app.post("/player/{pid}/restore")
def restore_player(pid: int, _user: dict = Depends(require_td)):
    tid = set_player_status(pid, "active")
    return RedirectResponse(f"/tournament/{tid}", status_code=303)


# ---------------------------------------------------------------------------
# Bye request management (TD)
# ---------------------------------------------------------------------------

@app.post("/player/{pid}/bye-request")
def update_bye_request(
    pid: int,
    round_num: int = Form(...),
    action: str = Form(...),
    _user: dict = Depends(require_td),
):
    tid = update_player_bye_request(pid, round_num, action)
    return RedirectResponse(f"/tournament/{tid}", status_code=303)


@app.get("/privacy", response_class=HTMLResponse)
def privacy(request: Request):
    return templates.TemplateResponse(request=request, name="privacy.html")


# ---------------------------------------------------------------------------
# Research Advanced Players
# ---------------------------------------------------------------------------

async def _scrape_round_pairings(url: str) -> dict:
    """Fetch and parse a chess-results.com round pairings page.

    Standard flag=30 column layout (0-based):
      0=Bo. 1=No(white) 2=Name(white) 3=Rtg 4=Pts 5=Result 6=Pts 7=Name(black) 8=Rtg 9=No(black)
    Result strings: '1 - 0', '0 - 1', '½ - ½'
    """
    from bs4 import BeautifulSoup

    if 'prt=' not in url:
        sep = '&' if '?' in url else '?'
        url = url + sep + 'prt=1'

    m = re.search(r'rd=(\d+)', url)
    round_num = int(m.group(1)) if m else 1

    ua = {"User-Agent": "Mozilla/5.0 (compatible; MyChesspairings/1.0)"}
    async with httpx.AsyncClient(follow_redirects=True, timeout=20) as client:
        resp = await client.get(url, headers=ua)
        resp.raise_for_status()

    soup = BeautifulSoup(resp.text, "html.parser")
    title_tag = soup.find('h2') or soup.find('title')
    tournament_name = title_tag.get_text(strip=True) if title_tag else ''

    # Find the pairings table — contains both "Bo." and "Result"
    pairings_table = None
    for table in soup.find_all('table'):
        txt = table.get_text()
        if 'Bo.' in txt and 'Result' in txt:
            pairings_table = table
            break

    if not pairings_table:
        return {'round': round_num, 'pairs': [], 'tournament_name': tournament_name,
                'error': 'Pairings table not found'}

    # Find the actual column-header row: the tr that has a cell exactly "Bo."
    header_row = None
    all_rows = pairings_table.find_all('tr')
    for tr in all_rows:
        cells = tr.find_all(['th', 'td'])
        if any(c.get_text(strip=True) == 'Bo.' for c in cells):
            header_row = tr
            break

    if not header_row:
        return {'round': round_num, 'pairs': [], 'tournament_name': tournament_name,
                'error': 'Header row with "Bo." not found'}

    headers_text = [c.get_text(strip=True) for c in header_row.find_all(['th', 'td'])]

    def _find_col(hdrs, label, default):
        return next((i for i, h in enumerate(hdrs) if h.strip() == label), default)

    # chess-results flag=30 has 14 cols: Bo|No|flag|title|White|Rtg|Pts|Result|Pts|title|Black|Rtg|flag|No
    result_col    = _find_col(headers_text, 'Result', 7)
    white_no_col  = _find_col(headers_text, 'No.', 1)            # first "No." = white start rank
    white_name_col = _find_col(headers_text, 'White', 4)         # "White" label column
    black_name_col = _find_col(headers_text, 'Black', result_col + 3)  # "Black" label column
    # Last "No." = black start rank
    rev = list(reversed(headers_text))
    black_no_col  = len(headers_text) - 1 - _find_col(rev, 'No.', 0)

    def _no(val):
        try:
            return int(str(val).strip())
        except Exception:
            return 0

    pairs = []
    header_row_index = next((i for i, tr in enumerate(all_rows) if tr is header_row), 0)
    for row in all_rows[header_row_index + 1:]:
        cells = [td.get_text(strip=True) for td in row.find_all('td')]
        if not cells:
            continue
        board_raw = cells[0]
        if not board_raw.isdigit():
            continue  # skip sub-headers or section labels

        if len(cells) <= result_col:
            continue

        result_raw = cells[result_col]
        if '½' in result_raw or '1/2' in result_raw:
            result = '½ - ½'
        elif '1 - 0' in result_raw or '1-0' in result_raw or '1:0' in result_raw:
            result = '1 - 0'
        elif '0 - 1' in result_raw or '0-1' in result_raw or '0:1' in result_raw:
            result = '0 - 1'
        else:
            result = result_raw or '*'

        white_no   = _no(cells[white_no_col])   if white_no_col   < len(cells) else 0
        white_name = cells[white_name_col]       if white_name_col < len(cells) else ''
        black_name = cells[black_name_col]       if black_name_col < len(cells) else ''
        black_no   = _no(cells[black_no_col])   if black_no_col   < len(cells) else 0

        pairs.append({
            'board_no':   int(board_raw),
            'white_no':   white_no,
            'white_name': white_name,
            'black_no':   black_no,
            'black_name': black_name,
            'result':     result,
        })

    return {
        'round': round_num,
        'pairs': pairs,
        'tournament_name': tournament_name,
        'debug': {
            'headers': headers_text,
            'result_col': result_col,
            'white_no_col': white_no_col,
            'black_no_col': black_no_col,
            'sample': pairs[:3],
        },
    }

async def _scrape_chess_results(url: str):
    """Fetch and parse a chess-results.com player list page.
    Returns (tournament_name, players_list).

    chess-results renders the starting-rank table on art=0&prt=1 (print view of
    tournament info page). Columns in the CRs1 table are fixed:
      0=No, 1=flag-img, 2=Title, 3=Name, 4=FideID, 5=FED, 6=Rtg
    """
    from bs4 import BeautifulSoup
    import re as _re

    base = url.split('?')[0]
    qs   = url.split('?')[1] if '?' in url else 'lan=1'
    qs   = _re.sub(r'(?:^|&)art=\d+', '', qs).lstrip('&')
    fetch_url = f"{base}?{qs}&lan=1&art=0&prt=1"

    ua = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124 Safari/537.36"
    async with httpx.AsyncClient(follow_redirects=True, timeout=20) as client:
        r = await client.get(fetch_url, headers={"User-Agent": ua})
        r.raise_for_status()

    soup = BeautifulSoup(r.text, 'html.parser')

    # Tournament name from <title>
    title_tag = soup.find('title')
    tournament_name = title_tag.get_text(' ', strip=True) if title_tag else 'Unknown Tournament'
    tournament_name = _re.sub(r'\s*[-|–]\s*Chess-Results.*$', '', tournament_name, flags=_re.IGNORECASE).strip()
    tournament_name = _re.sub(r'^Chess-Results Server\s*', '', tournament_name, flags=_re.IGNORECASE).strip()

    # The starting rank table has class "CRs1" and a header containing "FideID"
    target_table = None
    for t in soup.find_all('table', class_='CRs1'):
        header_text = t.find('tr').get_text() if t.find('tr') else ''
        if 'FideID' in header_text or 'Name' in header_text:
            target_table = t
            break

    if not target_table:
        raise ValueError("Could not find player table (CRs1 with FideID header) on the chess-results page.")

    rows = target_table.find_all('tr')
    players = []
    for row in rows[1:]:  # skip header
        cells = row.find_all(['td', 'th'])
        if len(cells) < 5:
            continue
        # Fixed columns: 0=No, 1=flag, 2=Title, 3=Name, 4=FideID, 5=FED, 6=Rtg
        title       = cells[2].get_text(strip=True)
        name        = cells[3].get_text(' ', strip=True)
        fide_id_raw = cells[4].get_text(strip=True)
        country     = cells[5].get_text(strip=True) if len(cells) > 5 else ''
        rtg_raw     = cells[6].get_text(strip=True) if len(cells) > 6 else ''

        if not name or not fide_id_raw:
            continue
        # Extract numeric FIDE ID (strip any non-digit chars from link text)
        fide_id = _re.sub(r'\D', '', fide_id_raw)

        players.append({
            'start_rank':  len(players) + 1,
            'name':        name,
            'title':       title,
            'fide_id':     fide_id,
            'fide_rating': int(rtg_raw) if rtg_raw.isdigit() else 0,
            'country':     country,
            'national_id': '',
        })

    return tournament_name, players


@app.get("/research-players", response_class=HTMLResponse)
def research_players_page(
    request: Request,
    q: str = '',
    user: dict = Depends(require_login),
):
    players = list_research_players(search=q)
    sources = list_research_sources()
    return templates.TemplateResponse(request=request, name="research_players.html", context={
        "players": players, "sources": sources, "q": q,
    })


@app.post("/research-players/import")
async def research_import(
    request: Request,
    url: str = Form(...),
    user: dict = Depends(require_login),
):
    error = None
    imported = 0
    tournament_name = ''
    try:
        tournament_name, players = await _scrape_chess_results(url.strip())
        imported = upsert_research_players(players, url.strip(), tournament_name, user['id'])
    except Exception as exc:
        error = str(exc)
    sources = list_research_sources()
    all_players = list_research_players()
    return templates.TemplateResponse(request=request, name="research_players.html", context={
        "players": all_players, "sources": sources, "q": '',
        "import_result": {"imported": imported, "tournament": tournament_name, "error": error},
    })


@app.post("/research-players/delete/{rid}")
def research_delete(rid: int, user: dict = Depends(require_login)):
    delete_research_player(rid)
    return RedirectResponse("/research-players", status_code=303)


@app.post("/research-players/delete-source")
def research_delete_source(
    tournament_source: str = Form(...),
    user: dict = Depends(require_login),
):
    delete_research_by_source(tournament_source)
    return RedirectResponse("/research-players", status_code=303)


async def _scrape_standings(url: str) -> dict:
    """Fetch chess-results.com 'Rank after Round N' page (art=1 or art=3).

    Returns {round, standings: [{start_no, name, pts}], tournament_name}.
    Columns: Rk.|SNo|flag|title|Name|FED|Rtg|Pts.|TB1...
    """
    from bs4 import BeautifulSoup

    if 'prt=' not in url:
        sep = '&' if '?' in url else '?'
        url = url + sep + 'prt=1'

    m = re.search(r'rd=(\d+)', url)
    round_num = int(m.group(1)) if m else 1

    ua = {"User-Agent": "Mozilla/5.0 (compatible; MyChesspairings/1.0)"}
    async with httpx.AsyncClient(follow_redirects=True, timeout=20) as client:
        resp = await client.get(url, headers=ua)
        resp.raise_for_status()

    soup = BeautifulSoup(resp.text, "html.parser")
    title_tag = soup.find('h2') or soup.find('title')
    tournament_name = title_tag.get_text(strip=True) if title_tag else ''

    # Find standings table — chess-results uses class="CRs1" for main content tables
    # Target the specific one whose header row contains "SNo"
    standings_table = None
    for table in soup.find_all('table', class_='CRs1'):
        if 'SNo' in table.get_text():
            standings_table = table
            break
    # Fallback: any table containing SNo + Pts.
    if not standings_table:
        for table in soup.find_all('table'):
            if 'SNo' in table.get_text() and 'Pts.' in table.get_text():
                standings_table = table
                break

    if not standings_table:
        return {'round': round_num, 'standings': [], 'tournament_name': tournament_name,
                'error': 'Standings table not found'}

    # Find the header row (the tr that has a cell with exactly "SNo")
    header_row = None
    all_rows = standings_table.find_all('tr', recursive=False)  # direct children only
    if not all_rows:
        # table may use tbody
        all_rows = standings_table.find_all('tr')
    for tr in all_rows:
        if any(c.get_text(strip=True) == 'SNo' for c in tr.find_all(['th', 'td'])):
            header_row = tr
            break

    if not header_row:
        return {'round': round_num, 'standings': [], 'tournament_name': tournament_name,
                'error': 'Header row with "SNo" not found'}

    headers = [c.get_text(strip=True) for c in header_row.find_all(['th', 'td'])]

    def _col(hdrs, label, default):
        return next((i for i, h in enumerate(hdrs) if h.strip() == label), default)

    sno_col  = _col(headers, 'SNo',  1)
    name_col = _col(headers, 'Name', 4)
    pts_col  = _col(headers, 'Pts.', 7)

    def _pts(val):
        try:
            return float(str(val).replace(',', '.').strip())
        except Exception:
            return 0.0

    standings = []
    header_idx = next((i for i, tr in enumerate(all_rows) if tr is header_row), 0)
    for row in all_rows[header_idx + 1:]:
        cells = [td.get_text(strip=True) for td in row.find_all('td')]
        if not cells:
            continue
        try:
            start_no = int(cells[sno_col])
        except Exception:
            continue
        name = cells[name_col] if name_col < len(cells) else ''
        pts = _pts(cells[pts_col]) if pts_col < len(cells) else 0.0
        standings.append({'start_no': start_no, 'name': name, 'pts': pts})

    return {'round': round_num, 'standings': standings, 'tournament_name': tournament_name}


@app.get("/api/research/debug-standings")
async def debug_standings(url: str, user: dict = Depends(require_login)):
    """Return raw standings scrape result for debugging."""
    try:
        data = await _scrape_standings(url)
        return JSONResponse({
            'round': data.get('round'),
            'tournament_name': data.get('tournament_name'),
            'error': data.get('error'),
            'player_count': len(data.get('standings', [])),
            'first_5': data.get('standings', [])[:5],
        })
    except Exception as e:
        return JSONResponse({'exception': str(e)}, status_code=500)


@app.post("/research-players/import-standings", response_class=HTMLResponse)
async def research_import_standings(
    request: Request,
    tournament_source: str = Form(''),
    standings_url: str = Form(''),
    my_rank: int = Form(0),
    buffer: int = Form(4),
    user: dict = Depends(require_login),
):
    from urllib.parse import quote
    import_ok = None
    import_error = None
    try:
        data = await _scrape_standings(standings_url)
        if data.get('error'):
            import_error = data['error']
        else:
            store_standings(tournament_source, data['round'], data['standings'], standings_url=standings_url)
            n = len(data['standings'])
            import_ok = f"Standings for round {data['round']} imported ({n} players)"
    except Exception as e:
        import_error = str(e)

    qs = f"tournament_source={quote(tournament_source)}&my_rank={my_rank}&buffer={buffer}"
    if import_ok:
        qs += f"&import_msg={quote(import_ok)}"
    else:
        qs += f"&import_err={quote(str(import_error))}"
    return RedirectResponse(f"/research-players/simulate?{qs}", status_code=303)


@app.post("/research-players/refresh-standings", response_class=HTMLResponse)
async def research_refresh_standings(
    request: Request,
    tournament_source: str = Form(''),
    round_num: int = Form(0),
    my_rank: int = Form(0),
    buffer: int = Form(4),
    user: dict = Depends(require_login),
):
    from urllib.parse import quote
    imported = list_imported_standings(tournament_source)
    entry = next((s for s in imported if s['round'] == round_num), None)
    standings_url = entry['standings_url'] if entry else ''
    import_ok = None
    import_error = None
    if not standings_url:
        import_error = f"No stored URL for standings round {round_num}"
    else:
        try:
            data = await _scrape_standings(standings_url)
            if data.get('error'):
                import_error = data['error']
            else:
                store_standings(tournament_source, data['round'], data['standings'], standings_url=standings_url)
                n = len(data['standings'])
                import_ok = f"Round {round_num} standings refreshed ({n} players)"
        except Exception as e:
            import_error = str(e)

    qs = f"tournament_source={quote(tournament_source)}&my_rank={my_rank}&buffer={buffer}"
    if import_ok:
        qs += f"&import_msg={quote(import_ok)}"
    else:
        qs += f"&import_err={quote(str(import_error))}"
    return RedirectResponse(f"/research-players/simulate?{qs}", status_code=303)


@app.post("/research-players/import-round", response_class=HTMLResponse)
async def research_import_round(
    request: Request,
    tournament_source: str = Form(''),
    round_url: str = Form(''),
    my_rank: int = Form(0),
    buffer: int = Form(4),
    user: dict = Depends(require_login),
):
    try:
        data = await _scrape_round_pairings(round_url)
        if data.get('error'):
            import_error = data['error']
            import_ok = None
        else:
            store_round_results(tournament_source, data['round'], data['pairs'], round_url=round_url)
            pairs = data['pairs']
            sample = ', '.join(
                f"#{p['white_no']} vs #{p['black_no']}"
                for p in pairs[:3]
            )
            import_ok = (
                f"Imported {len(pairs)} pairings for round {data['round']}. "
                f"Sample: {sample}"
            )
            import_error = None
    except Exception as e:
        import_ok = None
        import_error = str(e)

    from urllib.parse import quote
    qs = f"tournament_source={quote(tournament_source)}&my_rank={my_rank}&buffer={buffer}"
    if import_ok:
        qs += f"&import_msg={quote(import_ok)}"
    else:
        qs += f"&import_err={quote(str(import_error))}"
    return RedirectResponse(f"/research-players/simulate?{qs}", status_code=303)


@app.post("/research-players/refresh-round", response_class=HTMLResponse)
async def research_refresh_round(
    request: Request,
    tournament_source: str = Form(''),
    round_num: int = Form(0),
    my_rank: int = Form(0),
    buffer: int = Form(4),
    user: dict = Depends(require_login),
):
    """Re-scrape a previously imported round using its stored URL."""
    from urllib.parse import quote
    urls = get_round_urls(tournament_source)
    round_url = urls.get(round_num, '')
    import_ok = None
    import_error = None
    if not round_url:
        import_error = f"No stored URL for round {round_num}"
    else:
        try:
            data = await _scrape_round_pairings(round_url)
            if data.get('error'):
                import_error = data['error']
            else:
                store_round_results(tournament_source, data['round'], data['pairs'], round_url=round_url)
                pairs = data['pairs']
                done = sum(1 for p in pairs if p['result'] != '*')
                import_ok = f"Round {round_num} refreshed: {done}/{len(pairs)} results in"
        except Exception as e:
            import_error = str(e)

    qs = f"tournament_source={quote(tournament_source)}&my_rank={my_rank}&buffer={buffer}"
    if import_ok:
        qs += f"&import_msg={quote(import_ok)}"
    else:
        qs += f"&import_err={quote(str(import_error))}"
    return RedirectResponse(f"/research-players/simulate?{qs}", status_code=303)


@app.get("/research-players/simulate", response_class=HTMLResponse)
def research_simulate(
    request: Request,
    tournament_source: str = '',
    my_rank: int = 0,
    buffer: int = 4,
    import_msg: str = '',
    import_err: str = '',
    user: dict = Depends(require_login),
):
    import math
    players = []
    opponent_rank = None
    total = 0
    tournament_name = ''

    # Auto-detect user's starting rank from their FIDE ID
    profile = get_user_profile(user['id'])
    auto_rank = 0
    if tournament_source and profile and profile.get('fide_id'):
        found = get_research_player_fide_id_by_user(tournament_source, profile['fide_id'])
        if found:
            auto_rank = found['start_rank']

    effective_rank = my_rank or auto_rank

    # Round 1 simulation (pre-tournament)
    if tournament_source and effective_rank:
        total = get_research_player_count(tournament_source)
        half = math.ceil(total / 2)
        opponent_rank = (effective_rank + half) if effective_rank <= half else (effective_rank - half)
        rank_lo = max(1, opponent_rank - buffer)
        rank_hi = min(total, opponent_rank + buffer)
        players = get_research_players_by_rank_range(tournament_source, rank_lo, rank_hi)
        if players:
            tournament_name = players[0]['tournament_name']

    # Round results and predictions for subsequent rounds (pairings-based)
    imported_rounds = list_imported_rounds(tournament_source) if tournament_source else []
    round_urls = get_round_urls(tournament_source) if tournament_source else {}
    round_predictions = []

    if imported_rounds and tournament_source and effective_rank:
        for rd in imported_rounds:
            rd_results = get_round_results(tournament_source, rd)
            my_result = None
            for pair in rd_results:
                if pair['white_start_no'] == effective_rank:
                    raw = pair['result']
                    outcome = 'Win' if raw == '1 - 0' else ('Loss' if raw == '0 - 1' else 'Draw')
                    my_result = {'color': 'White', 'opponent_no': pair['black_start_no'],
                                 'opponent_name': pair['black_name'], 'result': raw, 'outcome': outcome}
                    break
                elif pair['black_start_no'] == effective_rank:
                    raw = pair['result']
                    outcome = 'Win' if raw == '0 - 1' else ('Loss' if raw == '1 - 0' else 'Draw')
                    my_result = {'color': 'Black', 'opponent_no': pair['white_start_no'],
                                 'opponent_name': pair['white_name'], 'result': raw, 'outcome': outcome}
                    break

            scores_thru = get_player_scores_after_round(tournament_source, rd)
            my_sc = scores_thru.get(effective_rank, 0)
            same_group = sorted([no for no, sc in scores_thru.items() if sc == my_sc])
            if effective_rank not in same_group:
                same_group.append(effective_rank)
                same_group.sort()
            group_size = len(same_group)
            my_pos = same_group.index(effective_rank)
            half2 = math.ceil(group_size / 2)
            opp_pos = (my_pos + half2) if my_pos < half2 else (my_pos - half2)
            next_opp_no = same_group[opp_pos] if 0 <= opp_pos < group_size else None

            r2_players = []
            if next_opp_no:
                pos_lo = max(0, opp_pos - buffer)
                pos_hi = min(group_size - 1, opp_pos + buffer)
                r2_players = get_research_players_by_start_nos(tournament_source, same_group[pos_lo:pos_hi + 1])

            round_predictions.append({
                'after_round': rd,
                'predict_round': rd + 1,
                'my_result': my_result,
                'my_score': my_sc,
                'score_group_size': group_size,
                'opponent_rank': next_opp_no,
                'players': r2_players,
            })

    # Standings-based predictions (primary path when standings are imported)
    imported_standings_list = list_imported_standings(tournament_source) if tournament_source else []
    standings_predictions = []

    if imported_standings_list and tournament_source and effective_rank:
        # Build a lookup of round → in-progress pairings from imported round results
        in_progress_by_round: dict = {}
        for rd in imported_rounds:
            rd_results = get_round_results(tournament_source, rd)
            in_progress_by_round[rd] = {
                pair['white_start_no'] for pair in rd_results if pair['result'] == '*'
            } | {pair['black_start_no'] for pair in rd_results if pair['result'] == '*'}

        for st_info in imported_standings_list:
            st_round = st_info['round']
            all_st = get_round_standings(tournament_source, st_round)
            if not all_st:
                continue

            score_map = {s['start_no']: s for s in all_st}
            me = score_map.get(effective_rank)
            if not me:
                continue
            my_score = me['pts']

            # Who is in the score group right now?
            confirmed_group = sorted(
                [s for s in all_st if s['pts'] == my_score],
                key=lambda s: s['start_no']
            )

            # For in-progress games this round: classify potential entrants
            # score shown in standings = pre-round score for still-playing players
            in_progress_nos = in_progress_by_round.get(st_round, set())
            potential = []  # {'start_no', 'name', 'current_pts', 'needed'}
            for s in all_st:
                if s['start_no'] in in_progress_nos and s['pts'] != my_score:
                    # Can any outcome bring them to my_score?
                    for delta, needed in [(1.0, 'win'), (0.5, 'draw'), (0.0, 'loss')]:
                        if abs(s['pts'] + delta - my_score) < 0.01:
                            potential.append({**s, 'needed': needed})
                            break

            # Swiss pairing within confirmed group
            group_size = len(confirmed_group)
            my_pos = next((i for i, s in enumerate(confirmed_group) if s['start_no'] == effective_rank), -1)
            half2 = math.ceil(group_size / 2)
            opp_pos = (my_pos + half2) if my_pos >= 0 and my_pos < half2 else max(0, my_pos - half2)
            predicted_opp = confirmed_group[opp_pos] if 0 <= opp_pos < group_size else None

            # Show ±buffer window around predicted opponent
            pos_lo = max(0, opp_pos - buffer)
            pos_hi = min(group_size - 1, opp_pos + buffer)
            likely_nos = [s['start_no'] for s in confirmed_group[pos_lo:pos_hi + 1]]
            st_players = get_research_players_by_start_nos(tournament_source, likely_nos)
            # Also load any potential entrants from player_research
            pot_nos = [p['start_no'] for p in potential]
            pot_players = get_research_players_by_start_nos(tournament_source, pot_nos)
            pot_player_map = {p['start_rank']: p for p in pot_players}

            standings_predictions.append({
                'standings_round': st_round,
                'predict_round': st_round + 1,
                'my_score': my_score,
                'confirmed_group': confirmed_group,
                'my_pos': my_pos,
                'predicted_opp': predicted_opp,
                'players': st_players,
                'potential': [
                    {**p, 'research': pot_player_map.get(p['start_no'])}
                    for p in potential
                ],
                'has_in_progress': bool(in_progress_nos),
            })

    sources = list_research_sources()
    return templates.TemplateResponse(request=request, name="research_simulate.html", context={
        "sources": sources,
        "tournament_source": tournament_source,
        "tournament_name": tournament_name,
        "my_rank": my_rank,
        "auto_rank": auto_rank,
        "opponent_rank": opponent_rank,
        "buffer": buffer,
        "total": total,
        "players": players,
        "imported_rounds": imported_rounds,
        "round_urls": round_urls,
        "round_predictions": round_predictions,
        "imported_standings_list": imported_standings_list,
        "standings_predictions": standings_predictions,
        "import_msg": import_msg,
        "import_err": import_err,
    })


@app.post("/api/research/fetch-chessbase")
async def fetch_chessbase(
    request: Request,
    user: dict = Depends(require_login),
):
    """Scrape ChessBase player page and store data including PGN links."""
    from bs4 import BeautifulSoup
    body = await request.json()
    player_name: str = body.get("name", "")
    fide_id: str = body.get("fide_id", "")

    if not player_name:
        return JSONResponse({"error": "name is required"}, status_code=400)

    parts = player_name.split(",")
    cb_last = parts[0].strip().lower().replace(" ", "_")
    cb_first = parts[1].strip().lower().replace(" ", "_") if len(parts) > 1 else ""
    cb_query = f"{cb_last}_{cb_first}" if cb_first else f"{cb_last}_"

    search_url = f"https://players.chessbase.com/en/search/{cb_query}"
    headers = {"User-Agent": "Mozilla/5.0 (compatible; MyChesspairings/1.0)"}

    async with httpx.AsyncClient(follow_redirects=True, timeout=20) as client:
        try:
            resp = await client.get(search_url, headers=headers)
            resp.raise_for_status()
        except Exception as e:
            return JSONResponse({"error": f"Failed to fetch ChessBase: {e}"}, status_code=502)

        soup = BeautifulSoup(resp.text, "html.parser")

        # Look for player profile link in search results
        player_id = None
        player_url = None
        # URL format: /en/player/{name}/{id}  e.g. /en/player/scrimieri_gabriele/745621
        profile_link = soup.find("a", href=re.compile(r"/en/player/[^/\"']+/\d+"))
        if profile_link:
            player_url = "https://players.chessbase.com" + profile_link["href"]
            m = re.search(r"/en/player/[^/]+/(\d+)", profile_link["href"])
            if m:
                player_id = m.group(1)

        raw_info = soup.get_text(separator="\n", strip=True)[:8000]
        pgn_links = []

        if player_url:
            try:
                presp = await client.get(player_url, headers=headers)
                presp.raise_for_status()
                psoup = BeautifulSoup(presp.text, "html.parser")
                raw_info = psoup.get_text(separator="\n", strip=True)[:8000]
                for a in psoup.find_all("a", href=re.compile(r"\.pgn", re.I)):
                    href = a["href"]
                    if not href.startswith("http"):
                        href = "https://players.chessbase.com" + href
                    pgn_links.append(href)
            except Exception:
                pass

    pgn_data = "\n".join(pgn_links)

    upsert_player_chessbase(
        fide_id=fide_id or "",
        player_name=player_name,
        chessbase_player_id=player_id or "",
        chessbase_url=player_url or search_url,
        game_count=len(pgn_links),
        pgn_data=pgn_data,
        raw_info=raw_info,
    )

    return JSONResponse({
        "ok": True,
        "player_id": player_id,
        "player_url": player_url or search_url,
        "pgn_count": len(pgn_links),
        "pgn_links": pgn_links[:20],
        "info_text": raw_info[:1200],
    })


@app.post("/api/research/player-links")
async def save_player_links(
    request: Request,
    user: dict = Depends(require_login),
):
    """Save Lichess and Chess.com usernames for a research player."""
    body = await request.json()
    player_id = int(body.get("player_id", 0))
    lichess_id = (body.get("lichess_id") or "").strip().lstrip("@")
    chessdotcom_id = (body.get("chessdotcom_id") or "").strip()
    if not player_id:
        return JSONResponse({"error": "player_id required"}, status_code=400)
    update_research_player_links(player_id, lichess_id, chessdotcom_id)
    return JSONResponse({"ok": True})


@app.get("/api/research/player-info")
async def research_player_info(
    request: Request,
    player_id: int = 0,
    fide_id: str = '',
    name: str = '',
    lichess_id: str = '',
    chessdotcom_id: str = '',
    user: dict = Depends(require_login),
):
    """Fetch player ratings/profile from FIDE, Lichess, Chess.com."""
    from bs4 import BeautifulSoup

    parts = [p.strip() for p in name.split(',')] if ',' in name else [name.strip(), '']
    lastname, firstname = parts[0], (parts[1] if len(parts) > 1 else '')

    result: dict = {}
    ua = {"User-Agent": "Mozilla/5.0 (compatible; MyChesspairings/1.0)"}

    async with httpx.AsyncClient(follow_redirects=True, timeout=12) as client:

        # ── FIDE ──────────────────────────────────────────────────────────────
        if fide_id:
            fide_info: dict = {"profile_url": f"https://ratings.fide.com/profile/{fide_id}"}
            try:
                r = await client.get(f"https://ratings.fide.com/profile/{fide_id}", headers=ua)
                soup = BeautifulSoup(r.text, "html.parser")
                ratings: dict = {}
                full_text = soup.get_text(separator="\n")
                for label in ("Standard", "Rapid", "Blitz"):
                    idx = full_text.find(label)
                    if idx >= 0:
                        snippet = full_text[idx:idx+40]
                        m = re.search(r'\b(\d{3,4})\b', snippet)
                        if m:
                            ratings[label.lower()] = int(m.group(1))
                fide_info["ratings"] = ratings
            except Exception as e:
                fide_info["error"] = str(e)
            result["fide"] = fide_info

        # ── Lichess ───────────────────────────────────────────────────────────
        lc_username = lichess_id.lstrip("@").strip() if lichess_id else ""
        if not lc_username and (firstname or lastname):
            # Autocomplete search by name
            try:
                search_term = f"{firstname} {lastname}".strip()
                r = await client.get(
                    "https://lichess.org/api/player/autocomplete",
                    params={"term": search_term, "object": "true"},
                    headers={**ua, "Accept": "application/json"},
                )
                if r.status_code == 200:
                    candidates = r.json()
                    if isinstance(candidates, list) and candidates:
                        lc_username = candidates[0].get("id", "")
            except Exception:
                pass

        if lc_username:
            try:
                ur = await client.get(
                    f"https://lichess.org/api/user/{lc_username}",
                    headers={**ua, "Accept": "application/json"},
                )
                if ur.status_code == 200:
                    ud = ur.json()
                    perfs = ud.get("perfs", {})
                    tc_ratings: dict = {}
                    total_games = 0
                    for tc in ("bullet", "blitz", "rapid", "classical"):
                        if tc in perfs and "rating" in perfs[tc]:
                            tc_ratings[tc] = perfs[tc]["rating"]
                            total_games += perfs[tc].get("games", 0)
                    result["lichess"] = {
                        "username": lc_username,
                        "title": ud.get("title"),
                        "profile_url": f"https://lichess.org/@/{lc_username}",
                        "ratings": tc_ratings,
                        "total_games": total_games,
                        "found_via": "stored" if lichess_id else "search",
                    }
                else:
                    result["lichess"] = {"error": f"User not found: {lc_username}"}
            except Exception as e:
                result["lichess"] = {"error": str(e)}
        else:
            result["lichess"] = None

        # ── Chess.com ─────────────────────────────────────────────────────────
        cc_username = chessdotcom_id.strip() if chessdotcom_id else ""
        if not cc_username and firstname and lastname:
            fn, ln = firstname.lower().replace(" ", ""), lastname.lower().replace(" ", "")
            candidates = [f"{fn}{ln}", f"{fn}_{ln}", f"{ln}_{fn}", f"{fn[0]}{ln}", f"{fn}{ln[:5]}"]
            for cand in candidates:
                try:
                    r = await client.get(f"https://api.chess.com/pub/player/{cand}", headers=ua)
                    if r.status_code == 200:
                        cc_username = cand
                        break
                except Exception:
                    pass

        if cc_username:
            try:
                pr = await client.get(f"https://api.chess.com/pub/player/{cc_username}", headers=ua)
                sr = await client.get(f"https://api.chess.com/pub/player/{cc_username}/stats", headers=ua)
                if pr.status_code == 200:
                    pd = pr.json()
                    stats = sr.json() if sr.status_code == 200 else {}
                    cc_ratings: dict = {}
                    for tc, key in [("bullet","chess_bullet"), ("blitz","chess_blitz"), ("rapid","chess_rapid")]:
                        v = stats.get(key, {}).get("last", {}).get("rating")
                        if v:
                            cc_ratings[tc] = v
                    result["chessdotcom"] = {
                        "username": cc_username,
                        "title": pd.get("title"),
                        "profile_url": pd.get("url", f"https://www.chess.com/member/{cc_username}"),
                        "ratings": cc_ratings,
                        "found_via": "stored" if chessdotcom_id else "search",
                    }
                else:
                    result["chessdotcom"] = {"error": f"User not found: {cc_username}"}
            except Exception as e:
                result["chessdotcom"] = {"error": str(e)}
        else:
            result["chessdotcom"] = None

    return JSONResponse(result)


# ---------------------------------------------------------------------------
# PGN Database
# ---------------------------------------------------------------------------

@app.get("/pgn-database", response_class=HTMLResponse)
def pgn_database_page(
    request: Request,
    search: str = '',
    event: str = '',
    research_only: int = 0,
    page: int = 1,
    user: dict = Depends(require_login),
):
    limit = 50
    offset = (page - 1) * limit
    games = list_pgn_games(search=search, event=event,
                            research_only=bool(research_only), limit=limit, offset=offset)
    total = count_pgn_games(search=search, event=event, research_only=bool(research_only))
    return templates.TemplateResponse(request=request, name="pgn_database.html", context={
        "organizers": list_pgn_organizers(),
        "sources":    list_pgn_sources(),
        "games":      games,
        "total":      total,
        "page":       page,
        "pages":      max(1, (total + limit - 1) // limit),
        "search":     search,
        "event":      event,
        "research_only": research_only,
        "events":     list_pgn_events(),
        "import_msg": request.query_params.get("import_msg", ""),
        "import_err": request.query_params.get("import_err", ""),
        "twic": {"last_issue": get_setting("twic_last_issue", "0"), "auto": get_setting("twic_auto", "0") == "1",
                 "running": _twic_state["running"], "message": _twic_state["message"]},
        "is_admin": user.get("role") == "admin",
    })


@app.post("/pgn-database/add-organizer")
def pgn_add_organizer(
    request: Request,
    name: str = Form(...),
    lichess_id: str = Form(''),
    chesscom_id: str = Form(''),
    notes: str = Form(''),
    user: dict = Depends(require_login),
):
    add_pgn_organizer(name.strip(), lichess_id.strip(), chesscom_id.strip(),
                      notes.strip(), user['id'])
    return RedirectResponse("/pgn-database", status_code=303)


@app.post("/pgn-database/delete-organizer/{oid}")
def pgn_delete_organizer(oid: int, user: dict = Depends(require_login)):
    delete_pgn_organizer(oid)
    return RedirectResponse("/pgn-database", status_code=303)


@app.post("/pgn-database/import-file")
async def pgn_import_file(pgn_file: UploadFile = File(...), label: str = Form(""), user: dict = Depends(require_admin)):
    """Bulk PGN upload (e.g. a subset exported from ChessBase) into the games archive."""
    data = await pgn_file.read()
    if not data or len(data) > 50 * 1024 * 1024:
        return RedirectResponse("/pgn-database?import_err=PGN+file+must+be+between+1+byte+and+50+MB", status_code=303)
    try:
        raw = data.decode("utf-8")
    except UnicodeDecodeError:
        raw = data.decode("latin-1")
    name = label.strip() or (pgn_file.filename or "PGN upload")
    run = asyncio.get_event_loop().run_in_executor
    sid = await run(None, ensure_pgn_source, "upload", f"upload:{user['id']}:{uuid.uuid4().hex}", name)
    games = await run(None, _store_pgn_text, raw, sid)
    await run(None, update_pgn_source_fetched, sid, len(games))
    from urllib.parse import quote
    return RedirectResponse(f"/pgn-database?import_msg={quote(f'{len(games)} game(s) imported from {name}')}", status_code=303)


@app.post("/pgn-database/import")
async def pgn_import(
    request: Request,
    url: str = Form(...),
    organizer_id: str = Form(''),
    user: dict = Depends(require_login),
):
    url = url.strip()
    org_id = int(organizer_id) if organizer_id.strip().isdigit() else None
    source_type = ph.detect_source_type(url)
    msg, err = '', ''

    try:
        if source_type == 'lichess':
            info = ph.parse_lichess_broadcast_url(url)
            round_id = info.get('round_id', '')
            if not round_id:
                raise ValueError("Could not extract round ID from URL")

            # Scrape the broadcast page to get tour ID (no direct API for this)
            tour_id = await ph.lichess_tour_id_from_round_page(url)
            total_imported = 0
            rounds_imported = 0

            if tour_id:
                binfo = await ph.lichess_broadcast_info(tour_id)
                tour_name = binfo.get('name') or info.get('tour_slug', '').replace('-', ' ').title()

                # Auto-save organizer if not already tracked
                owner_lichess_id = binfo.get('owner_id', '')
                owner_name       = binfo.get('owner_name', '') or owner_lichess_id
                if owner_lichess_id and not org_id:
                    existing = [o for o in list_pgn_organizers() if o.get('lichess_id') == owner_lichess_id]
                    if existing:
                        org_id = existing[0]['id']
                    else:
                        org_id = add_pgn_organizer(owner_name, owner_lichess_id, '', '', user['id'])

                for rd in binfo.get('rounds', []):
                    rid = rd.get('id', '')
                    rd_url = rd.get('url', '')
                    if not rid or not rd_url:
                        continue
                    rname = rd.get('name', '')
                    rnum_m = re.search(r'(\d+)', rname)
                    rnum = int(rnum_m.group(1)) if rnum_m else 0
                    rd_live = not rd.get('finished', True)
                    sid = upsert_pgn_source('lichess', rd_url, tour_name,
                                            rnum, rname, tour_id, rid, rd_live, org_id)
                    n = await ph.import_lichess_round(rid, sid, insert_pgn_games, rd_url)
                    update_pgn_source_fetched(sid, count_pgn_games_for_source(sid))
                    total_imported += n
                    rounds_imported += 1
                auto_org_note = f" (organizer '{owner_name}' auto-saved)" if owner_lichess_id else ''
                msg = f"Imported {total_imported} games across {rounds_imported} rounds of '{tour_name}'{auto_org_note}"
            else:
                # Could not find tour — import just the single round from user URL
                tour_name = info.get('tour_slug', '').replace('-', ' ').title()
                rnum_m = re.search(r'(\d+)', info.get('round_slug', ''))
                round_num = int(rnum_m.group(1)) if rnum_m else 0
                sid = upsert_pgn_source('lichess', url, tour_name,
                                        round_num, '', '', round_id, False, org_id)
                n = await ph.import_lichess_round(round_id, sid, insert_pgn_games, url)
                update_pgn_source_fetched(sid, count_pgn_games_for_source(sid))
                msg = f"Imported {n} games from round of '{tour_name}'"

        elif source_type == 'chesscom_event':
            slug = ph.parse_chesscom_event_url(url)
            if not slug:
                raise ValueError("Could not extract event slug from Chess.com events URL")

            # Determine rooms: /events/info/{index-slug} → all sections via index API;
            # /events/{room-slug} → single room directly.
            is_info_url = '/events/info/' in url
            if is_info_url:
                einfo = await ph.chesscom_event_info(slug)
                if not einfo:
                    raise ValueError(f"Could not fetch event info for '{slug}'")
                event_name = einfo.get('name', slug)
                rooms_to_import = einfo.get('rooms', [])
                if not rooms_to_import:
                    raise ValueError("No sections (rooms) found for this event")
            else:
                # Room URL — fetch that single room and build a synthetic rooms list
                rdata_single = await ph.chesscom_room_data(slug)
                if not rdata_single:
                    raise ValueError(f"Could not fetch room data for '{slug}'")
                room_meta = rdata_single.get('room', {})
                event_name = room_meta.get('name', slug)
                rooms_to_import = [{'slug': slug, 'name': event_name, 'id': room_meta.get('id')}]

            total_imported = 0
            rooms_done = 0
            for room in rooms_to_import:
                room_slug = room.get('slug', '')
                room_name = room.get('name', room_slug)
                room_id   = room.get('id')
                if not room_slug or not room_id:
                    continue
                rdata = await ph.chesscom_room_data(room_slug) if is_info_url else rdata_single
                rounds = rdata.get('rounds', [])
                api_games = rdata.get('games', [])
                if not rounds:
                    continue
                # For multi-section events include section name; for single room just use room name
                section_name = f"{event_name} — {room_name}" if is_info_url else event_name
                round_pgns = await ph.chesscom_event_pgns(
                    room_id, rounds, api_games=api_games, event_name=section_name
                )
                for rnd_name, pgn_text in round_pgns:
                    rnum_m = re.search(r'(\d+)', rnd_name)
                    rnum = int(rnum_m.group(1)) if rnum_m else 0
                    sid = upsert_pgn_source(
                        'chesscom_event', url, section_name,
                        rnum, rnd_name, '', '', False, org_id
                    )
                    n = await ph.import_pgn_text(pgn_text, sid, insert_pgn_games,
                                                 event_override=section_name)
                    update_pgn_source_fetched(sid, count_pgn_games_for_source(sid))
                    total_imported += n
                rooms_done += 1
            msg = f"Imported {total_imported} games from {rooms_done} section(s) of '{event_name}'"

        elif source_type == 'chesscom':
            tour_url_id = ph.parse_chesscom_tournament_url(url)
            if not tour_url_id:
                raise ValueError("Could not extract tournament ID from Chess.com URL")
            tinfo = await ph.chesscom_tournament_info(tour_url_id)
            tour_name = tinfo.get('name', tour_url_id)
            rounds_list = tinfo.get('rounds', [])
            total_imported = 0
            for i, _ in enumerate(rounds_list, start=1):
                sid = upsert_pgn_source('chesscom', url, tour_name, i,
                                        f"Round {i}", '', '', False, org_id)
                raw = await ph.chesscom_round_pgn(tour_url_id, i)
                n = await ph.import_pgn_text(raw, sid, insert_pgn_games)
                update_pgn_source_fetched(sid, count_pgn_games_for_source(sid))
                total_imported += n
            msg = f"Imported {total_imported} games from Chess.com '{tour_name}'"

        elif source_type == 'chessresults':
            raw = await ph.chessresults_pgn(url)
            if not raw:
                raise ValueError("No PGN found at this chess-results URL (tournament may not have PGN available)")
            sid = upsert_pgn_source('chessresults', url, '', 0, '', '', '', False, org_id)
            n = await ph.import_pgn_text(raw, sid, insert_pgn_games)
            update_pgn_source_fetched(sid, count_pgn_games_for_source(sid))
            msg = f"Imported {n} games from chess-results"

        else:  # direct
            raw = await ph.fetch_direct_pgn(url)
            if not raw or '[Event' not in raw:
                raise ValueError("URL did not return valid PGN content")
            sid = upsert_pgn_source('direct', url, '', 0, '', '', '', False, org_id)
            n = await ph.import_pgn_text(raw, sid, insert_pgn_games)
            update_pgn_source_fetched(sid, count_pgn_games_for_source(sid))
            msg = f"Imported {n} games from direct URL"

    except Exception as exc:
        err = str(exc)

    limit = 50
    games = list_pgn_games(limit=limit, offset=0)
    total = count_pgn_games()
    return templates.TemplateResponse(request=request, name="pgn_database.html", context={
        "organizers": list_pgn_organizers(),
        "sources":    list_pgn_sources(),
        "games":      games,
        "total":      total,
        "page":       1,
        "pages":      max(1, (total + limit - 1) // limit),
        "search":     '',
        "event":      '',
        "research_only": 0,
        "events":     list_pgn_events(),
        "import_msg": msg,
        "import_err": err,
    })


@app.post("/pgn-database/refresh/{sid}")
async def pgn_refresh_source(sid: int, user: dict = Depends(require_login)):
    src = get_pgn_source(sid)
    if not src:
        raise HTTPException(404)
    try:
        if src['source_type'] == 'lichess' and src.get('lichess_round_id'):
            await ph.import_lichess_round(src['lichess_round_id'], sid, insert_pgn_games)
        elif src['source_type'] == 'chessresults':
            raw = await ph.chessresults_pgn(src['source_url'])
            await ph.import_pgn_text(raw, sid, insert_pgn_games)
        else:
            raw = await ph.fetch_direct_pgn(src['source_url'])
            await ph.import_pgn_text(raw, sid, insert_pgn_games)
        update_pgn_source_fetched(sid, count_pgn_games_for_source(sid))
    except Exception as exc:
        raise HTTPException(500, str(exc))
    return RedirectResponse("/pgn-database", status_code=303)


@app.post("/pgn-database/delete-source/{sid}")
def pgn_delete_source(sid: int, user: dict = Depends(require_login)):
    delete_pgn_source(sid)
    return RedirectResponse("/pgn-database", status_code=303)


@app.get("/pgn-database/download")
def pgn_download(
    search: str = '',
    event: str = '',
    research_only: int = 0,
    user: dict = Depends(require_login),
):
    raw = download_pgn_games(search=search, event=event, research_only=bool(research_only))
    fname = "games.pgn"
    if event:
        fname = event.replace(' ', '_')[:40] + ".pgn"
    elif search:
        fname = search.replace(' ', '_')[:40] + ".pgn"
    return Response(
        content=raw,
        media_type="application/x-chess-pgn",
        headers={"Content-Disposition": f'attachment; filename="{fname}"'},
    )


@app.post("/pgn-database/harvest-organizer/{oid}")
async def pgn_harvest_organizer(oid: int, user: dict = Depends(require_login)):
    """Discover and import all broadcasts by this organizer's Lichess ID."""
    organizers = list_pgn_organizers()
    org = next((o for o in organizers if o['id'] == oid), None)
    if not org:
        raise HTTPException(404)
    lichess_id = org.get('lichess_id', '')
    if not lichess_id:
        return RedirectResponse("/pgn-database?err=No+Lichess+ID+set", status_code=303)

    tour_listings = await ph.lichess_organizer_broadcasts(lichess_id)
    discovered = 0
    for bc in tour_listings:
        tour = bc.get('tour', {})
        tour_id = tour.get('id', '')
        if not tour_id:
            continue
        binfo = await ph.lichess_broadcast_info(tour_id)
        tour_name = binfo.get('name') or tour.get('name', '')
        for rd in binfo.get('rounds', []):
            rid = rd.get('id', '')
            if not rid or lichess_round_already_known(rid):
                continue
            rname   = rd.get('name', '')
            rd_live = not rd.get('finished', True)
            rnum_m  = re.search(r'(\d+)', rname)
            rnum    = int(rnum_m.group(1)) if rnum_m else 0
            rd_url  = rd.get('url', f"https://lichess.org/broadcast/{tour_id}/{rid}")
            sid = upsert_pgn_source('lichess', rd_url, tour_name, rnum,
                                    rname, tour_id, rid, rd_live, oid, auto_discovered=True)
            await ph.import_lichess_round(rid, sid, insert_pgn_games, rd_url)
            update_pgn_source_fetched(sid, count_pgn_games_for_source(sid))
            discovered += 1

    return RedirectResponse(f"/pgn-database?msg=Discovered+{discovered}+rounds", status_code=303)


@app.get("/pgn-database/game/{gid}")
def pgn_game_raw(gid: int, user: dict = Depends(require_login)):
    raw = get_pgn_game_raw(gid)
    if raw is None:
        raise HTTPException(404, "Game not found")
    return Response(content=raw, media_type="text/plain")


# Background harvest task

async def _refresh_live_sources():
    """One pass over every live PGN source, re-fetching its latest games.
    Extracted out of _harvest_loop so it can be exercised directly in tests
    without the loop's sleep/counter wrapper. Each source's failure is caught
    and logged individually so one bad source can't stop the rest."""
    for src in list_live_pgn_sources():
        try:
            if src['source_type'] == 'lichess' and src.get('lichess_round_id'):
                await ph.import_lichess_round(
                    src['lichess_round_id'], src['id'], insert_pgn_games,
                    src.get('source_url', '')
                )
                update_pgn_source_fetched(src['id'], count_pgn_games_for_source(src['id']))
        except Exception as e:
            logging.warning("harvest live source %s: %s", src['id'], e)


async def _discover_new_broadcasts():
    """One pass over every tracked organizer, registering and importing any
    Lichess broadcast round not already known. Extracted out of
    _harvest_loop for the same testability reason as _refresh_live_sources."""
    for org in list_pgn_organizers():
        if not org.get('lichess_id'):
            continue
        try:
            tour_listings = await ph.lichess_organizer_broadcasts(org['lichess_id'])
            for bc in tour_listings:
                tour = bc.get('tour', {})
                tour_id = tour.get('id', '')
                if not tour_id:
                    continue
                binfo = await ph.lichess_broadcast_info(tour_id)
                tour_name = binfo.get('name') or tour.get('name', '')
                for rd in binfo.get('rounds', []):
                    rid = rd.get('id', '')
                    if not rid or lichess_round_already_known(rid):
                        continue
                    rname   = rd.get('name', '')
                    rd_live = not rd.get('finished', True)
                    rnum_m  = re.search(r'(\d+)', rname)
                    rnum    = int(rnum_m.group(1)) if rnum_m else 0
                    rd_url  = rd.get('url', f"https://lichess.org/broadcast/{tour_id}/{rid}")
                    sid = upsert_pgn_source(
                        'lichess', rd_url, tour_name, rnum,
                        rname, tour_id, rid, rd_live, org['id'], auto_discovered=True
                    )
                    await ph.import_lichess_round(rid, sid, insert_pgn_games, rd_url)
                    update_pgn_source_fetched(sid, count_pgn_games_for_source(sid))
        except Exception as e:
            logging.warning("harvest organizer %s: %s", org['lichess_id'], e)


async def _harvest_loop():
    """Refresh live sources every 60 s; discover new organizer broadcasts every 10 min."""
    counter = 0
    while True:
        await asyncio.sleep(60)
        counter += 1
        try:
            await _refresh_live_sources()
            if counter % 10 == 0:
                await _discover_new_broadcasts()
            if counter % 1440 == 0:
                await _twic_auto_check()
        except Exception as e:
            logging.warning("harvest loop: %s", e)


@app.on_event("startup")
async def start_harvest_loop():
    asyncio.create_task(_harvest_loop())


# ---------------------------------------------------------------------------
# Coaching: dashboards, students, sessions, homework
# ---------------------------------------------------------------------------

_HOMEWORK_KINDS = ("study", "puzzle", "exercise")


def _is_coach(user: dict) -> bool:
    return user.get("role") in ("coach", "admin")


def _platform_fee_percent() -> int:
    try:
        return max(0, min(100, int(get_setting("platform_fee_percent", "15"))))
    except ValueError:
        return 15


def _form_int_list(form, key: str) -> List[int]:
    out = []
    for v in form.getlist(key):
        try:
            out.append(int(v))
        except (TypeError, ValueError):
            pass
    return out


def _coach_student_ids(form, coach_id: int) -> List[int]:
    """Selected students, restricted to the coach's own; none selected means everyone."""
    allowed = {s["id"] for s in list_coach_students(coach_id)}
    chosen = [s for s in _form_int_list(form, "student_ids") if s in allowed]
    return chosen or sorted(allowed)


def _parse_solution(text: str) -> str:
    moves = []
    for tok in (text or "").replace("...", " ").split():
        tok = re.sub(r"^\d+\.+", "", tok)
        if tok and tok not in ("1-0", "0-1", "1/2-1/2", "*"):
            moves.append(tok)
    return " ".join(moves)


def _norm_san(s: str) -> str:
    return re.sub(r"[+#!?\s]", "", s or "")


_FEN_IMAGE_PROMPT = (
    "This image is a photo or screenshot of a chess position: a physical board, a printed diagram, "
    "or a chess app/website. Read the position and output ONLY the piece-placement field of the FEN "
    "(ranks 8 to 1 separated by '/', uppercase letters for White, lowercase for Black, digits for "
    "consecutive empty squares). Orient the board with White at the bottom: use any coordinates or "
    "labels visible in the image; otherwise assume the side nearest the bottom of the image is White. "
    "If no chessboard is visible, output exactly NONE. Output nothing else."
)
_BOARD_IMAGE_MEDIA_TYPES = {"image/png", "image/jpeg", "image/webp", "image/gif"}
_MAX_BOARD_IMAGE_BYTES = 10 * 1024 * 1024


def _valid_fen_placement(placement: str) -> bool:
    ranks = placement.split("/")
    if len(ranks) != 8:
        return False
    kings = {"K": 0, "k": 0}
    for rank in ranks:
        total = 0
        for ch in rank:
            if ch in "12345678":
                total += int(ch)
            elif ch in "pnbrqkPNBRQK":
                total += 1
                if ch in kings:
                    kings[ch] += 1
            else:
                return False
        if total != 8:
            return False
    return kings["K"] == 1 and kings["k"] == 1


def _claude_auth_mode() -> str:
    """'api_key', 'federation', or '' when Claude is not configured.

    A static key wins over federation, matching the SDK's own credential precedence.
    """
    if ANTHROPIC_API_KEY:
        return "api_key"
    if ANTHROPIC_FEDERATION_RULE_ID and ANTHROPIC_ORGANIZATION_ID:
        return "federation"
    return ""


def _fly_transport() -> httpx.BaseTransport:
    return httpx.HTTPTransport(uds=FLY_API_SOCKET)


def _fly_identity_token() -> str:
    """Mint a fresh OIDC JWT for this Fly machine.

    Minted on every call rather than cached: Anthropic rejects a JWT whose jti was
    already exchanged, and the SDK calls this only when it needs a new access token.
    """
    body = {"aud": ANTHROPIC_IDENTITY_TOKEN_AUDIENCE} if ANTHROPIC_IDENTITY_TOKEN_AUDIENCE else {}
    with httpx.Client(transport=_fly_transport(), timeout=10) as client:
        resp = client.post("http://localhost/v1/tokens/oidc", json=body)
        resp.raise_for_status()
    token = resp.text.strip()
    if token.startswith("{"):
        payload = resp.json()
        token = next((payload[k] for k in ("token", "jwt", "id_token", "access_token")
                      if isinstance(payload.get(k), str)), "")
    if token.count(".") != 2:
        raise RuntimeError("Fly OIDC endpoint did not return a JWT")
    global _last_fly_token_claims
    _last_fly_token_claims = _jwt_claims_summary(token)
    logging.info("Fly identity token minted: %s", _last_fly_token_claims)
    return token


_last_fly_token_claims = ""


def _jwt_claims_summary(token: str) -> str:
    """iss/sub/aud/app_name of a JWT (unverified, no signature) for diagnosing federation rule mismatches."""
    import base64
    try:
        payload = token.split(".")[1]
        claims = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
        return json.dumps({k: claims.get(k) for k in ("iss", "sub", "aud", "app_name", "exp")})
    except Exception:
        return "<undecodable>"


def _identity_token_provider():
    if ANTHROPIC_IDENTITY_TOKEN_FILE:
        return anthropic.IdentityTokenFile(ANTHROPIC_IDENTITY_TOKEN_FILE)
    return _fly_identity_token


def _federation_credentials() -> "anthropic.WorkloadIdentityCredentials":
    return anthropic.WorkloadIdentityCredentials(
        identity_token_provider=_identity_token_provider(),
        federation_rule_id=ANTHROPIC_FEDERATION_RULE_ID,
        organization_id=ANTHROPIC_ORGANIZATION_ID,
        service_account_id=ANTHROPIC_SERVICE_ACCOUNT_ID or None,
        workspace_id=ANTHROPIC_WORKSPACE_ID or None,
    )


_claude_client = None
_claude_client_config = None


def _anthropic_client():
    """Claude API client, cached so a federated access token is reused until it expires."""
    global _claude_client, _claude_client_config
    config = (ANTHROPIC_API_KEY, ANTHROPIC_WORKSPACE_ID, ANTHROPIC_FEDERATION_RULE_ID, ANTHROPIC_ORGANIZATION_ID,
              ANTHROPIC_SERVICE_ACCOUNT_ID, ANTHROPIC_IDENTITY_TOKEN_FILE)
    if _claude_client is not None and _claude_client_config == config:
        return _claude_client
    if _claude_auth_mode() == "federation":
        client = anthropic.Anthropic(credentials=_federation_credentials())
    else:
        headers = {"anthropic-workspace-id": ANTHROPIC_WORKSPACE_ID} if ANTHROPIC_WORKSPACE_ID else None
        client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY, default_headers=headers)
    _claude_client, _claude_client_config = client, config
    return client


def _fen_from_image(data: bytes, media_type: str) -> Optional[str]:
    """Ask Claude to read a board image; returns the FEN placement, or None if no board was found."""
    import base64

    client = _anthropic_client()
    response = client.beta.messages.create(
        model="claude-opus-5-5",
        max_tokens=8000,
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
        output_config={"effort": "high"},
        messages=[{"role": "user", "content": [
            {"type": "image", "source": {"type": "base64", "media_type": media_type,
                                         "data": base64.standard_b64encode(data).decode("ascii")}},
            {"type": "text", "text": _FEN_IMAGE_PROMPT},
        ]}],
    )
    if response.stop_reason == "refusal":
        return None
    text = "".join(block.text for block in response.content if block.type == "text")
    match = re.search(r"[pnbrqkPNBRQK1-8]+(?:/[pnbrqkPNBRQK1-8]+){7}", text)
    return match.group(0) if match else None


@app.post("/api/coaching/board-from-image")
async def coaching_board_from_image(image: UploadFile = File(...), user: dict = Depends(require_coach)):
    if not _claude_auth_mode():
        raise HTTPException(status_code=503, detail="Reading positions from images is not configured on this server")
    media_type = (image.content_type or "").lower()
    if media_type == "image/jpg":
        media_type = "image/jpeg"
    if media_type not in _BOARD_IMAGE_MEDIA_TYPES:
        raise HTTPException(status_code=415, detail="Please use a PNG, JPEG, WebP or GIF image")
    data = await image.read()
    if not data or len(data) > _MAX_BOARD_IMAGE_BYTES:
        raise HTTPException(status_code=413, detail="Image must be between 1 byte and 10 MB")
    try:
        placement = await asyncio.get_event_loop().run_in_executor(None, _fen_from_image, data, media_type)
    except Exception as e:
        detail = f" [last identity token: {_last_fly_token_claims}]" if _claude_auth_mode() == "federation" else ""
        logging.warning("board-from-image for user %s: %s%s", user["id"], e, detail)
        raise HTTPException(status_code=502, detail="Could not read the image right now - please try again")
    if not placement or not _valid_fen_placement(placement):
        raise HTTPException(status_code=422, detail="Couldn't find a readable chessboard in that image")
    return {"fen": f"{placement} w - - 0 1"}


def _owns_homework(hw: dict, user: dict) -> bool:
    return hw["coach_id"] == user["id"] or user.get("role") == "admin"


@app.get("/coaching", response_class=HTMLResponse)
def coaching_page(request: Request, msg: Optional[str] = None, user: dict = Depends(require_login)):
    uid = user["id"]
    if _is_coach(user):
        return templates.TemplateResponse(request=request, name="coaching_coach.html", context={
            "profile": get_coach_profile(uid),
            "students": list_coach_students(uid),
            "sessions": list_coach_sessions(uid),
            "homework": list_coach_homework(uid),
            "courses": list_coach_courses(uid),
            "stripe_configured": bool(STRIPE_SECRET_KEY),
            "platform_fee": _platform_fee_percent(),
            "invite": request.session.pop("coach_invite", None),
            "msg": msg,
        })
    return templates.TemplateResponse(request=request, name="coaching_student.html", context={
        "coaches": list_coaches_for_student(uid),
        "sessions": list_student_sessions(uid),
        "homework": list_student_homework(uid),
        "enrollments": list_user_enrollments(uid),
        "msg": msg,
    })


@app.post("/coaching/profile")
def coaching_profile_save(default_link: str = Form(""), bio: str = Form(""), user: dict = Depends(require_coach)):
    update_coach_profile(user["id"], default_link, bio)
    return RedirectResponse("/coaching?msg=profile", status_code=303)


@app.post("/coaching/students")
def coaching_add_student(username: str = Form(...), user: dict = Depends(require_coach)):
    target = get_user_by_username(username)
    if not target:
        return RedirectResponse("/coaching?msg=nouser", status_code=303)
    if target["id"] == user["id"]:
        return RedirectResponse("/coaching?msg=self", status_code=303)
    add_coach_student(user["id"], target["id"])
    return RedirectResponse("/coaching?msg=student", status_code=303)


@app.get("/api/coaching/student-search", response_class=HTMLResponse)
def coaching_student_search(request: Request, q: str = "", user: dict = Depends(require_coach)):
    q = q.strip()
    if len(q) < 2:
        return HTMLResponse("")
    existing = {s["id"] for s in list_coach_students(user["id"])}
    matches = [dict(u, already=u["id"] in existing) for u in search_users(q) if u["id"] != user["id"]]
    is_email = "@" in q
    return templates.TemplateResponse(request=request, name="fragments/student_search.html", context={
        "q": q, "matches": matches,
        "suggest_username": "" if is_email else q,
        "suggest_email": q if is_email else "",
    })


@app.post("/coaching/students/invite")
async def coaching_invite_student(request: Request, username: str = Form(...), email: str = Form(...),
                                  user: dict = Depends(require_coach)):
    username, email = username.strip(), email.strip().lower()
    if not username or "@" not in email:
        return RedirectResponse("/coaching?msg=invite_invalid", status_code=303)
    if get_user_by_username(username) or get_user_by_email(email):
        return RedirectResponse("/coaching?msg=invite_exists", status_code=303)
    uid = create_user(username, uuid.uuid4().hex, role="viewer", status="active")
    update_user_contact(uid, email, None)
    add_coach_student(user["id"], uid)
    token = create_password_reset_token(uid, hours=72)
    link = f"{str(request.base_url).rstrip('/')}/reset-password?token={token}"
    sent = True
    try:
        await send_student_invite_email(email, user["username"], username, link)
    except Exception as e:
        logging.warning("student invite email to %s: %s", email, e)
        sent = False
    request.session["coach_invite"] = {"username": username, "email": email, "link": link, "sent": sent}
    return RedirectResponse("/coaching?msg=invited", status_code=303)


@app.post("/coaching/students/{sid}/remove")
def coaching_remove_student(sid: int, user: dict = Depends(require_coach)):
    remove_coach_student(user["id"], sid)
    return RedirectResponse("/coaching", status_code=303)


@app.post("/coaching/sessions")
async def coaching_create_session(request: Request, user: dict = Depends(require_coach)):
    form = await request.form()
    title = (form.get("title") or "").strip()
    starts_at = (form.get("starts_at") or "").strip()
    if not title or not starts_at:
        return RedirectResponse("/coaching?msg=session_invalid", status_code=303)
    create_coach_session(user["id"], title, starts_at, form.get("link"), form.get("notes"),
                         _coach_student_ids(form, user["id"]))
    return RedirectResponse("/coaching?msg=session", status_code=303)


@app.post("/coaching/sessions/{sid}/delete")
def coaching_delete_session(sid: int, user: dict = Depends(require_coach)):
    delete_coach_session(sid, user["id"])
    return RedirectResponse("/coaching", status_code=303)


@app.post("/coaching/homework")
async def coaching_create_homework(request: Request, user: dict = Depends(require_coach)):
    form = await request.form()
    title = (form.get("title") or "").strip()
    kind = form.get("kind") if form.get("kind") in _HOMEWORK_KINDS else "study"
    fen = (form.get("fen") or "").strip()
    solution = _parse_solution(form.get("solution") or "")
    if not title:
        return RedirectResponse("/coaching?msg=homework_invalid", status_code=303)
    if kind == "puzzle" and not (fen and solution):
        return RedirectResponse("/coaching?msg=puzzle_invalid", status_code=303)
    hid = create_homework(user["id"], title, form.get("instructions"), kind, fen, form.get("pgn"),
                          solution if kind == "puzzle" else None, form.get("due_date"),
                          _coach_student_ids(form, user["id"]))
    return RedirectResponse(f"/coaching/homework/{hid}", status_code=303)


@app.get("/coaching/homework/{hid}", response_class=HTMLResponse)
def coaching_homework_detail(request: Request, hid: int, msg: Optional[str] = None,
                             user: dict = Depends(require_login)):
    hw = get_homework(hid)
    if not hw:
        raise HTTPException(404)
    is_owner = _owns_homework(hw, user)
    assignment = None
    if not is_owner:
        assignment = get_assignment(hid, user["id"])
        if not assignment:
            raise HTTPException(status_code=403, detail="This homework is not assigned to you")
        hw = dict(hw, solution=None)
    return templates.TemplateResponse(request=request, name="coaching_homework.html", context={
        "hw": hw,
        "is_owner": is_owner,
        "assignment": assignment,
        "assignments": list_homework_assignments(hid) if is_owner else [],
        "coach": get_coach_profile(hw["coach_id"]),
        "msg": msg,
    })


@app.post("/coaching/homework/{hid}/delete")
def coaching_delete_homework(hid: int, user: dict = Depends(require_coach)):
    hw = get_homework(hid)
    if hw and _owns_homework(hw, user):
        delete_homework(hid, hw["coach_id"])
    return RedirectResponse("/coaching", status_code=303)


@app.post("/coaching/homework/{hid}/submit")
async def coaching_submit_homework(request: Request, hid: int, user: dict = Depends(require_login)):
    if not get_homework(hid):
        raise HTTPException(404)
    form = await request.form()
    pgn = form.get("pgn") or ""
    upload = form.get("pgn_file")
    if upload is not None and hasattr(upload, "read"):
        data = await upload.read()
        if data:
            pgn = data.decode("utf-8", errors="replace")
    if not submit_assignment(hid, user["id"], form.get("note"), pgn):
        raise HTTPException(status_code=403, detail="This homework is not assigned to you")
    return RedirectResponse(f"/coaching/homework/{hid}?msg=submitted", status_code=303)


@app.post("/coaching/homework/{hid}/grade")
def coaching_grade_homework(hid: int, assignment_id: int = Form(...), grade: str = Form(""),
                            feedback: str = Form(""), user: dict = Depends(require_coach)):
    hw = get_homework(hid)
    if not hw or not _owns_homework(hw, user):
        raise HTTPException(status_code=403, detail="Not your homework")
    grade_assignment(assignment_id, hw["coach_id"], grade, feedback)
    return RedirectResponse(f"/coaching/homework/{hid}?msg=graded", status_code=303)


@app.post("/api/coaching/puzzle-move")
async def coaching_puzzle_move(request: Request, user: dict = Depends(require_login)):
    """Server-side puzzle checking so the solution never reaches the student's browser."""
    body = await request.json()
    try:
        hid, ply = int(body.get("hid")), int(body.get("ply"))
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="hid and ply required")
    hw = get_homework(hid)
    if not hw or hw["kind"] != "puzzle" or not hw["solution"]:
        raise HTTPException(404)
    is_owner = _owns_homework(hw, user)
    if not is_owner and not get_assignment(hid, user["id"]):
        raise HTTPException(status_code=403, detail="This homework is not assigned to you")
    solution = hw["solution"].split()
    if ply < 0 or ply >= len(solution):
        return {"correct": False, "reply": None, "solved": True}
    if _norm_san(solution[ply]) != _norm_san(str(body.get("san") or "")):
        return {"correct": False, "reply": None, "solved": False}
    reply = solution[ply + 1] if ply + 1 < len(solution) else None
    solved = ply + 2 >= len(solution)
    if solved and not is_owner:
        mark_puzzle_solved(hid, user["id"])
    return {"correct": True, "reply": reply, "solved": solved}


# ---------------------------------------------------------------------------
# Coaching: courses, enrollment payments (Stripe Connect), coach payouts
# ---------------------------------------------------------------------------

@app.post("/coaching/courses")
def coaching_create_course(title: str = Form(...), description: str = Form(""), price: str = Form("0"),
                           user: dict = Depends(require_coach)):
    try:
        cents = int(round(float(price) * 100))
    except ValueError:
        cents = 0
    if not title.strip():
        return RedirectResponse("/coaching?msg=course_invalid", status_code=303)
    create_course(user["id"], title, description, cents)
    return RedirectResponse("/coaching?msg=course", status_code=303)


@app.post("/coaching/courses/{cid}/toggle")
def coaching_toggle_course(cid: int, user: dict = Depends(require_coach)):
    course = get_course(cid)
    if course and course["coach_id"] == user["id"]:
        set_course_active(cid, user["id"], not course["active"])
    return RedirectResponse("/coaching", status_code=303)


def _stripe_connect_onboarding_url(request: Request, user: dict) -> str:
    stripe.api_key = STRIPE_SECRET_KEY
    profile = get_coach_profile(user["id"])
    account_id = profile.get("stripe_account_id")
    if not account_id:
        account = stripe.Account.create(
            type="express",
            email=profile.get("email") or None,
            capabilities={"card_payments": {"requested": True}, "transfers": {"requested": True}},
        )
        account_id = account.id
        set_coach_stripe_account(user["id"], account_id, False)
    base_url = str(request.base_url).rstrip("/")
    link = stripe.AccountLink.create(
        account=account_id,
        refresh_url=f"{base_url}/coaching/stripe/refresh",
        return_url=f"{base_url}/coaching/stripe/return",
        type="account_onboarding",
    )
    return link.url


@app.post("/coaching/stripe/connect")
def coaching_stripe_connect(request: Request, user: dict = Depends(require_coach)):
    if not STRIPE_SECRET_KEY:
        return RedirectResponse("/coaching?msg=stripe_unconfigured", status_code=303)
    return RedirectResponse(_stripe_connect_onboarding_url(request, user), status_code=303)


@app.get("/coaching/stripe/refresh")
def coaching_stripe_refresh(request: Request, user: dict = Depends(require_coach)):
    if not STRIPE_SECRET_KEY:
        return RedirectResponse("/coaching?msg=stripe_unconfigured", status_code=303)
    return RedirectResponse(_stripe_connect_onboarding_url(request, user), status_code=303)


@app.get("/coaching/stripe/return")
def coaching_stripe_return(user: dict = Depends(require_coach)):
    profile = get_coach_profile(user["id"])
    account_id = profile.get("stripe_account_id")
    if account_id and STRIPE_SECRET_KEY:
        stripe.api_key = STRIPE_SECRET_KEY
        try:
            account = stripe.Account.retrieve(account_id)
            set_coach_stripe_account(user["id"], account_id, bool(account.charges_enabled))
        except Exception as e:
            logging.warning("stripe connect return for user %s: %s", user["id"], e)
    return RedirectResponse("/coaching?msg=stripe", status_code=303)


def _complete_enrollment(eid: int, stripe_session_id: Optional[str]):
    enrollment = get_enrollment(eid)
    if not enrollment:
        return
    if enrollment["status"] != "paid":
        mark_enrollment_paid(eid, stripe_session_id)
    course = get_course(enrollment["course_id"])
    if course:
        add_coach_student(course["coach_id"], enrollment["user_id"])


@app.get("/courses", response_class=HTMLResponse)
def courses_page(request: Request):
    return templates.TemplateResponse(request=request, name="courses.html", context={
        "courses": list_courses(),
        "user": get_current_user(request),
    })


@app.get("/courses/{cid}", response_class=HTMLResponse)
def course_detail_page(request: Request, cid: int, cancelled: Optional[str] = None, error: Optional[str] = None):
    course = get_course(cid)
    user = get_current_user(request)
    is_owner = bool(user) and (course or {}).get("coach_id") == user["id"]
    if not course or (not course["active"] and not is_owner and (user or {}).get("role") != "admin"):
        raise HTTPException(404)
    enrollment = get_user_enrollment(cid, user["id"]) if user else None
    payments_ready = course["price_cents"] <= 0 or (
        bool(STRIPE_SECRET_KEY) and bool(course["stripe_account_id"]) and bool(course["stripe_charges_enabled"])
    )
    return templates.TemplateResponse(request=request, name="course_detail.html", context={
        "course": course, "user": user, "is_owner": is_owner, "enrollment": enrollment,
        "payments_ready": payments_ready, "cancelled": cancelled == "1", "error": error,
    })


@app.post("/courses/{cid}/enroll")
def course_enroll(request: Request, cid: int, user: dict = Depends(require_login)):
    course = get_course(cid)
    if not course or not course["active"]:
        raise HTTPException(404)
    if course["coach_id"] == user["id"]:
        return RedirectResponse(f"/courses/{cid}?error=own", status_code=303)
    if get_user_enrollment(cid, user["id"]):
        return RedirectResponse("/coaching", status_code=303)
    price = course["price_cents"]
    if price <= 0:
        create_enrollment(cid, user["id"], "free", 0)
        add_coach_student(course["coach_id"], user["id"])
        return RedirectResponse(f"/courses/{cid}/enroll/success", status_code=303)
    if not STRIPE_SECRET_KEY or not course["stripe_account_id"] or not course["stripe_charges_enabled"]:
        return RedirectResponse(f"/courses/{cid}?error=payments", status_code=303)

    eid = create_enrollment(cid, user["id"], "pending", price)
    stripe.api_key = STRIPE_SECRET_KEY
    base_url = str(request.base_url).rstrip("/")
    email = (get_user_by_username(user["username"]) or {}).get("email")
    session = stripe.checkout.Session.create(
        payment_method_types=["card"],
        line_items=[{"price_data": {"currency": "usd",
            "product_data": {"name": f"Course: {course['title']} (coach {course['coach']})"},
            "unit_amount": price}, "quantity": 1}],
        mode="payment",
        success_url=f"{base_url}/courses/{cid}/enroll/success?session_id={{CHECKOUT_SESSION_ID}}",
        cancel_url=f"{base_url}/courses/{cid}?cancelled=1",
        customer_email=email or None,
        metadata={"enrollment_id": str(eid), "course_id": str(cid)},
        payment_intent_data={
            "application_fee_amount": price * _platform_fee_percent() // 100,
            "transfer_data": {"destination": course["stripe_account_id"]},
        },
    )
    return RedirectResponse(session.url, status_code=303)


@app.get("/courses/{cid}/enroll/success", response_class=HTMLResponse)
def course_enroll_success(request: Request, cid: int, session_id: Optional[str] = None,
                          user: dict = Depends(require_login)):
    course = get_course(cid)
    if not course:
        raise HTTPException(404)
    if session_id and STRIPE_SECRET_KEY:
        stripe.api_key = STRIPE_SECRET_KEY
        try:
            s = stripe.checkout.Session.retrieve(session_id)
            if s.payment_status == "paid":
                enrollment = get_enrollment(int((s.metadata or {}).get("enrollment_id") or 0))
                if enrollment and enrollment["user_id"] == user["id"] and enrollment["course_id"] == cid:
                    _complete_enrollment(enrollment["id"], session_id)
        except Exception as e:
            logging.warning("course enroll success check: %s", e)
    return templates.TemplateResponse(request=request, name="course_enroll_success.html", context={
        "course": course,
        "enrollment": get_user_enrollment(cid, user["id"]),
    })


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
