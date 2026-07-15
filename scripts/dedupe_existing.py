"""
One-off cleanup script for already-existing duplicate entries.

Background: some PolygonX configurations send both a "Complete Raid Battle
Encounter!" message AND a regular "caught successfully" embed for the same
raid catch. Before the dedupe logic was added to shared/db.py, such a catch
ended up duplicated in the database: once in the catches table, once in the
raids table.

This script looks through the catches table for entries that share
(trainer, Pokemon, IV values) with an existing raid entry and occur close
together in time, and deletes the redundant catches entry - the raid entry
is kept.

Usage (from the project root, with the venv activated):
    python -m scripts.dedupe_existing            # shows what would be deleted
    python -m scripts.dedupe_existing --apply     # actually deletes

Dry run by default (nothing is changed) - only with --apply are the
duplicates actually removed.
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from shared import db  # noqa: E402
from shared.db import _find_recent_match, _normalize_ts, DEDUPE_WINDOW_SECONDS  # noqa: E402


def find_duplicates(conn, window_seconds=DEDUPE_WINDOW_SECONDS):
    raids = conn.execute(
        "SELECT id, ts, trainer, pokemon_id, pokemon_name, iv_atk, iv_def, iv_sta FROM raids"
    ).fetchall()

    duplicates = []
    for raid in raids:
        catch_id = _find_recent_match(
            conn, "catches", raid["trainer"], raid["pokemon_id"],
            raid["iv_atk"], raid["iv_def"], raid["iv_sta"], raid["ts"],
            window_seconds=window_seconds,
        )
        if catch_id is not None:
            duplicates.append((catch_id, raid["pokemon_name"], raid["trainer"], raid["ts"]))
    return duplicates


def main():
    parser = argparse.ArgumentParser(description="Removes catch entries that are already recorded as a raid catch.")
    parser.add_argument("--apply", action="store_true", help="Actually delete duplicates (otherwise just show them)")
    parser.add_argument("--db", default=str(db.DB_PATH), help="Path to the SQLite database")
    args = parser.parse_args()

    with db.get_conn(args.db) as conn:
        duplicates = find_duplicates(conn)

        if not duplicates:
            print("No duplicates found.")
            return

        print(f"Found {len(duplicates)} duplicate catch entry/entries (already recorded as a raid):")
        for catch_id, name, trainer, ts in duplicates:
            print(f"  catches.id={catch_id}  {name}  ({trainer}, {ts})")

        if args.apply:
            for catch_id, _, _, _ in duplicates:
                conn.execute("DELETE FROM catches WHERE id = ?", (catch_id,))
            conn.commit()
            print(f"\nDeleted {len(duplicates)} entry/entries.")
        else:
            print("\nDry run - nothing deleted. Run again with --apply to actually delete.")


if __name__ == "__main__":
    main()
