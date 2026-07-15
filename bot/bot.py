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

import discord
from dotenv import load_dotenv

from shared import db
from shared.parser import embed_to_text, parse_catch_embed, parse_raid_embed

load_dotenv()

TOKEN = os.environ["DISCORD_TOKEN"]
CHANNEL_ID = int(os.environ["CHANNEL_ID"])

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("pogo-bot")

intents = discord.Intents.default()
intents.message_content = True

client = discord.Client(intents=intents)


@client.event
async def on_ready():
    db.init_db()
    log.info("Logged in as %s, listening on channel %s", client.user, CHANNEL_ID)


@client.event
async def on_message(message):
    if message.channel.id != CHANNEL_ID:
        return
    if not message.embeds:
        return

    for embed in message.embeds:
        text = embed_to_text(embed)

        parsed = parse_catch_embed(text)
        if parsed is not None:
            db.insert_catch(
                ts=message.created_at,
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
            continue

        raid = parse_raid_embed(text)
        if raid is not None:
            db.insert_raid(
                ts=message.created_at,
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
            continue

        # Neither catch/flee nor raid -> some other, not-yet-supported
        # PolygonX alert type. Hook up another parser here if needed.


if __name__ == "__main__":
    client.run(TOKEN)
