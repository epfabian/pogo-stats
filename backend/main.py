"""
FastAPI backend for the PoGo Stats web UI.

Reads exclusively from the SQLite DB that the Discord bot populates. Also
serves Pokemon sprites, which are downloaded once from the free PokeAPI
sprites repo (https://github.com/PokeAPI/sprites) and cached locally - no
repeated fetching from an external source needed.

Start (from the project root, with the venv activated):
    uvicorn backend.main:app --host 0.0.0.0 --port 8000

Two optional environment variables let the dashboard be exposed beyond a
trusted LAN (see .env.example):
  - DASHBOARD_USER / DASHBOARD_PASSWORD enable an HTTP Basic Auth prompt
    (the browser's native login popup, Sonarr-style). Leave both blank to
    keep the original no-auth behavior.
  - URL_BASE serves the whole app under a sub-path (e.g. /pogo) so it can
    sit behind a reverse proxy at DOMAIN/pogo. Leave blank to serve at root.
"""

import asyncio
import base64
import csv
import hashlib
import hmac
import io
import os
import secrets
import time
from datetime import date
from pathlib import Path
from typing import Optional

import httpx
from dotenv import load_dotenv
from fastapi import APIRouter, FastAPI, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from timezonefinder import TimezoneFinder

from shared import db

# The bot loads .env on its own; the backend didn't before it had any config of
# its own. Load it here too so DASHBOARD_USER/DASHBOARD_PASSWORD/URL_BASE can be
# set in the same .env file rather than the process environment.
load_dotenv()

BASE_DIR = Path(__file__).resolve().parent.parent
STATIC_DIR = Path(__file__).resolve().parent / "static"
SPRITE_CACHE = BASE_DIR / "data" / "sprites"
SPRITE_URL = "https://raw.githubusercontent.com/PokeAPI/sprites/master/sprites/pokemon/{id}.png"
SHINY_SPRITE_URL = "https://raw.githubusercontent.com/PokeAPI/sprites/master/sprites/pokemon/shiny/{id}.png"

# How often the background task re-runs the DB backup while the server stays
# up. The backup itself keeps the last 7 daily copies (see db.backup_db), so
# a once-a-day cadence gives a rolling week of restore points. Without this
# the "daily" backup only ever ran once, at startup - useless on a machine
# that stays up for weeks (see the periodic task below).
BACKUP_INTERVAL_SECONDS = 24 * 60 * 60


def _normalize_url_base(raw):
    """Turns a raw URL_BASE env value into a canonical prefix: empty string
    for "serve at root", otherwise exactly one leading slash and no trailing
    slash (so "pogo", "/pogo" and "/pogo/" all become "/pogo")."""
    raw = (raw or "").strip().strip("/")
    return "/" + raw if raw else ""


URL_BASE = _normalize_url_base(os.environ.get("URL_BASE", ""))

# Basic Auth is active only when BOTH are set to a non-empty value; leaving
# either blank keeps the original trusted-LAN behavior (no auth at all).
AUTH_USER = os.environ.get("DASHBOARD_USER", "")
AUTH_PASS = os.environ.get("DASHBOARD_PASSWORD", "")

# After a correct login, a signed session cookie is issued so the browser
# stays logged in (no popup) for this long - even across browser restarts -
# instead of only for as long as it happens to cache the Basic Auth header.
SESSION_COOKIE = "pogo_session"
SESSION_TTL_SECONDS = 24 * 60 * 60

app = FastAPI(title="PoGo Stats API")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


def _check_basic_auth(header):
    """Constant-time check of an `Authorization: Basic ...` header value
    against the configured credentials. Returns False for anything missing,
    malformed, or non-matching."""
    if not header or not header.startswith("Basic "):
        return False
    try:
        decoded = base64.b64decode(header[6:]).decode("utf-8", "replace")
    except Exception:
        return False
    user, sep, password = decoded.partition(":")
    if not sep:
        return False
    # compare_digest on both parts so timing doesn't leak which one was wrong.
    user_ok = secrets.compare_digest(user, AUTH_USER)
    pass_ok = secrets.compare_digest(password, AUTH_PASS)
    return user_ok and pass_ok


def _session_key():
    """HMAC key for the session cookie, derived from the credentials so that
    changing the password automatically invalidates every cookie issued under
    the old one. Never leaves the server."""
    return (AUTH_USER + ":" + AUTH_PASS).encode("utf-8")


def _sign_session(expiry_str):
    return hmac.new(_session_key(), expiry_str.encode("utf-8"), hashlib.sha256).hexdigest()


def _make_session_token(expiry):
    expiry_str = str(expiry)
    return expiry_str + "." + _sign_session(expiry_str)


def _valid_session_cookie(token):
    """True only if `token` is a cookie this server signed and it hasn't
    expired - so it can't be forged without the password-derived key."""
    if not token or "." not in token:
        return False
    expiry_str, _, sig = token.partition(".")
    try:
        if int(expiry_str) < int(time.time()):
            return False
    except ValueError:
        return False
    return hmac.compare_digest(sig, _sign_session(expiry_str))


