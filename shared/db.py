"""
Data access layer for the Pokemon Go statistics.

Deliberately data-sparing: we do NOT store every CP/IV/coordinate, only what
is needed for the desired statistics (catch time, Pokemon, shiny flag,
100%-IV flag, plus IV/CP/level once those were added).

Extensibility: for new event types (e.g. raids) simply add a new table
following this pattern (see SCHEMA) and add matching get_* functions.
The dashboard/API/frontend then get an additional tab.
"""

import shutil
import sqlite3
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

# Time window within which a catch and a raid catch with identical
# trainer/Pokemon/IV are considered the same real-world event (see
# _find_recent_match).
DEDUPE_WINDOW_SECONDS = 300

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "pogo_stats.db"
BACKUP_DIR = Path(__file__).resolve().parent.parent / "data" / "backups"
BACKUP_KEEP = 7


def _apply_pragmas(conn):
    """WAL mode + synchronous=NORMAL make the database more resilient against
    interrupted writes (crash, power loss, sync tools like OneDrive touching
    the file mid-write). In the old rollback-journal mode, a transaction that
    grows the file (e.g. when creating the raids table) could leave behind an
    incomplete file if interrupted - which is exactly what happened once."""
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")

SCHEMA = """
CREATE TABLE IF NOT EXISTS catches (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    event_type TEXT NOT NULL,
    trainer TEXT,
    pokemon_id INTEGER,
    pokemon_name TEXT,
    shiny INTEGER NOT NULL DEFAULT 0,
    iv100 INTEGER NOT NULL DEFAULT 0,
    lat REAL,
    lon REAL,
    iv_atk INTEGER,
    iv_def INTEGER,
    iv_sta INTEGER,
    cp INTEGER,
    level INTEGER
);
CREATE INDEX IF NOT EXISTS idx_catches_ts ON catches(ts);
CREATE INDEX IF NOT EXISTS idx_catches_type ON catches(event_type);

CREATE TABLE IF NOT EXISTS raids (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    trainer TEXT,
    pokemon_id INTEGER,
    pokemon_name TEXT,
    shiny INTEGER NOT NULL DEFAULT 0,
    iv100 INTEGER NOT NULL DEFAULT 0,
    lat REAL,
    lon REAL,
    iv_atk INTEGER,
    iv_def INTEGER,
    iv_sta INTEGER,
    cp INTEGER,
    level INTEGER
);
CREATE INDEX IF NOT EXISTS idx_raids_ts ON raids(ts);
"""


def _normalize_ts(ts):
    """Discord provides tz-aware UTC timestamps. isoformat() then appends a
    '+00:00' - combined with the 'Z' suffix the frontend appends, this
    produces an invalid date. So we normalize to a naive UTC datetime here
    before writing it to the database."""
    if ts.tzinfo is not None:
        ts = ts.astimezone(timezone.utc).replace(tzinfo=None)
    return ts.isoformat(timespec="seconds")


def _seconds_apart(ts_a, ts_b):
    try:
        a = datetime.fromisoformat(ts_a)
        b = datetime.fromisoformat(ts_b)
    except ValueError:
        return None
    return abs((a - b).total_seconds())


def _find_recent_match(conn, table, trainer, pokemon_id, iv_atk, iv_def, iv_sta, ts_str,
                        window_seconds=DEDUPE_WINDOW_SECONDS):
    """Some PolygonX configurations send TWO Discord messages for the same
    raid catch: the "Complete Raid Battle Encounter!" message AND a regular
    "caught successfully" embed. To avoid such a catch ending up in both
    tables (and thus duplicated in stats/history), this checks whether the
    OTHER table already has an entry with the same trainer/Pokemon/IV values
    within a short time window."""
    if pokemon_id is None:
        return None
    rows = conn.execute(
        "SELECT id, ts FROM " + table + " WHERE pokemon_id = ? "
        "AND trainer IS ? AND iv_atk IS ? AND iv_def IS ? AND iv_sta IS ?",
        (pokemon_id, trainer, iv_atk, iv_def, iv_sta),
    ).fetchall()
    for row in rows:
        diff = _seconds_apart(row["ts"], ts_str)
        if diff is not None and diff <= window_seconds:
            return row["id"]
    return None


