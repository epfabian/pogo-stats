"""
Parser for the PolygonX catch/flee embeds.

Rather than relying on the exact internal field structure of the embed
(which tends to differ slightly between Discord/webhook tools depending on
whether something is in the title, description, author, or fields), we
collect the ENTIRE visible text of the embed into a string and search it with
regexes for the known patterns. This is more robust against small formatting
differences.

Known example (from the Discord screenshot):

    SomeTrainerName
    Pokemon caught successfully!
    Pokemon: Pikachu (25:3002:0:2)
    IV: 10/6/10
    Level: 3
    CP: 63

    Location: 40.800186,-73.965601
    Timestamp: 09:37:28
    No cooldown

IMPORTANT regarding shiny detection: the example shown doesn't include a
shiny catch, so it's unclear exactly how PolygonX marks a shiny (its own
field? emoji? the word "shiny" in the text? a different embed color?). The
regex below is a placeholder that reacts to the word "shiny" or a sparkle
emoji. Once a real shiny example is available, adjust this (SHINY_PATTERN or
custom logic).
"""

import re

CATCH_PATTERN = re.compile(r"caught successfully", re.IGNORECASE)
FLEE_PATTERN = re.compile(r"\bflee\b", re.IGNORECASE)
RAID_PATTERN = re.compile(r"Complete Raid Battle Encounter", re.IGNORECASE)

POKEMON_PATTERN = re.compile(r"Pokemon:\s*([^\n(]+?)\s*\((\d+)[:)]")

IV_PATTERN = re.compile(r"IV:\s*(\d+)\s*/\s*(\d+)\s*/\s*(\d+)")

CP_PATTERN = re.compile(r"CP:\s*(\d+)")
LEVEL_PATTERN = re.compile(r"Level:\s*(\d+)")

SHINY_PATTERN = re.compile(r"shiny|✨", re.IGNORECASE)

LOCATION_PATTERN = re.compile(r"Location:\s*(-?\d+\.\d+)\s*,\s*(-?\d+\.\d+)")


def embed_to_text(embed):
    parts = []
    if embed.author and embed.author.name:
        parts.append(str(embed.author.name))
    if embed.title:
        parts.append(str(embed.title))
    if embed.description:
        parts.append(str(embed.description))
    for field in embed.fields:
        parts.append(field.name + ": " + str(field.value))
    if embed.footer and embed.footer.text:
        parts.append(str(embed.footer.text))
    return "\n".join(p for p in parts if p)


def _extract_pokemon_fields(text):
    """Extracts the fields that are structured the same way across catch,
    flee, AND raid embeds (Pokemon, IV, CP, level, shiny, location, trainer).
    Returns None if no Pokemon field was found."""
    pokemon_match = POKEMON_PATTERN.search(text)
    if not pokemon_match:
        return None
    pokemon_name = pokemon_match.group(1).strip()
    pokemon_id = int(pokemon_match.group(2))

    iv100 = False
    iv_atk = iv_def = iv_sta = None
    iv_match = IV_PATTERN.search(text)
    if iv_match:
        iv_atk = int(iv_match.group(1))
        iv_def = int(iv_match.group(2))
        iv_sta = int(iv_match.group(3))
        iv100 = iv_atk == 15 and iv_def == 15 and iv_sta == 15

    cp = None
    cp_match = CP_PATTERN.search(text)
    if cp_match:
        cp = int(cp_match.group(1))

    level = None
    level_match = LEVEL_PATTERN.search(text)
    if level_match:
        level = int(level_match.group(1))

    shiny = bool(SHINY_PATTERN.search(text))

    lat = None
    lon = None
    location_match = LOCATION_PATTERN.search(text)
    if location_match:
        lat = float(location_match.group(1))
        lon = float(location_match.group(2))

    trainer = None
    for line in text.splitlines():
        line = line.strip()
        if line:
            trainer = line
            break

    fields = {}
    fields["trainer"] = trainer
    fields["pokemon_id"] = pokemon_id
    fields["pokemon_name"] = pokemon_name
    fields["shiny"] = shiny
    fields["iv100"] = iv100
    fields["iv_atk"] = iv_atk
    fields["iv_def"] = iv_def
    fields["iv_sta"] = iv_sta
    fields["cp"] = cp
    fields["level"] = level
    fields["lat"] = lat
    fields["lon"] = lon
    return fields


def parse_catch_embed(text):
    if RAID_PATTERN.search(text):
        # Raid-completion embeds also contain "caught successfully" (you
        # always catch the Pokemon after winning the raid battle), so without
        # this guard they'd match CATCH_PATTERN and get stored as a plain
        # catch instead of a raid. Let parse_raid_embed handle these instead.
        return None

    if CATCH_PATTERN.search(text):
        event_type = "catch"
    elif FLEE_PATTERN.search(text):
        event_type = "flee"
    else:
        return None

    fields = _extract_pokemon_fields(text)
    if fields is None:
        return None

    fields["event_type"] = event_type
    return fields


def parse_raid_embed(text):
    """Parses a "Complete Raid Battle Encounter!" embed (catch after winning
    a raid). Returns None if it's not a raid event."""
    if not RAID_PATTERN.search(text):
        return None

    return _extract_pokemon_fields(text)