@app.middleware("http")
async def basic_auth_middleware(request: Request, call_next):
    """Gates every request (API, sprites, AND the static frontend) behind HTTP
    Basic Auth when credentials are configured. Implemented as middleware
    rather than a per-route dependency precisely so it also covers the
    StaticFiles mount, which dependencies can't reach.

    A correct Basic Auth login (401 + WWW-Authenticate is what triggers the
    browser's native popup) also mints a signed, day-long session cookie, so
    the user isn't re-prompted on every browser restart - a valid cookie alone
    is accepted on later requests without any credentials."""
    if AUTH_USER and AUTH_PASS:
        cookie_ok = _valid_session_cookie(request.cookies.get(SESSION_COOKIE))
        header_ok = False if cookie_ok else _check_basic_auth(request.headers.get("Authorization"))
        if not cookie_ok and not header_ok:
            return Response(
                status_code=401,
                headers={"WWW-Authenticate": 'Basic realm="PoGo Stats"'},
            )
        response = await call_next(request)
        # Issue/refresh the session cookie only on a fresh password login, so
        # the 24h window starts at login and subsequent requests ride the
        # cookie instead of re-sending credentials.
        if header_ok:
            expiry = int(time.time()) + SESSION_TTL_SECONDS
            response.set_cookie(
                SESSION_COOKIE,
                _make_session_token(expiry),
                max_age=SESSION_TTL_SECONDS,
                httponly=True,
                samesite="lax",
                path=URL_BASE or "/",
            )
        return response
    return await call_next(request)


# Built once at import time (loads its offline shapefile-derived lookup data
# into memory) and reused for every request - constructing it per-request
# would be needlessly slow. No network calls, no API key.
_tzfinder = TimezoneFinder()

# Shared across every sprite request instead of opening a new httpx client
# (and its own connection pool) per request - created on startup, closed on
# shutdown, see below.
_http_client: Optional[httpx.AsyncClient] = None

# Handle to the recurring backup task, so it can be cancelled on shutdown.
_backup_task: Optional[asyncio.Task] = None


async def _periodic_backup():
    """Re-runs the SQLite backup once a day for as long as the server is up.
    db.backup_db is blocking (SQLite's online backup API), so it's pushed to a
    worker thread to avoid stalling the event loop. Any failure is logged and
    swallowed - a backup problem must never kill the loop or the server."""
    while True:
        await asyncio.sleep(BACKUP_INTERVAL_SECONDS)
        try:
            await asyncio.to_thread(db.backup_db)
        except Exception as exc:
            print("Warning: periodic DB backup failed:", exc)


@app.on_event("startup")
async def startup():
    global _http_client, _backup_task
    db.init_db()
    SPRITE_CACHE.mkdir(parents=True, exist_ok=True)
    _http_client = httpx.AsyncClient()
    # Daily backup so that if the DB ever gets corrupted (see the incident
    # with the raids table), at most one day of data is lost. Runs once here
    # AND on a daily timer (see _periodic_backup) so a long-running server
    # keeps producing fresh backups, not just one at boot.
    try:
        db.backup_db()
    except Exception as exc:  # Backup must never block startup.
        print("Warning: DB backup failed on startup:", exc)
    _backup_task = asyncio.create_task(_periodic_backup())


@app.on_event("shutdown")
async def shutdown():
    if _backup_task is not None:
        _backup_task.cancel()
    if _http_client is not None:
        await _http_client.aclose()


# Every API/sprite route lives on this router, which is then mounted under
# URL_BASE (empty = root). That's what makes DOMAIN/pogo/api/... work without
# touching a single route decorator.
router = APIRouter()


@router.get("/api/summary")
def summary(tz: str = "UTC"):
    return db.get_summary(tz=tz)


@router.get("/api/rolling/summary")
def rolling_summary(hours: int = 24):
    return db.get_rolling_summary(hours=hours)


@router.get("/api/timeseries")
def timeseries(days: int = 30, tz: str = "UTC"):
    return db.get_timeseries(days=days, tz=tz)


@router.get("/api/top-species")
def top_species(days: int = 30, limit: int = 10, tz: str = "UTC"):
    return db.get_top_species(days=days, limit=limit, tz=tz)


@router.get("/api/day/{day}")
def day_stats(day: str, tz: str = "UTC"):
    try:
        date.fromisoformat(day)
    except ValueError:
        raise HTTPException(400, "Date must be in YYYY-MM-DD format")
    return db.get_day_stats(day, tz=tz)


@router.get("/api/calendar/{year}/{month}")
def calendar_month(year: int, month: int, tz: str = "UTC"):
    return db.get_calendar_month(year, month, tz=tz)


@router.get("/api/last-location")
def last_location():
    loc = db.get_last_location()
    if loc is None:
        return {}
    if loc.get("lat") is not None and loc.get("lon") is not None:
        # Resolves the coordinates to an IANA timezone (e.g. "Europe/Berlin")
        # so the frontend can show what time it currently is *there* -
        # separate from and in addition to the user's own clock/timezone.
        loc["timezone"] = _tzfinder.timezone_at(lat=loc["lat"], lng=loc["lon"])
    else:
        loc["timezone"] = None
    return loc