def _ensure_columns(conn):
    # Migration for databases created before the lat/lon extension.
    existing = [row["name"] for row in conn.execute("PRAGMA table_info(catches)").fetchall()]
    if "lat" not in existing:
        conn.execute("ALTER TABLE catches ADD COLUMN lat REAL")
    if "lon" not in existing:
        conn.execute("ALTER TABLE catches ADD COLUMN lon REAL")

    # Migration for databases created before the IV/CP/level extension -
    # applies to both catches and raids.
    for table in ("catches", "raids"):
        existing_cols = [row["name"] for row in conn.execute("PRAGMA table_info(" + table + ")").fetchall()]
        for column in ("iv_atk", "iv_def", "iv_sta", "cp", "level"):
            if column not in existing_cols:
                conn.execute("ALTER TABLE " + table + " ADD COLUMN " + column + " INTEGER")


def init_db(db_path=DB_PATH):
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        _apply_pragmas(conn)
        conn.executescript(SCHEMA)
        _ensure_columns(conn)
        conn.commit()
    finally:
        conn.close()


@contextmanager
def get_conn(db_path=DB_PATH):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    _apply_pragmas(conn)
    try:
        yield conn
    finally:
        conn.close()


def backup_db(db_path=DB_PATH, backup_dir=BACKUP_DIR, keep=BACKUP_KEEP):
    """Creates a daily backup copy of the database (via SQLite's online
    backup API, so it's safe even while the DB is in use) and keeps only the
    last `keep` copies. Called on backend startup so that if the file ever
    gets corrupted, at most one day of data is lost."""
    db_path = Path(db_path)
    backup_dir = Path(backup_dir)
    if not db_path.exists():
        return None
    backup_dir.mkdir(parents=True, exist_ok=True)

    today = date.today().isoformat()
    target = backup_dir / f"pogo_stats-{today}.db"

    src = sqlite3.connect(db_path)
    try:
        dst = sqlite3.connect(target)
        try:
            src.backup(dst)
        finally:
            dst.close()
    finally:
        src.close()

    backups = sorted(backup_dir.glob("pogo_stats-*.db"))
    for old in backups[:-keep]:
        old.unlink(missing_ok=True)

    return target


def insert_catch(ts, event_type, trainer, pokemon_id, pokemon_name, shiny, iv100,
                  lat=None, lon=None, iv_atk=None, iv_def=None, iv_sta=None, cp=None, level=None,
                  db_path=DB_PATH):
    ts_str = _normalize_ts(ts)
    with get_conn(db_path) as conn:
        if event_type == "catch":
            # If this catch was already recorded as a raid catch (some
            # PolygonX setups send a regular catch embed in addition to the
            # raid message for the same catch), don't store it twice.
            dup_id = _find_recent_match(conn, "raids", trainer, pokemon_id, iv_atk, iv_def, iv_sta, ts_str)
            if dup_id is not None:
                return
        conn.execute(
            "INSERT INTO catches (ts, event_type, trainer, pokemon_id, pokemon_name, shiny, iv100, lat, lon, "
            "iv_atk, iv_def, iv_sta, cp, level) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                ts_str,
                event_type,
                trainer,
                pokemon_id,
                pokemon_name,
                int(shiny),
                int(iv100),
                lat,
                lon,
                iv_atk,
                iv_def,
                iv_sta,
                cp,
                level,
            ),
        )
        conn.commit()


def insert_raid(ts, trainer, pokemon_id, pokemon_name, shiny, iv100, lat=None, lon=None,
                 iv_atk=None, iv_def=None, iv_sta=None, cp=None, level=None, db_path=DB_PATH):
    ts_str = _normalize_ts(ts)
    with get_conn(db_path) as conn:
        # If the matching regular catch already came in (shortly) before,
        # remove that row - the raid entry is the authoritative source.
        dup_id = _find_recent_match(conn, "catches", trainer, pokemon_id, iv_atk, iv_def, iv_sta, ts_str)
        if dup_id is not None:
            conn.execute("DELETE FROM catches WHERE id = ?", (dup_id,))

        conn.execute(
            "INSERT INTO raids (ts, trainer, pokemon_id, pokemon_name, shiny, iv100, lat, lon, "
            "iv_atk, iv_def, iv_sta, cp, level) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                ts_str,
                trainer,
                pokemon_id,
                pokemon_name,
                int(shiny),
                int(iv100),
                lat,
                lon,
                iv_atk,
                iv_def,
                iv_sta,
                cp,
                level,
            ),
        )
        conn.commit()


def _count_raids(conn, where, params=()):
    row = conn.execute("SELECT COUNT(*) AS c FROM raids WHERE " + where, params).fetchone()
    if row:
        return row["c"]
    return 0


