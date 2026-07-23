"""Tests for db.get_species_stats - the per-Pokemon drill-down behind clicking
a name in the History tab.

Note: every row is given a distinct IV triple. The raid/catch de-duplication in
insert_catch matches on trainer + pokemon + IVs within a short time window, and
rows with all-NULL IVs match each other - so without this, seeding a catch and
a raid for the same species would silently collapse into one row and the
assertions here would be testing the wrong thing."""

from datetime import datetime, timedelta, timezone

from shared import db

GIBLE = 443


def _ago(hours):
    return datetime.now(timezone.utc) - timedelta(hours=hours)


def _catch(dbp, hours_ago, iv, shiny=False, iv100=False, trainer="Ash",
           pid=GIBLE, name="Gible", lat=None, lon=None, event_type="catch"):
    db.insert_catch(
        ts=_ago(hours_ago), event_type=event_type, trainer=trainer, pokemon_id=pid,
        pokemon_name=name, shiny=shiny, iv100=iv100, lat=lat, lon=lon,
        iv_atk=iv, iv_def=iv, iv_sta=iv, db_path=dbp,
    )


def _raid(dbp, hours_ago, iv, shiny=False, iv100=False, trainer="Ash",
          pid=GIBLE, name="Gible", lat=None, lon=None):
    db.insert_raid(
        ts=_ago(hours_ago), trainer=trainer, pokemon_id=pid, pokemon_name=name,
        shiny=shiny, iv100=iv100, lat=lat, lon=lon,
        iv_atk=iv, iv_def=iv, iv_sta=iv, db_path=dbp,
    )


def test_rolling_period_buckets(tmp_path):
    dbp = tmp_path / "t.db"
    db.init_db(dbp)
    _catch(dbp, 1, iv=1)            # inside 24h
    _catch(dbp, 30, iv=2)           # outside 24h, inside 7d
    _catch(dbp, 24 * 10, iv=3)      # outside 7d, inside 30d
    _catch(dbp, 24 * 60, iv=4)      # outside 30d

    periods = db.get_species_stats(GIBLE, db_path=dbp)["periods"]
    assert periods["24h"]["caught"] == 1
    assert periods["7d"]["caught"] == 2
    assert periods["30d"]["caught"] == 3
    assert periods["all"]["caught"] == 4


def test_shiny_hundo_and_shundo(tmp_path):
    dbp = tmp_path / "t.db"
    db.init_db(dbp)
    _catch(dbp, 1, iv=1, shiny=True)                  # shiny only
    _catch(dbp, 2, iv=2, iv100=True)                  # hundo only
    _catch(dbp, 3, iv=3, shiny=True, iv100=True)      # shundo

    stats = db.get_species_stats(GIBLE, db_path=dbp)["periods"]["all"]
    assert stats["caught"] == 3
    assert stats["shiny"] == 2   # shiny-only + shundo
    assert stats["hundo"] == 2   # hundo-only + shundo
    assert stats["shundo"] == 1  # only the one that is both


def test_raids_count_as_caught_and_are_broken_out(tmp_path):
    dbp = tmp_path / "t.db"
    db.init_db(dbp)
    _catch(dbp, 1, iv=1)
    _raid(dbp, 2, iv=2)
    _raid(dbp, 3, iv=3)
    # A different species must not leak into the totals.
    _catch(dbp, 1, iv=4, pid=25, name="Pikachu")

    stats = db.get_species_stats(GIBLE, db_path=dbp)["periods"]["all"]
    assert stats["caught"] == 3  # 1 normal + 2 raid catches, combined
    assert stats["raids"] == 2   # of which came from raids


def test_flees_excluded_from_caught_but_counted(tmp_path):
    dbp = tmp_path / "t.db"
    db.init_db(dbp)
    _catch(dbp, 1, iv=1)
    _catch(dbp, 2, iv=2, event_type="flee")
    _catch(dbp, 3, iv=3, event_type="flee")

    stats = db.get_species_stats(GIBLE, db_path=dbp)["periods"]["all"]
    assert stats["caught"] == 1
    assert stats["fled"] == 2


def test_trainer_scoping(tmp_path):
    dbp = tmp_path / "t.db"
    db.init_db(dbp)
    _catch(dbp, 1, iv=1, trainer="Ash")
    _catch(dbp, 2, iv=2, trainer="Ash")
    _catch(dbp, 3, iv=3, trainer="Misty")

    assert db.get_species_stats(GIBLE, db_path=dbp)["periods"]["all"]["caught"] == 3
    scoped = db.get_species_stats(GIBLE, trainer="Ash", db_path=dbp)
    assert scoped["periods"]["all"]["caught"] == 2
    assert scoped["trainer"] == "Ash"


def test_last_location_falls_back_to_older_located_catch(tmp_path):
    dbp = tmp_path / "t.db"
    db.init_db(dbp)
    _catch(dbp, 5, iv=1, lat=48.1, lon=11.5)   # older, has GPS
    _catch(dbp, 1, iv=2)                       # newest, no GPS

    stats = db.get_species_stats(GIBLE, db_path=dbp)
    # last_caught tracks the newest catch even though it has no coordinates...
    assert stats["last_caught"] is not None
    # ...while last_location points back at the most recent *located* one.
    assert stats["last_location"]["lat"] == 48.1
    assert stats["last_caught"]["ts"] > stats["last_location"]["ts"]


def test_last_caught_flags_a_raid(tmp_path):
    dbp = tmp_path / "t.db"
    db.init_db(dbp)
    _catch(dbp, 5, iv=1)
    _raid(dbp, 1, iv=2)

    assert db.get_species_stats(GIBLE, db_path=dbp)["last_caught"]["is_raid"] is True


def test_unknown_species_returns_empty_stats(tmp_path):
    dbp = tmp_path / "t.db"
    db.init_db(dbp)

    stats = db.get_species_stats(9999, db_path=dbp)
    assert stats["name"] is None
    assert stats["last_caught"] is None
    assert stats["last_location"] is None
    assert stats["periods"]["all"]["caught"] == 0
