"""Tests for the raid/catch de-duplication in shared/db.py. Some PolygonX
configs emit BOTH a raid-completion embed and a plain catch embed for the same
raid catch; db.py must make sure such a catch is counted once, with the raid
row treated as authoritative. This is subtle, order-dependent logic that's easy
to break in a refactor - hence the coverage."""

from datetime import datetime, timedelta, timezone

from shared import db

BASE = datetime(2026, 7, 22, 12, 0, 0, tzinfo=timezone.utc)


def _ts(offset_seconds=0):
    return BASE + timedelta(seconds=offset_seconds)


def _add_catch(dbp, ts, iv=(10, 10, 10), trainer="Ash", pid=25, name="Pikachu"):
    db.insert_catch(
        ts=ts, event_type="catch", trainer=trainer, pokemon_id=pid, pokemon_name=name,
        shiny=False, iv100=False, iv_atk=iv[0], iv_def=iv[1], iv_sta=iv[2], db_path=dbp,
    )


def _add_raid(dbp, ts, iv=(10, 10, 10), trainer="Ash", pid=25, name="Pikachu"):
    db.insert_raid(
        ts=ts, trainer=trainer, pokemon_id=pid, pokemon_name=name,
        shiny=False, iv100=False, iv_atk=iv[0], iv_def=iv[1], iv_sta=iv[2], db_path=dbp,
    )


def test_catch_then_matching_raid_removes_catch(tmp_path):
    dbp = tmp_path / "t.db"
    db.init_db(dbp)
    _add_catch(dbp, _ts(0))
    _add_raid(dbp, _ts(30))
    assert db.get_history(db_path=dbp)["total"] == 0
    assert db.get_raid_history(db_path=dbp)["total"] == 1


def test_raid_then_matching_catch_skips_catch(tmp_path):
    dbp = tmp_path / "t.db"
    db.init_db(dbp)
    _add_raid(dbp, _ts(0))
    _add_catch(dbp, _ts(30))
    assert db.get_history(db_path=dbp)["total"] == 0
    assert db.get_raid_history(db_path=dbp)["total"] == 1


def test_different_iv_keeps_both(tmp_path):
    dbp = tmp_path / "t.db"
    db.init_db(dbp)
    _add_catch(dbp, _ts(0), iv=(10, 10, 10))
    _add_raid(dbp, _ts(30), iv=(11, 10, 10))
    assert db.get_history(db_path=dbp)["total"] == 1
    assert db.get_raid_history(db_path=dbp)["total"] == 1


def test_outside_time_window_keeps_both(tmp_path):
    dbp = tmp_path / "t.db"
    db.init_db(dbp)
    _add_catch(dbp, _ts(0))
    _add_raid(dbp, _ts(db.DEDUPE_WINDOW_SECONDS + 60))
    assert db.get_history(db_path=dbp)["total"] == 1
    assert db.get_raid_history(db_path=dbp)["total"] == 1