def get_raid_summary(db_path=DB_PATH):
    today_iso = date.today().isoformat()
    week_start_iso = (date.today() - timedelta(days=date.today().weekday())).isoformat()
    with get_conn(db_path) as conn:
        summary = {}
        summary["today"] = _count_raids(conn, "ts >= ?", (today_iso,))
        summary["week"] = _count_raids(conn, "ts >= ?", (week_start_iso,))
        summary["all_time"] = _count_raids(conn, "1=1")
        summary["shiny_today"] = _count_raids(conn, "shiny=1 AND ts >= ?", (today_iso,))
        summary["iv100_today"] = _count_raids(conn, "iv100=1 AND ts >= ?", (today_iso,))
        return summary


def get_raid_top_species(days=30, limit=10, db_path=DB_PATH):
    with get_conn(db_path) as conn:
        query = (
            "SELECT pokemon_id, pokemon_name, COUNT(*) AS c FROM raids "
            "WHERE ts >= date('now', ?) "
            "GROUP BY pokemon_id, pokemon_name ORDER BY c DESC LIMIT ?"
        )
        rows = conn.execute(query, ("-" + str(days) + " days", limit)).fetchall()
        output = []
        for row in rows:
            output.append({
                "pokemon_id": row["pokemon_id"],
                "name": row["pokemon_name"],
                "count": row["c"],
            })
        return output


def get_raid_history(limit=50, offset=0, db_path=DB_PATH):
    with get_conn(db_path) as conn:
        query = (
            "SELECT id, ts, trainer, pokemon_id, pokemon_name, shiny, iv100, lat, lon, "
            "iv_atk, iv_def, iv_sta, cp, level "
            "FROM raids ORDER BY ts DESC LIMIT ? OFFSET ?"
        )
        rows = conn.execute(query, (limit, offset)).fetchall()
        total = _count_raids(conn, "1=1")

        entries = []
        for row in rows:
            entries.append({
                "id": row["id"],
                "ts": row["ts"],
                "trainer": row["trainer"],
                "pokemon_id": row["pokemon_id"],
                "pokemon_name": row["pokemon_name"],
                "shiny": bool(row["shiny"]),
                "iv100": bool(row["iv100"]),
                "lat": row["lat"],
                "lon": row["lon"],
                "iv_atk": row["iv_atk"],
                "iv_def": row["iv_def"],
                "iv_sta": row["iv_sta"],
                "cp": row["cp"],
                "level": row["level"],
            })

        result = {}
        result["entries"] = entries
        result["total"] = total
        result["limit"] = limit
        result["offset"] = offset
        return result


def _count(conn, where, params=()):
    row = conn.execute("SELECT COUNT(*) AS c FROM catches WHERE " + where, params).fetchone()
    if row:
        return row["c"]
    return 0


def get_summary(db_path=DB_PATH):
    today_iso = date.today().isoformat()
    week_start_iso = (date.today() - timedelta(days=date.today().weekday())).isoformat()
    with get_conn(db_path) as conn:
        summary = {}
        summary["today"] = _count(conn, "event_type='catch' AND ts >= ?", (today_iso,))
        summary["week"] = _count(conn, "event_type='catch' AND ts >= ?", (week_start_iso,))
        summary["all_time"] = _count(conn, "event_type='catch'")
        summary["shiny_today"] = _count(conn, "event_type='catch' AND shiny=1 AND ts >= ?", (today_iso,))
        summary["iv100_today"] = _count(conn, "event_type='catch' AND iv100=1 AND ts >= ?", (today_iso,))
        summary["flee_today"] = _count(conn, "event_type='flee' AND ts >= ?", (today_iso,))
        return summary


def get_timeseries(days=30, db_path=DB_PATH):
    with get_conn(db_path) as conn:
        query = (
            "SELECT substr(ts, 1, 10) AS day, COUNT(*) AS c FROM catches "
            "WHERE event_type='catch' AND ts >= date('now', ?) "
            "GROUP BY day ORDER BY day"
        )
        rows = conn.execute(query, ("-" + str(days) + " days",)).fetchall()
        output = []
        for row in rows:
            output.append({"day": row["day"], "count": row["c"]})
        return output


