# PoGo Stats

Discord bot + web dashboard for Pokemon Go catch and raid statistics from
PolygonX webhook embeds.

Deliberately data-sparing: for every catch, flee, or raid, only the fields
already present in the PolygonX embed are stored - timestamp, Pokemon, event
type, shiny/100% IV flags, IV values, CP, level, coordinates, and the
trainer name. Nothing else about the Discord message or channel is
recorded. The trainer name can be hidden from the dashboard and CSV export
via the **"Hide trainer name in History and Raids"** toggle in Settings,
but that only affects what's *displayed* - the underlying database still
has it, so treat `data/pogo_stats.db` as containing personal information
and keep it out of version control (already covered by `.gitignore`).

## Structure

```
pogo-stats/
  shared/db.py      SQLite data access (schema + queries)
  shared/parser.py  Parses PolygonX embed text into catch/flee/raid events
  bot/bot.py        Discord bot, listens on the webhook channel
  backend/main.py   FastAPI: stats endpoints + sprite cache + frontend
  backend/static/   Dashboard (vanilla JS, Chart.js, Leaflet)
  scripts/          One-off maintenance scripts
  data/             SQLite DB + cached sprites (created on first run)
```

## Setup

### 1. Python environment

```
cd pogo-stats
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

### 2. Set up the Discord bot

PolygonX itself doesn't use a bot - it posts its catch/raid alerts through a
plain Discord webhook (channel → Edit Channel → Integrations → Webhooks →
New Webhook → paste the URL into PolygonX's own configuration). That's
entirely separate from what's below. This project needs its own bot, whose
only job is to sit in that same channel and read the messages the webhook
posts there:

1. Create an application/bot at https://discord.com/developers/applications
   - just for this project, not related to PolygonX in any way.
2. Under **Bot → Privileged Gateway Intents**, enable **Message Content
   Intent**. Without this the bot can't see the embed content.
3. Invite the bot with **View Channel** and **Read Message History**
   permissions into the channel where PolygonX's webhook posts its
   messages.
4. Find the channel ID (Discord: enable Developer Mode, then on the channel
   → "Copy ID").

### 3. Configuration

Copy `.env.example` to `.env` and fill it in:

```
DISCORD_TOKEN=your-bot-token
CHANNEL_ID=<channel id>
```

### 4. Start it

Two processes, each run from the `pogo-stats` folder with the venv
activated:

```
python -m bot.bot
```

```
uvicorn backend.main:app --host 0.0.0.0 --port 8000
```

The dashboard is then reachable at `http://<server-ip>:8000`.

On Windows you can also just run `start.bat`, which starts both processes
and opens the dashboard in your browser automatically. Use `stop.bat` to
stop them again.

## Features

- **Dashboard** - live clock, last-catch map, today/week/all-time metrics,
  catches-over-time and top-species charts, and a catch density heatmap
  (aggregate only - no individual catch locations are plotted besides the
  single most recent one).
- **Calendar** - month view with a per-day breakdown of both catches and
  raids.
- **History** / **Raids** - paginated, filterable lists of every recorded
  event, each showing IV/CP/level and a shiny / 100% IV / shiny+100% IV tag
  where applicable.
- **Settings** - hide your trainer name from the interface and CSV export,
  opt in to browser notifications for shiny / 100% IV / shiny+100% IV
  catches (off by default), and set how many days back the dashboard/raid
  charts look.
- **About** - credits for every third-party library and data source this
  project relies on.
- **CSV export** - download the full catch/flee/raid history as a CSV file
  from the Settings tab (`/api/export/csv`).

All tabs auto-refresh in the background; charts and maps update in place
without resetting your zoom/pan or flashing when nothing has actually
changed.

## Notes on the parser

- The parser collects the entire visible embed text (title, description,
  fields) and searches it with regexes for the known patterns - this makes
  it robust against small formatting differences.
- **Shiny detection is currently a best-effort placeholder**
  (`shared/parser.py`, `SHINY_PATTERN`): it reacts to the word "shiny" or a
  sparkle emoji in the embed text. Adjust `SHINY_PATTERN` if your PolygonX
  setup marks shinies differently.
- 100% IV is computed from the `IV: A/D/S` field (all three values = 15).
  Wherever IV values are shown in the dashboard, the IV percentage is also
  displayed (e.g. `15/15/15 - 100%`).
- Raid-completion embeds ("Complete Raid Battle Encounter!") are matched
  before the plain catch pattern, since they also contain "caught
  successfully" text (you always catch the Pokemon after winning the raid).
- The timestamp comes deliberately from Discord itself (`message.created_at`),
  not from the embed text, because the embed only has a time of day without
  a date.

## Pokemon sprites

Sprites are downloaded once, on demand, from
https://github.com/PokeAPI/sprites (a free, community-maintained
collection) and cached in `data/sprites/` - no repeated fetching from an
external source. Used in the frontend as e.g. `/sprites/25.png` (Pikachu)
or `/sprites/25.png?shiny=true` for the shiny variant.

Note: these graphics belong to Nintendo, Game Freak, and The Pokemon
Company (Pokemon Go itself is published by Niantic). For a purely private,
non-public tool on your own server this is low-risk, but it would look
different for publication or monetization - see the in-app About tab for
the full trademark disclaimer.

## Credits

This project relies on the following third-party libraries and data
sources (same list shown in the in-app About tab):

- [FastAPI](https://fastapi.tiangolo.com/) - the backend web framework
- [Uvicorn](https://www.uvicorn.org/) - the ASGI server FastAPI runs on
- [discord.py](https://discordpy.readthedocs.io/) - the Discord bot library
- [httpx](https://www.python-httpx.org/) - used to fetch and cache Pokemon sprites
- [python-dotenv](https://github.com/theskumar/python-dotenv) - loads the bot token from `.env`
- [Chart.js](https://www.chartjs.org/) - the dashboard and raid charts
- [Leaflet.js](https://leafletjs.com/) - the interactive maps
- [Leaflet.heat](https://github.com/Leaflet/Leaflet.heat) - the catch density heatmap
- [CARTO](https://carto.com/basemaps) - the free dark map tiles
- [OpenStreetMap contributors](https://www.openstreetmap.org/copyright) - the underlying map data
- [PokeAPI/sprites](https://github.com/PokeAPI/sprites) - the Pokemon sprite images
- PolygonX - the Discord webhook source this tool reads catch/raid data from
