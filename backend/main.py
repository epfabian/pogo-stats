"""
FastAPI backend for the PoGo Stats web UI.

Reads exclusively from the SQLite DB that the Discord bot populates. Also
serves Pokemon sprites, which are downloaded once from the free PokeAPI
sprites repo (https://github.com/PokeAPI/sprites) and cached locally - no
repeated fetching from an external source needed.

Start (from the project root, with the venv activated):
    uvicorn backend.main:app --host 0.0.0.0 --port 8000
"""

import csv
import io
from datetime import date
from pathlib import Path

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from timezonefinder import TimezoneFinder

from shared import db

BASE_DIR = Path(__file__).resolve().parent.parent
STATIC_DIR = Path(__file__).resolve().parent / "static"
SPRITE_CACHE = BASE_DIR / "data" / "sprites"
SPRITE_URL = "https://raw.githubusercontent.com/PokeAPI/sprites/master/sprites/pokemon/{id}.png"
SHINY_SPRITE_URL = "https://raw.githubusercontent.com/PokeAPI/sprites/master/sprites/pokemon/shiny/{id}.png"

app = FastAPI(title="PoGo Stats API")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

# Built once at import time (loads its offline shapefile-derived lookup data
# into memory) and reused for every request - constructing it per-request
# would be needlessly slow. No network calls, no API key.
_tzfinder = TimezoneFinder()


@app.on_event("startup")
def startup():
    db.init_db()
    SPRITE_CACHE.mkdir(parents=True, exist_ok=True)
    # Daily backup so that if the DB ever gets corrupted (see the incident
    # with the raids table), at most one day of data is lost.
    try:
        db.backup_db()
    except Exception as exc:  # Backup must never block startup.
        print("Warning: DB backup failed on startup:", exc)


@app.get("/api/summary")
def summary(tz: str = "UTC"):
    return db.get_summary(tz=tz)


@app.get("/api/rolling/summary")
def rolling_summary(hours: int = 24):
    return db.get_rolling_summary(hours=hours)


@app.get("/api/timeseries")
def timeseries(days: int = 30, tz: str = "UTC"):
    return db.get_timeseries(days=days, tz=tz)


@app.get("/api/top-species")
def top_species(days: int = 30, limit: int = 10, tz: str = "UTC"):
    return db.get_top_species(days=days, limit=limit, tz=tz)


@app.get("/api/day/{day}")
def day_stats(day: str, tz: str = "UTC"):
    try:
        date.fromisoformat(day)
    except ValueError:
        raise HTTPException(400, "Date must be in YYYY-MM-DD format")
    return db.get_day_stats(day, tz=tz)


@app.get("/api/calendar/{year}/{month}")
def calendar_month(year: int, month: int, tz: str = "UTC"):
    return db.get_calendar_month(year, month, tz=tz)


@app.get("/api/last-location")
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


@app.get("/api/locations")
def locations():
    return db.get_all_locations()


@app.get("/api/export/csv")
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


@app.get("/api/history")
def history(limit: int = 50, offset: int = 0, type: str = "all", shiny: bool = False, iv100: bool = False):
    event_type = type if type in ("catch", "flee") else None
    return db.get_history(limit=limit, offset=offset, event_type=event_type, shiny_only=shiny, iv100_only=iv100)


@app.get("/api/raids/summary")
def raids_summary(tz: str = "UTC"):
    return db.get_raid_summary(tz=tz)


@app.get("/api/raids/top-species")
def raids_top_species(days: int = 30, limit: int = 10, tz: str = "UTC"):
    return db.get_raid_top_species(days=days, limit=limit, tz=tz)


@app.get("/api/raids/history")
def raids_history(limit: int = 50, offset: int = 0, shiny: bool = False, iv100: bool = False):
    return db.get_raid_history(limit=limit, offset=offset, shiny_only=shiny, iv100_only=iv100)


async def _get_or_cache_sprite(pokemon_id: int, shiny: bool) -> Path:
    if shiny:
        cache_file = SPRITE_CACHE / (str(pokemon_id) + "_shiny.png")
        url = SHINY_SPRITE_URL.format(id=pokemon_id)
    else:
        cache_file = SPRITE_CACHE / (str(pokemon_id) + ".png")
        url = SPRITE_URL.format(id=pokemon_id)

    if not cache_file.exists():
        async with httpx.AsyncClient() as http_client:
            resp = await http_client.get(url, follow_redirects=True, timeout=10)
        if resp.status_code != 200 or not resp.content:
            raise HTTPException(404, "Sprite not found")
        cache_file.write_bytes(resp.content)
    return cache_file


@app.get("/sprites/{pokemon_id}.png")
async def sprite(pokemon_id: int, shiny: bool = False):
    cache_file = await _get_or_cache_sprite(pokemon_id, shiny)
    return FileResponse(cache_file, media_type="image/png")


app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
