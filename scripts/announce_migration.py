"""One-off script: posts a single announcement to every channel currently registered in
data/guilds.json, explaining that boss alerts moved from the old channel-wide reaction/meta
system to personal DMs tied to a linked xdoApp account.

Run once, manually, after the bot itself has already been redeployed with the new commands
(so /vincular exists by the time people read this and try it):

    python scripts/announce_migration.py

Not wired into bot.py's own startup -- this is a one-time transition message, not a permanent
feature, so it doesn't need to become a codebase fixture ("has this channel already seen the
announcement?" flags, etc.).
"""
import asyncio
import json
import os
import sys

import discord

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import TOKEN, SITE_BASE_URL  # noqa: E402

GUILDS_FILE = "data/guilds.json"

ANNOUNCEMENT = (
    "📢 **Avisos de boss mudaram de jeito!**\n\n"
    "Os alertas de boss deste canal deixaram de ser coletivos (com reação ✅ e meta de "
    "confirmações). Agora cada pessoa recebe os avisos **pessoalmente, por DM**, com base "
    "nas próprias preferências e no próprio checklist -- os mesmos que já existem no site.\n\n"
    f"Pra continuar recebendo avisos de boss:\n"
    f"1. Entre em {SITE_BASE_URL}/settings e gere um código em **Discord**.\n"
    f"2. Mande `/vincular <codigo>` pro bot, por DM.\n\n"
    "Os comandos de consulta (`/proximo`, `/lista`, `/spawn`) e de utilidade "
    "(`/setup_limpeza`, `/alarme`) deste canal continuam funcionando normalmente."
)


async def main():
    try:
        with open(GUILDS_FILE, "r", encoding="utf-8") as f:
            guilds_data = json.load(f)
    except FileNotFoundError:
        print(f"[announce] {GUILDS_FILE} não encontrado, nada a anunciar.")
        return

    channel_ids = [
        int(channel_id)
        for guild in guilds_data.get("guilds", {}).values()
        for channel_id in guild.get("channels", {})
    ]
    if not channel_ids:
        print("[announce] Nenhum canal registrado.")
        return

    intents = discord.Intents.default()
    client = discord.Client(intents=intents)

    @client.event
    async def on_ready():
        print(f"[announce] Conectado como {client.user}. Anunciando em {len(channel_ids)} canal(is)...")
        for channel_id in channel_ids:
            channel = client.get_channel(channel_id)
            if channel is None:
                try:
                    channel = await client.fetch_channel(channel_id)
                except discord.HTTPException as e:
                    print(f"[announce] ⚠️ Não achei o canal {channel_id}: {e}")
                    continue
            try:
                await channel.send(ANNOUNCEMENT)
                print(f"[announce] ✅ Enviado em #{channel.name} ({channel_id})")
            except discord.Forbidden:
                print(f"[announce] ⚠️ Sem permissão em #{getattr(channel, 'name', channel_id)}")
            except discord.HTTPException as e:
                print(f"[announce] ⚠️ Erro em {channel_id}: {e}")
            await asyncio.sleep(1)
        print("[announce] Concluído.")
        await client.close()

    await client.start(TOKEN)


if __name__ == "__main__":
    asyncio.run(main())