def get_top_species(days=30, limit=10, db_path=DB_PATH):
    with get_conn(db_path) as conn:
        query = (
            "SELECT pokemon_name, COUNT(*) AS c FROM catches "
            "WHERE event_type='catch' AND ts >= date('now', ?) "
            "GROUP BY pokemon_name ORDER BY c DESC LIMIT ?"
        )
        rows = conn.execute(query, ("-" + str(days) + " days", limit)).fetchall()
        output = []
        for row in rows:
            output.append({"name": row["pokemon_name"], "count": row["c"]})
        return output


def get_day_stats(day, db_path=DB_PATH):
    like = day + "%"
    with get_conn(db_path) as conn:
        catches = _count(conn, "event_type='catch' AND ts LIKE ?", (like,))
        shiny = _count(conn, "event_type='catch' AND shiny=1 AND ts LIKE ?", (like,))
        iv100 = _count(conn, "event_type='catch' AND iv100=1 AND ts LIKE ?", (like,))
        flee = _count(conn, "event_type='flee' AND ts LIKE ?", (like,))
        query = (
            "SELECT pokemon_id, pokemon_name, COUNT(*) AS c FROM catches "
            "WHERE event_type='catch' AND ts LIKE ? "
            "GROUP BY pokemon_id, pokemon_name ORDER BY c DESC LIMIT 5"
        )
        top_rows = conn.execute(query, (like,)).fetchall()
        top_species = []
        for row in top_rows:
            top_species.append({
                "pokemon_id": row["pokemon_id"],
                "name": row["pokemon_name"],
                "count": row["c"],
            })

        raids = _count_raids(conn, "ts LIKE ?", (like,))
        raid_shiny = _count_raids(conn, "shiny=1 AND ts LIKE ?", (like,))
        raid_iv100 = _count_raids(conn, "iv100=1 AND ts LIKE ?", (like,))
        raid_query = (
            "SELECT pokemon_id, pokemon_name, COUNT(*) AS c FROM raids "
            "WHERE ts LIKE ? "
            "GROUP BY pokemon_id, pokemon_name ORDER BY c DESC LIMIT 5"
        )
        raid_top_rows = conn.execute(raid_query, (like,)).fetchall()
        raid_top_species = []
        for row in raid_top_rows:
            raid_top_species.append({
                "pokemon_id": row["pokemon_id"],
                "name": row["pokemon_name"],
                "count": row["c"],
            })

        result = {}
        result["day"] = day
        result["catches"] = catches
        result["shiny"] = shiny
        result["iv100"] = iv100
        result["flee"] = flee
        result["top_species"] = top_species
        result["raids"] = raids
        result["raid_shiny"] = raid_shiny
        result["raid_iv100"] = raid_iv100
        result["raid_top_species"] = raid_top_species
        return result


def get_history(limit=50, offset=0, event_type=None, include_raids=False, db_path=DB_PATH):
    with get_conn(db_path) as conn:
        where = "1=1"
        params = []
        if event_type in ("catch", "flee"):
            where = "event_type = ?"
            params.append(event_type)

        catch_cols = (
            "id, ts, event_type, trainer, pokemon_id, pokemon_name, shiny, iv100, lat, lon, "
            "iv_atk, iv_def, iv_sta, cp, level"
        )
        raid_cols = (
            "id, ts, 'raid' AS event_type, trainer, pokemon_id, pokemon_name, shiny, iv100, lat, lon, "
            "iv_atk, iv_def, iv_sta, cp, level"
        )

        if include_raids:
            query = (
                "SELECT " + catch_cols + " FROM catches WHERE " + where +
                " UNION ALL SELECT " + raid_cols + " FROM raids "
                "ORDER BY ts DESC LIMIT ? OFFSET ?"
            )
            query_params = list(params) + [limit, offset]
            rows = conn.execute(query, query_params).fetchall()
            total = _count(conn, where, tuple(params)) + _count_raids(conn, "1=1")
        else:
            query = (
                "SELECT " + catch_cols + " FROM catches WHERE " + where + " "
                "ORDER BY ts DESC LIMIT ? OFFSET ?"
            )
            query_params = list(params) + [limit, offset]
            rows = conn.execute(query, query_params).fetchall()
            total = _count(conn, where, tuple(params))

        entries = []
        for row in rows:
            entries.append({
                "id": row["id"],
                "ts": row["ts"],
                "event_type": row["event_type"],
                "trainer": row["trainer"],
                "pokemon_id": row["pokemon_id"],
                "pokemon_name": row["pokemon_name"],
                "shiny": bool(row["shiny"]),
                "iv100": bool(row["iv100"]),
                "lat": row["lat"],
                "lon": row["lon"],
                "iv_atk": row["iv_atk"],
                "iv_def": row["iv_def"],
                "iv_sta": row["iv_sta"],
                "cp": row["cp"],
                "level": row["level"],
            })

        result = {}
        result["entries"] = entries
        result["total"] = total
        result["limit"] = limit
        result["offset"] = offset
        return result


