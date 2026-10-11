"""Slash commands.

Every reply is ephemeral (only the person who ran the command sees it), and
commands only work inside a server, never in DMs.
"""

import math

import discord
from discord import app_commands


@app_commands.guild_only()
async def ping(interaction: discord.Interaction) -> None:
    latency = interaction.client.latency  # seconds; inf/nan before the first heartbeat
    text = f"pong ({round(latency * 1000)} ms)" if math.isfinite(latency) else "pong"
    await interaction.response.send_message(text, ephemeral=True)


def register_commands(tree: app_commands.CommandTree) -> None:
    # Handlers stay plain functions (easy to call in tests); Command objects are
    # built here. Decorators like guild_only() on the function carry over.
    tree.add_command(
        app_commands.Command(
            name="ping", description="Check that needtoknow is online.", callback=ping
        )
    )
