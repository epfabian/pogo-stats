"""
Discord bot that only reads the configured channel where PolygonX posts its
catch/flee embeds via webhook, parses them, and writes them to the SQLite
database in a data-sparing way.

The bot itself does nothing else (no commands, no replies in the channel).

Start (from the project root, with the venv activated):
    python -m bot.bot
"""

import logging
import os
from datetime import datetime, timezone

import discord
from dotenv import load_dotenv

from shared import db
from shared.parser import embed_to_text, parse_catch_embed, parse_raid_embed

load_dotenv()

TOKEN = os.environ["DISCORD_TOKEN"]
CHANNEL_ID = int(os.environ["CHANNEL_ID"])

# Upper bound on how many messages the startup catch-up will look at, so a
# corrupted/very old timestamp can't turn a restart into an unbounded
# history scan. Generous for a single personal-use channel - if this is
# ever actually hit, older missed messages would still be sitting in
# Discord's history and could be picked up by a manual re-run once the
# underlying cause (e.g. a very long outage) is addressed.
CATCH_UP_LIMIT = 2000

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("pogo-bot")

intents = discord.Intents.default()
intents.message_content = True

client = discord.Client(intents=intents)


async def _process_embed(embed, created_at, message_id):
    """Parses a single embed and stores it if it matches a known catch/
    flee/raid pattern. Shared between on_message (live messages) and
    catch_up_missed_messages (startup backfill) so both behave identically.
    Never raises - a parsing or DB failure here is logged and swallowed so
    it can only cost this one embed, not the caller's loop."""
    try:
        text = embed_to_text(embed)

        parsed = parse_catch_embed(text)
        if parsed is not None:
            db.insert_catch(
                ts=created_at,
                event_type=parsed["event_type"],
                trainer=parsed["trainer"],
                pokemon_id=parsed["pokemon_id"],
                pokemon_name=parsed["pokemon_name"],
                shiny=parsed["shiny"],
                iv100=parsed["iv100"],
                lat=parsed["lat"],
                lon=parsed["lon"],
                iv_atk=parsed["iv_atk"],
                iv_def=parsed["iv_def"],
                iv_sta=parsed["iv_sta"],
                cp=parsed["cp"],
                level=parsed["level"],
            )
            log.info(
                "%-5s %-15s shiny=%s iv100=%s (%s)",
                parsed["event_type"],
                parsed["pokemon_name"],
                parsed["shiny"],
                parsed["iv100"],
                parsed["trainer"],
            )
            return

        raid = parse_raid_embed(text)
        if raid is not None:
            db.insert_raid(
                ts=created_at,
                trainer=raid["trainer"],
                pokemon_id=raid["pokemon_id"],
                pokemon_name=raid["pokemon_name"],
                shiny=raid["shiny"],
                iv100=raid["iv100"],
                lat=raid["lat"],
                lon=raid["lon"],
                iv_atk=raid["iv_atk"],
                iv_def=raid["iv_def"],
                iv_sta=raid["iv_sta"],
                cp=raid["cp"],
                level=raid["level"],
            )
            log.info(
                "raid  %-15s shiny=%s iv100=%s (%s)",
                raid["pokemon_name"],
                raid["shiny"],
                raid["iv100"],
                raid["trainer"],
            )
            return

        # Neither catch/flee nor raid -> some other, not-yet-supported
        # PolygonX alert type. Hook up another parser here if needed.
    except Exception:
        log.exception(
            "Failed to process an embed from message %s - skipping it, continuing with any others.",
            message_id,
        )


async def catch_up_missed_messages():
    """Runs once on startup: looks for catch/flee/raid messages that arrived
    in the channel while the bot wasn't running (crash, restart, deployment,
    server reboot, etc.) and processes them retroactively, so an outage
    doesn't silently turn into lost data. Uses the most recent event already
    in the database as the starting point - on a brand new, empty database
    there's nothing to catch up to yet, so this is skipped entirely."""
    last_ts = db.get_last_event_ts()
    if last_ts is None:
        log.info("No prior events in the database yet - skipping startup catch-up.")
        return

    after_dt = datetime.fromisoformat(last_ts).replace(tzinfo=timezone.utc)

    try:
        channel = await client.fetch_channel(CHANNEL_ID)
    except (discord.NotFound, discord.Forbidden):
        log.warning("Could not access channel %s for startup catch-up - skipping.", CHANNEL_ID)
        return

    log.info("Checking for messages missed while offline (since %s UTC)...", last_ts)
    processed = 0
    try:
        async for message in channel.history(after=after_dt, oldest_first=True, limit=CATCH_UP_LIMIT):
            if not message.embeds:
                continue
            for embed in message.embeds:
                await _process_embed(embed, message.created_at, message.id)
                processed += 1
    except discord.Forbidden:
        log.warning("Missing 'Read Message History' permission - can't catch up on missed messages.")
        return
    except Exception:
        log.exception("Startup catch-up failed partway through - some missed messages may not have been processed.")
        return

    if processed:
        log.info("Catch-up complete: processed %d embed(s) from messages received while offline.", processed)
    else:
        log.info("Catch-up complete: nothing was missed.")


@client.event
async def on_ready():
    db.init_db()
    log.info("Logged in as %s, listening on channel %s", client.user, CHANNEL_ID)
    await catch_up_missed_messages()


@client.event
async def on_message(message):
    if message.channel.id != CHANNEL_ID:
        return
    if not message.embeds:
        return

    for embed in message.embeds:
        await _process_embed(embed, message.created_at, message.id)


if __name__ == "__main__":
    client.run(TOKEN)