def get_calendar_month(year, month, db_path=DB_PATH):
    prefix = "{:04d}-{:02d}".format(year, month)
    with get_conn(db_path) as conn:
        query = (
            "SELECT substr(ts, 1, 10) AS day, "
            "SUM(CASE WHEN event_type='catch' THEN 1 ELSE 0 END) AS catches, "
            "SUM(CASE WHEN event_type='catch' AND shiny=1 THEN 1 ELSE 0 END) AS shiny "
            "FROM catches WHERE substr(ts, 1, 7) = ? GROUP BY day"
        )
        rows = conn.execute(query, (prefix,)).fetchall()
        result = {}
        for row in rows:
            day_key = row["day"]
            result[day_key] = {"catches": row["catches"], "shiny": row["shiny"]}
        return result


def get_all_locations(db_path=DB_PATH):
    """Returns just the coordinates (no other details) of every catch/raid
    with a known location - used for the dashboard heatmap. Deliberately
    stripped down to lat/lon only, since the heatmap should show density,
    not per-catch identity."""
    with get_conn(db_path) as conn:
        query = (
            "SELECT lat, lon FROM catches WHERE lat IS NOT NULL AND lon IS NOT NULL "
            "UNION ALL "
            "SELECT lat, lon FROM raids WHERE lat IS NOT NULL AND lon IS NOT NULL"
        )
        rows = conn.execute(query).fetchall()
        return [{"lat": row["lat"], "lon": row["lon"]} for row in rows]


def get_all_events(db_path=DB_PATH):
    """Returns every catch/flee/raid row (no pagination) in chronological
    order, for CSV export."""
    with get_conn(db_path) as conn:
        catch_cols = (
            "id, ts, event_type, trainer, pokemon_id, pokemon_name, shiny, iv100, lat, lon, "
            "iv_atk, iv_def, iv_sta, cp, level"
        )
        raid_cols = (
            "id, ts, 'raid' AS event_type, trainer, pokemon_id, pokemon_name, shiny, iv100, lat, lon, "
            "iv_atk, iv_def, iv_sta, cp, level"
        )
        query = (
            "SELECT " + catch_cols + " FROM catches "
            "UNION ALL SELECT " + raid_cols + " FROM raids "
            "ORDER BY ts ASC"
        )
        rows = conn.execute(query).fetchall()
        entries = []
        for row in rows:
            entries.append({
                "id": row["id"],
                "ts": row["ts"],
                "event_type": row["event_type"],
                "trainer": row["trainer"],
                "pokemon_id": row["pokemon_id"],
                "pokemon_name": row["pokemon_name"],
                "shiny": bool(row["shiny"]),
                "iv100": bool(row["iv100"]),
                "iv_atk": row["iv_atk"],
                "iv_def": row["iv_def"],
                "iv_sta": row["iv_sta"],
                "cp": row["cp"],
                "level": row["level"],
                "lat": row["lat"],
                "lon": row["lon"],
            })
        return entries


def get_last_location(db_path=DB_PATH):
    """Returns trainer/Pokemon/time of the last action (catch, flee, or raid)
    with a known location - for the map on the dashboard."""
    with get_conn(db_path) as conn:
        query = (
            "SELECT ts, event_type, trainer, pokemon_id, pokemon_name, lat, lon "
            "FROM catches WHERE lat IS NOT NULL AND lon IS NOT NULL "
            "UNION ALL "
            "SELECT ts, 'raid' AS event_type, trainer, pokemon_id, pokemon_name, lat, lon "
            "FROM raids WHERE lat IS NOT NULL AND lon IS NOT NULL "
            "ORDER BY ts DESC LIMIT 1"
        )
        row = conn.execute(query).fetchone()
        if row is None:
            return None
        return {
            "ts": row["ts"],
            "event_type": row["event_type"],
            "trainer": row["trainer"],
            "pokemon_id": row["pokemon_id"],
            "pokemon_name": row["pokemon_name"],
            "lat": row["lat"],
            "lon": row["lon"],
        }
