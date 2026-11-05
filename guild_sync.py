"""Guild role/member/permission sync -- shared by cogs/alerts.py's 30-min periodic routine
and cogs/account.py's /vincular_canal (which runs the same sync immediately after a channel
is claimed, instead of waiting up to 30 min for the next scheduled pass). Kept in its own
module, not inside either cog, so account.py can call it without importing alerts.py (which
itself imports BossAlertView from account.py -- a cog-to-cog import would be circular).
"""
import discord
from discord.ext import commands

import api_client


async def sync_guild_roles(guild: discord.Guild):
    """guild.roles is a local gateway cache -- this costs the bot no extra Discord API
    call. @everyone (the default role) is skipped, it's not mentionable as a "role"."""
    roles = [
        {"role_id": str(r.id), "name": r.name, "color": str(r.color) if r.color.value else None}
        for r in guild.roles
        if not r.is_default()
    ]
    await api_client.sync_guild_roles(str(guild.id), roles)


async def sync_linked_members(guild: discord.Guild):
    """One REST call (guild.fetch_members) per guild -- the intersection with a linked
    site account happens server-side, not here, so this never needs to know who's linked."""
    try:
        members = [m async for m in guild.fetch_members(limit=None)]
    except discord.HTTPException as e:
        print(f"[guild_sync] fetch_members falhou em {guild.id}: {e}")
        return
    payload = [{"discord_id": str(m.id), "username": m.name} for m in members if not m.bot]
    await api_client.sync_linked_members(str(guild.id), payload)


async def check_channel_permissions(bot: commands.Bot, guild: discord.Guild):
    """Permissões vêm do cache local do gateway -- sem chamada extra ao Discord. Só checa
    canais de fato registrados (pergunta à API, não assume "todo canal de texto")."""
    channels = await api_client.get_guild_channels(str(guild.id))
    for ch in channels:
        channel = guild.get_channel(int(ch["channel_id"]))
        if channel is None:
            continue
        perms = channel.permissions_for(guild.me)
        missing = []
        if not perms.send_messages:
            missing.append("send_messages")
        if not perms.embed_links:
            missing.append("embed_links")
        if not perms.mention_everyone:
            missing.append("mention_everyone")
        await api_client.report_channel_health(str(guild.id), ch["channel_id"], missing_permissions=missing)


async def sync_guild(bot: commands.Bot, guild: discord.Guild):
    """Runs all three in sequence -- used by both the 30-min loop and /vincular_canal."""
    await sync_guild_roles(guild)
    await sync_linked_members(guild)
    await check_channel_permissions(bot, guild)