@router.get("/api/last-synced")
def last_synced():
    # Deliberately separate from /api/last-location: that endpoint only
    # considers entries with GPS data, so a flee or a GPS-less catch (both
    # valid signs the bot is alive and working) wouldn't move it. This one
    # answers a narrower question - "when did anything last get recorded at
    # all" - for the dashboard's "last synced" indicator.
    return {"ts": db.get_last_event_ts()}


@router.get("/api/trainers")
def trainers():
    # Distinct trainer names across catches and raids, for the History tab's
    # per-account filter (multiple trainers can post into the same channel).
    return db.get_trainers()


@router.get("/api/locations")
def locations(days: Optional[int] = None, tz: str = "UTC"):
    # days=None (the frontend leaves the param off entirely for its
    # "All Time" heatmap option) means unbounded/all-time - matches
    # db.get_all_locations' own default. The frontend itself defaults its
    # own heatmapDays setting to "30" (see app.js), so in normal use this
    # endpoint is always called with an explicit days value; None/omitted
    # is only reached via the deliberate "All Time" choice.
    return db.get_all_locations(days=days, tz=tz)


@router.get("/api/export/csv")
def export_csv(hide_trainer: bool = False):
    entries = db.get_all_events()

    buffer = io.StringIO()
    fieldnames = [
        "id", "ts", "event_type", "trainer", "pokemon_id", "pokemon_name",
        "shiny", "iv100", "iv_atk", "iv_def", "iv_sta", "cp", "level", "lat", "lon",
    ]
    writer = csv.DictWriter(buffer, fieldnames=fieldnames)
    writer.writeheader()
    for entry in entries:
        row = dict(entry)
        if hide_trainer:
            row["trainer"] = ""
        writer.writerow(row)

    buffer.seek(0)
    headers = {"Content-Disposition": "attachment; filename=pogo_stats_export.csv"}
    return StreamingResponse(buffer, media_type="text/csv", headers=headers)


@router.get("/api/history")
def history(limit: int = 50, offset: int = 0, type: str = "all", shiny: bool = False,
            iv100: bool = False, trainer: Optional[str] = None, q: Optional[str] = None):
    event_type = type if type in ("catch", "flee") else None
    return db.get_history(
        limit=limit, offset=offset, event_type=event_type, shiny_only=shiny,
        iv100_only=iv100, trainer=trainer, name_query=q,
    )


@router.get("/api/raids/summary")
def raids_summary(tz: str = "UTC"):
    return db.get_raid_summary(tz=tz)


@router.get("/api/raids/top-species")
def raids_top_species(days: int = 30, limit: int = 10, tz: str = "UTC"):
    return db.get_raid_top_species(days=days, limit=limit, tz=tz)


@router.get("/api/raids/history")
def raids_history(limit: int = 50, offset: int = 0, shiny: bool = False,
                  iv100: bool = False, trainer: Optional[str] = None, q: Optional[str] = None):
    return db.get_raid_history(
        limit=limit, offset=offset, shiny_only=shiny, iv100_only=iv100,
        trainer=trainer, name_query=q,
    )


async def _get_or_cache_sprite(pokemon_id: int, shiny: bool) -> Path:
    if shiny:
        cache_file = SPRITE_CACHE / (str(pokemon_id) + "_shiny.png")
        url = SHINY_SPRITE_URL.format(id=pokemon_id)
    else:
        cache_file = SPRITE_CACHE / (str(pokemon_id) + ".png")
        url = SPRITE_URL.format(id=pokemon_id)

    if not cache_file.exists():
        resp = await _http_client.get(url, follow_redirects=True, timeout=10)
        if resp.status_code != 200 or not resp.content:
            raise HTTPException(404, "Sprite not found")
        cache_file.write_bytes(resp.content)
    return cache_file


@router.get("/sprites/{pokemon_id}.png")
async def sprite(pokemon_id: int, shiny: bool = False):
    cache_file = await _get_or_cache_sprite(pokemon_id, shiny)
    return FileResponse(cache_file, media_type="image/png")


# Mount the routes under URL_BASE (prefix="" when serving at root, so behavior
# is identical to before when URL_BASE is unset).
app.include_router(router, prefix=URL_BASE)


if URL_BASE:
    # When served under a sub-path, send the bare host and the prefix without a
    # trailing slash to the canonical "/pogo/" - the trailing slash matters so
    # the frontend's relative asset links (style.css, app.js, favicon.svg)
    # resolve under the sub-path instead of the domain root.
    @app.get("/")
    def _root_redirect():
        return RedirectResponse(URL_BASE + "/")

    @app.get(URL_BASE)
    def _base_redirect():
        return RedirectResponse(URL_BASE + "/")


# Frontend last, so the API/redirect routes above take precedence. Mounted at
# URL_BASE (or "/" at root) so index.html is served at "/pogo/".
app.mount(URL_BASE or "/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
