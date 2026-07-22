"""Tests for the multi-account History filters added in shared/db.py:
get_trainers(), plus the trainer/name_query parameters on get_history() and
get_raid_history()."""

from datetime import datetime, timedelta, timezone

from shared import db

BASE = datetime(2026, 7, 22, 12, 0, 0, tzinfo=timezone.utc)


def _catch(dbp, i, trainer, name, pid):
    db.insert_catch(
        ts=BASE + timedelta(minutes=i), event_type="catch", trainer=trainer,
        pokemon_id=pid, pokemon_name=name, shiny=False, iv100=False, db_path=dbp,
    )


def _seed(dbp):
    db.init_db(dbp)
    _catch(dbp, 0, "Ash", "Pikachu", 25)
    _catch(dbp, 1, "Ash", "Bulbasaur", 1)
    _catch(dbp, 2, "Misty", "Staryu", 120)
    _catch(dbp, 3, "Misty", "Pikachu", 25)


def test_get_trainers_distinct_sorted(tmp_path):
    dbp = tmp_path / "t.db"
    _seed(dbp)
    assert db.get_trainers(db_path=dbp) == ["Ash", "Misty"]


def test_filter_by_trainer(tmp_path):
    dbp = tmp_path / "t.db"
    _seed(dbp)
    result = db.get_history(trainer="Ash", db_path=dbp)
    assert result["total"] == 2
    assert {e["trainer"] for e in result["entries"]} == {"Ash"}


def test_search_by_pokemon_name(tmp_path):
    dbp = tmp_path / "t.db"
    _seed(dbp)
    result = db.get_history(name_query="pika", db_path=dbp)  # case-insensitive
    assert result["total"] == 2
    assert {e["pokemon_name"] for e in result["entries"]} == {"Pikachu"}


def test_trainer_and_search_combined(tmp_path):
    dbp = tmp_path / "t.db"
    _seed(dbp)
    result = db.get_history(trainer="Misty", name_query="pika", db_path=dbp)
    assert result["total"] == 1
    assert result["entries"][0]["trainer"] == "Misty"
    assert result["entries"][0]["pokemon_name"] == "Pikachu"
