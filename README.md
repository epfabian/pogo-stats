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
  launcher.py       Status window + system tray icon that starts/stops
                     the Bot and Backend (started for you by start.vbs)
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

Two optional settings let you expose the dashboard beyond a trusted LAN. Both
are blank by default, which keeps the original no-auth, served-at-root
behavior:

```
# Password-protect the dashboard (browser login popup, Sonarr-style).
# Set BOTH to enable; leave BOTH blank to disable.
DASHBOARD_USER=you
DASHBOARD_PASSWORD=a-long-random-password

# Serve under a sub-path so it can live at DOMAIN/pogo behind a reverse
# proxy. Leave blank to serve at the root.
URL_BASE=/pogo
```

See [Remote access](#remote-access-optional) below for how these fit together.

### 4. Start it

On Windows, just double-click `start.vbs` - that's the only file you need
to run. It launches a small status window (`launcher.py`) that starts and
manages both the Bot and the Backend for you: a green/red dot next to "Bot"
and "Backend" shows whether each is currently running, plus buttons to open
the dashboard, restart both, or quit. Closing the window (the X button)
minimizes it to the system tray instead of quitting - double-click the tray
icon to bring it back, or right-click it for "Show"/"Quit". There's no
separate stop script: use the window's or tray icon's "Quit" button
instead, which shuts both processes down cleanly. (`start.vbs` rather than
a `.bat` file specifically so double-clicking it doesn't flash a console
window even for a moment.)

The dashboard is then reachable at `http://<server-ip>:8000`.

**Manual start (without the launcher)** - e.g. on Linux/macOS, or on a
headless server without a GUI: run the Bot and Backend as two separate
processes yourself, each from the `pogo-stats` folder with the venv
activated:

```
python -m bot.bot
```

```
uvicorn backend.main:app --host 0.0.0.0 --port 8000
```

`launcher.py` itself (`pystray`/`Tkinter`) requires a graphical Windows
environment, so this manual route is also the one to use if you ever want
to run PoGo Stats on non-Windows hardware.

## Remote access (optional)

By default the dashboard has no authentication and is meant for a trusted
local network. To reach it from outside:

1. **Password protection** - set `DASHBOARD_USER` and `DASHBOARD_PASSWORD` in
   `.env`. The whole dashboard and API then require HTTP Basic Auth, so the
   browser shows its native login popup (the same idea as Sonarr/Radarr).
   Because Basic Auth sends the credentials on every request, only ever expose
   it over **HTTPS**.
2. **Sub-path** - set `URL_BASE` (e.g. `/pogo`) to serve everything under
   `DOMAIN/pogo` instead of the domain root, so it can share a hostname with
   other apps behind a reverse proxy. Forward the prefix as-is; don't strip it.
   Example nginx:

   ```
   location /pogo/ {
       proxy_pass http://127.0.0.1:8000/pogo/;
       proxy_set_header Host $host;
   }
   ```

Terminate TLS at the reverse proxy (or use a tunnel such as Cloudflare Tunnel
or Tailscale) so the whole thing runs over HTTPS.

## Features

- **Dashboard** - live clock, last-catch map (with the local time *at* that
  location shown alongside your own clock, resolved from its coordinates),
  today/week/all-time metrics, a catches-over-time chart with hover
  tooltips, a top-species chart, and a catch density heatmap (aggregate
  only - no individual catch locations are plotted besides the single most
  recent one). The heatmap's own time range (default: last 30 days) is
  configurable in Settings, separately from the other charts - since it
  accumulates density over time, showing "all time" by default would
  eventually turn it into an undifferentiated blob around wherever you're
  usually active rather than showing where you've recently been. A
  "Last catch synced X minutes/hours ago" indicator also sits on the
  clock card - unlike the tray/status window (which only shows whether the
  Bot *process* is running), this reflects whether it's actually still
  receiving and storing Discord events.
- **Rolling 24h** - encounters (catches + flees + raid catches combined) and
  raids in a rolling 24-hour window ending right now, matching how Pokemon
  Go's own encounter limit actually works - unlike "Today", it's not tied to
  local midnight.
- **Calendar** - month view with a per-day breakdown of both catches and
  raids.
- **History** - paginated, filterable list of every recorded catch/flee,
  with a Catches/Raids sub-tab to browse raid catches the same way. Filter
  by Shiny only, 100% IV only, or both together (for shundos), by **account**
  (trainer), or **search by Pokemon name**, and switch between a detailed List
  view or a more compact Grid view. The account filter makes the tool usable
  when several trainers post into the same channel.
- **Raids** - a separate top-level tab with its own summary, top-boss chart,
  and paginated history.
- **Settings** - hide your trainer name from the interface and CSV export,
  opt in to browser notifications for shiny / 100% IV / shiny+100% IV
  catches (off by default), and set how many days back the dashboard/raid
  charts and the heatmap each look.
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
- A single malformed or unexpected embed is logged and skipped rather than
  interrupting the rest of that message (or the bot as a whole) - see
  `bot/bot.py`.
- On startup, the bot checks the channel's message history since the last
  event it has recorded and processes anything it missed while it wasn't
  running (crash, restart, server reboot, deployment, etc.), so a bot
  outage doesn't silently mean lost catches. This only runs if the database
  already has at least one prior event to use as a starting point - on a
  brand new install there's nothing to catch up to yet.

## Running the tests

```
pip install -r requirements-dev.txt
pytest
```

Covers the embed parser (`shared/parser.py`) plus the raid/catch
de-duplication and the History account/name filters in `shared/db.py`.

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
- [timezonefinder](https://github.com/jannikmi/timezonefinder) - resolves the last catch's coordinates to a timezone, for the "local time there" indicator (offline, no API key needed)
- [pystray](https://github.com/moses-palmer/pystray) - the system tray icon for `launcher.py`
- [Pillow](https://python-pillow.org/) - draws the tray icon image
- [Chart.js](https://www.chartjs.org/) - the dashboard and raid charts
- [Leaflet.js](https://leafletjs.com/) - the interactive maps
- [Leaflet.heat](https://github.com/Leaflet/Leaflet.heat) - the catch density heatmap
- [CARTO](https://carto.com/basemaps) - the free dark map tiles
- [OpenStreetMap contributors](https://www.openstreetmap.org/copyright) - the underlying map data
- [PokeAPI/sprites](https://github.com/PokeAPI/sprites) - the Pokemon sprite images
- PolygonX - the Discord webhook source this tool reads catch/raid data from
