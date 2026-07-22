"""Tests for shared/parser.py - the regex-based PolygonX embed parser, which
is the most fragile and most critical part of the pipeline (a wrong parse
silently corrupts every downstream stat). These exercise the flattened embed
*text* directly, which is exactly what embed_to_text() feeds the parsers."""

from shared.parser import parse_catch_embed, parse_raid_embed

CATCH_TEXT = """AshKetchum
Pokemon caught successfully!
Pokemon: Pikachu (25:3002:0:2)
IV: 10/6/10
Level: 3
CP: 63

Location: 40.800186,-73.965601
Timestamp: 09:37:28
No cooldown"""

FLEE_TEXT = """AshKetchum
Pokemon flee!
Pokemon: Rattata (19:0:0:0)
IV: 4/2/8
Level: 5
CP: 120"""

RAID_TEXT = """AshKetchum
Complete Raid Battle Encounter!
Pokemon caught successfully!
Pokemon: Mewtwo (150:0:0:0)
IV: 15/15/15
Level: 20
CP: 2317
Location: 48.137400,11.575500"""


def test_parse_catch_basic_fields():
    result = parse_catch_embed(CATCH_TEXT)
    assert result is not None
    assert result["event_type"] == "catch"
    assert result["pokemon_id"] == 25
    assert result["pokemon_name"] == "Pikachu"
    assert (result["iv_atk"], result["iv_def"], result["iv_sta"]) == (10, 6, 10)
    assert result["iv100"] is False
    assert result["cp"] == 63
    assert result["level"] == 3
    assert result["shiny"] is False
    assert result["trainer"] == "AshKetchum"
    assert result["lat"] == 40.800186
    assert result["lon"] == -73.965601


def test_parse_flee():
    result = parse_catch_embed(FLEE_TEXT)
    assert result is not None
    assert result["event_type"] == "flee"
    assert result["pokemon_name"] == "Rattata"


def test_raid_not_misclassified_as_catch():
    # A raid-completion embed also contains "caught successfully"; the catch
    # parser must defer to the raid parser rather than storing it as a catch.
    assert parse_catch_embed(RAID_TEXT) is None
    raid = parse_raid_embed(RAID_TEXT)
    assert raid is not None
    assert raid["pokemon_id"] == 150
    assert raid["pokemon_name"] == "Mewtwo"


def test_iv100_flagged():
    result = parse_raid_embed(RAID_TEXT)
    assert result["iv100"] is True  # 15/15/15


def test_shiny_detection():
    text = CATCH_TEXT.replace("caught successfully!", "caught successfully! (shiny)")
    result = parse_catch_embed(text)
    assert result["shiny"] is True


def test_missing_pokemon_field_returns_none():
    text = "AshKetchum\nPokemon caught successfully!\nIV: 10/6/10"
    assert parse_catch_embed(text) is None


def test_empty_or_garbage_returns_none():
    assert parse_catch_embed("") is None
    assert parse_catch_embed("some unrelated chatter with no markers") is None
    assert parse_raid_embed("") is None
