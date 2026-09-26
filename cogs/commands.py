import discord
from discord.ext import commands
from discord import app_commands
import json
import os
import uuid
from datetime import datetime, timedelta, timezone
from typing import Union
from zoneinfo import ZoneInfo
from i18n import t, set_guild_lang, get_boss_name

import api_client
from spawn_calc import next_spawn_time

GUILDS_FILE = "data/guilds.json"

EMOJIS = {
    "PICO": "🔶",
    "PRACA": "🟦",
    "MB": "🔴",
    "WB": "🌍",
    "EVENTO": "🎉",
}

COLORS = {
    "PICO": 0xD85A30,
    "PRACA": 0x00BFFF,
    "MB": 0xBA7517,
    "WB": 0xFF0000,
    "EVENTO": 0xFFD700,
}

TIMEZONE_MAP = {
    "SA": "America/Sao_Paulo",
    "NA": "America/New_York",
    "EU": "Europe/Paris",
    "INMENA": "Asia/Dubai",
    "ASIA": "Asia/Shanghai",
}


def load_json(file_path):
    if not os.path.exists(file_path):
        return {}
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def load_guilds():
    if not os.path.exists(GUILDS_FILE):
        return {"guilds": {}}
    try:
        with open(GUILDS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"guilds": {}}


def save_guilds(data):
    os.makedirs(os.path.dirname(GUILDS_FILE), exist_ok=True)
    with open(GUILDS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def ensure_guild_channels(data, guild_id):
    gid = str(guild_id)
    if gid not in data["guilds"]:
        data["guilds"][gid] = {"channels": {}}
    if "channels" not in data["guilds"][gid]:
        data["guilds"][gid]["channels"] = {}
    return data["guilds"][gid]["channels"]


def get_channel_config(guild_id, channel_id):
    data = load_guilds()
    gid = str(guild_id)
    cid = str(channel_id)
    cfg = data.get("guilds", {}).get(gid, {}).get("channels", {}).get(cid)
    if cfg is None:
        return None, data
    cfg.setdefault("timezone", "America/Sao_Paulo")
    cfg.setdefault("cleanup_enabled", False)
    cfg.setdefault("cleanup_time", None)
    cfg.setdefault("cleanup_last_ran", None)
    return cfg, data


class Commands(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    # ====================== SETUP CANAL ======================
    @app_commands.command(name="setup_canal", description="Registra um canal para receber avisos de boss")
    @app_commands.checks.has_permissions(administrator=True)
    @app_commands.allowed_contexts(guilds=True, dms=False, private_channels=False)
    async def setup_canal(self, interaction: discord.Interaction, canal: discord.TextChannel):
        await interaction.response.defer(ephemeral=True)
        loc = interaction.locale
        data = load_guilds()
        channels = ensure_guild_channels(data, interaction.guild_id)
        cid = str(canal.id)
        if cid not in channels:
            channels[cid] = {}
        cfg = channels[cid]
        cfg.setdefault("timezone", "America/Sao_Paulo")
        cfg.setdefault("cleanup_enabled", False)
        cfg.setdefault("cleanup_time", None)
        cfg.setdefault("cleanup_last_ran", None)
        save_guilds(data)
        await interaction.followup.send(t(interaction.guild_id, "canal_registered", locale=loc, channel=canal.mention), ephemeral=True)

    setup_canal.description_localizations = {
        "en-US": "Register a channel for boss alert lookup commands",
        "en-GB": "Register a channel for boss alert lookup commands",
        "es-ES": "Registra un canal para los comandos de consulta de bosses",
    }
    setup_canal.name_localizations = {
        "en-US": "setup_channel",
        "en-GB": "setup_channel",
        "es-ES": "setup_canal",
    }

    # ====================== REMOVE CANAL ======================
    @app_commands.command(name="remove_canal", description="Remove um canal registrado")
    @app_commands.checks.has_permissions(administrator=True)
    @app_commands.allowed_contexts(guilds=True, dms=False, private_channels=False)
    async def remove_canal(self, interaction: discord.Interaction, canal: discord.TextChannel):
        await interaction.response.defer(ephemeral=True)
        loc = interaction.locale
        data = load_guilds()
        gid = str(interaction.guild_id)
        cid = str(canal.id)
        channels = data.get("guilds", {}).get(gid, {}).get("channels", {})
        if cid not in channels:
            await interaction.followup.send(t(interaction.guild_id, "canal_not_found", locale=loc, channel=canal.mention), ephemeral=True)
            return
        del channels[cid]
        save_guilds(data)
        await interaction.followup.send(t(interaction.guild_id, "canal_removed", locale=loc, channel=canal.mention), ephemeral=True)

    remove_canal.description_localizations = {
        "en-US": "Remove a registered channel",
        "en-GB": "Remove a registered channel",
        "es-ES": "Elimina un canal registrado",
    }
    remove_canal.name_localizations = {
        "en-US": "remove_channel",
        "en-GB": "remove_channel",
        "es-ES": "remove_canal",
    }

    # ====================== SETUP TIMEZONE ======================
    @app_commands.command(name="setup_timezone", description="Define o fuso horário de exibição do canal atual")
    @app_commands.checks.has_permissions(administrator=True)
    @app_commands.allowed_contexts(guilds=True, dms=False, private_channels=False)
    @app_commands.choices(timezone=[
        app_commands.Choice(name="SA - Brasil (Padrão)", value="SA"),
        app_commands.Choice(name="NA - América do Norte", value="NA"),
        app_commands.Choice(name="EU - Europa", value="EU"),
        app_commands.Choice(name="INMENA", value="INMENA"),
        app_commands.Choice(name="ASIA - Ásia", value="ASIA"),
    ])
    async def setup_timezone(self, interaction: discord.Interaction, timezone: str):
        await interaction.response.defer(ephemeral=True)
        loc = interaction.locale
        cfg, data = get_channel_config(interaction.guild_id, interaction.channel_id)
        if cfg is None:
            await interaction.followup.send(t(interaction.guild_id, "err_channel_not_registered", locale=loc), ephemeral=True)
            return
        tz_name = TIMEZONE_MAP.get(timezone.upper(), timezone)
        try:
            ZoneInfo(tz_name)
        except Exception:
            await interaction.followup.send(t(interaction.guild_id, "timezone_invalid", locale=loc), ephemeral=True)
            return
        cfg["timezone"] = tz_name
        save_guilds(data)
        await interaction.followup.send(t(interaction.guild_id, "timezone_set", locale=loc, timezone=timezone), ephemeral=True)

    setup_timezone.description_localizations = {
        "en-US": "Set the display timezone for the current channel",
        "en-GB": "Set the display timezone for the current channel",
        "es-ES": "Define la zona horaria de visualización del canal actual",
    }
    setup_timezone.name_localizations = {
        "en-US": "setup_timezone",
        "en-GB": "setup_timezone",
        "es-ES": "setup_timezone",
    }

    # ====================== SETUP LANGUAGE ======================
    @app_commands.command(name="setup_language", description="Define o idioma do bot para este servidor")
    @app_commands.checks.has_permissions(administrator=True)
    @app_commands.allowed_contexts(guilds=True, dms=False, private_channels=False)
    @app_commands.choices(language=[
        app_commands.Choice(name="Português (PT-BR)", value="pt"),
        app_commands.Choice(name="English (EN)", value="en"),
        app_commands.Choice(name="Español (ES)", value="es"),
    ])
    async def setup_language(self, interaction: discord.Interaction, language: str):
        await interaction.response.defer(ephemeral=True)
        loc = interaction.locale
        data = load_guilds()
        gid = str(interaction.guild_id)
        if gid not in data["guilds"]:
            data["guilds"][gid] = {"channels": {}}
        data["guilds"][gid]["language"] = language
        save_guilds(data)
        set_guild_lang(interaction.guild_id, language)
        await interaction.followup.send(t(interaction.guild_id, "language_set", locale=loc, language=language), ephemeral=True)

    setup_language.description_localizations = {
        "en-US": "Set the bot language for this server",
        "en-GB": "Set the bot language for this server",
        "es-ES": "Define el idioma del bot para este servidor",
    }
    setup_language.name_localizations = {
        "en-US": "setup_language",
        "en-GB": "setup_language",
        "es-ES": "setup_idioma",
    }

    # ====================== CONFIG ======================
    @app_commands.command(name="config", description="Mostra as configurações do canal atual")
    async def config(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        loc = interaction.locale
        gid = str(interaction.guild_id)
        cfg, data = get_channel_config(interaction.guild_id, interaction.channel_id)
        if cfg is None:
            await interaction.followup.send(t(interaction.guild_id, "err_channel_not_registered", locale=loc), ephemeral=True)
            return

        tz = cfg.get("timezone", "America/Sao_Paulo")
        language = data.get("guilds", {}).get(gid, {}).get("language", "pt")

        if cfg.get("cleanup_enabled") and cfg.get("cleanup_time"):
            cleanup_text = t(gid, "config_cleanup_enabled", locale=loc, time=cfg["cleanup_time"], tz=tz)
        else:
            cleanup_text = t(gid, "config_cleanup_disabled", locale=loc)

        embed = discord.Embed(title=t(gid, "config_title", locale=loc), color=0x7F77DD)
        embed.add_field(name=t(gid, "config_field_channel", locale=loc), value=f"<#{interaction.channel_id}>", inline=False)
        embed.add_field(name=t(gid, "config_field_timezone", locale=loc), value=tz, inline=True)
        embed.add_field(name=t(gid, "config_field_language", locale=loc), value=language, inline=True)
        embed.add_field(name=t(gid, "config_field_cleanup", locale=loc), value=cleanup_text, inline=False)
        embed.set_footer(text=t(gid, "config_footer", locale=loc))
        await interaction.followup.send(embed=embed, ephemeral=True)

    config.description_localizations = {
        "en-US": "Show the current channel settings",
        "en-GB": "Show the current channel settings",
        "es-ES": "Muestra la configuración del canal actual",
    }
    config.name_localizations = {
        "en-US": "config",
        "en-GB": "config",
        "es-ES": "config",
    }

    # ====================== PRÓXIMOS BOSSES ======================
    @app_commands.command(name="proximo", description="Mostra os próximos 3 bosses a spawnar")
    async def proximo(self, interaction: discord.Interaction):
        await interaction.response.defer()
        loc = interaction.locale
        cfg, _ = get_channel_config(interaction.guild_id, interaction.channel_id)
        if cfg is None:
            await interaction.followup.send(t(interaction.guild_id, "err_channel_not_registered", locale=loc), ephemeral=True)
            return

        gid = str(interaction.guild_id)
        tz_name = cfg.get("timezone", "America/Sao_Paulo")

        try:
            tz = ZoneInfo(tz_name)
        except Exception:
            tz = ZoneInfo("America/Sao_Paulo")

        bosses = await api_client.get_boss_schedule()
        now = datetime.now(tz)

        upcoming = []
        for boss in bosses:
            if not boss.get("active", False):
                continue
            spawn_time = next_spawn_time(boss["start_time"], boss["respawn_minutes"], now)
            diff_min = (spawn_time - now).total_seconds() / 60
            upcoming.append((spawn_time, diff_min, boss))

        upcoming.sort(key=lambda x: x[0])
        top3 = upcoming[:3]

        if not top3:
            embed = discord.Embed(
                title=t(gid, "upcoming_title", locale=loc),
                description=t(gid, "upcoming_none", locale=loc),
                color=0x555555
            )
            await interaction.followup.send(embed=embed)
            return

        embed = discord.Embed(
            title=t(gid, "upcoming_title", locale=loc),
            color=0x7F77DD,
            description=t(gid, "upcoming_now", locale=loc, time=now.strftime("%H:%M"), tz=tz_name)
        )

        for i, (spawn_time, diff_min, boss) in enumerate(top3, 1):
            emoji = EMOJIS.get(boss["type"], "🔴")
            h = int(diff_min // 60)
            m = int(diff_min % 60)
            time_str = t(gid, "upcoming_time_hours", locale=loc, h=h, m=m) if h > 0 else t(gid, "upcoming_time_mins", locale=loc, m=m)
            loc_str = " | ".join(filter(None, [boss.get("layer"), boss.get("word"), boss.get("map")]))
            embed.add_field(
                name=f"{i}. {emoji} [{boss['type']}] {get_boss_name(boss, gid, loc)}",
                value=t(gid, "upcoming_spawn_value", locale=loc, time=spawn_time.strftime("%H:%M"), time_str=time_str, loc=loc_str),
                inline=False
            )

        await interaction.followup.send(embed=embed)

    proximo.description_localizations = {
        "en-US": "Show the next 3 bosses to spawn",
        "en-GB": "Show the next 3 bosses to spawn",
        "es-ES": "Muestra los próximos 3 bosses en aparecer",
    }
    proximo.name_localizations = {
        "en-US": "next",
        "en-GB": "next",
        "es-ES": "proximo",
    }

    # ====================== LISTA ======================
    @app_commands.command(name="lista", description="Lista todos os bosses de um tipo")
    @app_commands.choices(tipo=[
        app_commands.Choice(name="PICO", value="PICO"),
        app_commands.Choice(name="PRACA", value="PRACA"),
        app_commands.Choice(name="MB", value="MB"),
        app_commands.Choice(name="WB", value="WB"),
        app_commands.Choice(name="EVENTO", value="EVENTO"),
    ])
    async def lista(self, interaction: discord.Interaction, tipo: str):
        await interaction.response.defer()
        loc = interaction.locale
        gid = str(interaction.guild_id)
        bosses = await api_client.get_boss_schedule()
        tipo = tipo.upper()
        filtered = [b for b in bosses if b.get("type") == tipo and b.get("active", False)]
        if not filtered:
            await interaction.followup.send(t(gid, "lista_none", locale=loc, type=tipo), ephemeral=True)
            return

        emoji = EMOJIS.get(tipo, "🔴")
        color = COLORS.get(tipo, 0x555555)
        embeds = []
        chunk_size = 25
        for i in range(0, len(filtered), chunk_size):
            chunk = filtered[i:i + chunk_size]
            page = (i // chunk_size) + 1
            total_pages = (len(filtered) + chunk_size - 1) // chunk_size
            title = f"{emoji} Bosses — {tipo}" + (f" ({page}/{total_pages})" if total_pages > 1 else "")
            embed = discord.Embed(title=title, description=t(gid, "lista_active", locale=loc, count=len(filtered)), color=color)
            for boss in chunk:
                boss_loc = " | ".join(filter(None, [boss.get("layer"), boss.get("word"), boss.get("map")]))
                embed.add_field(
                    name=f"`{boss['id']}` — {get_boss_name(boss, gid, loc)}",
                    value=f"📍 {boss_loc}" if boss_loc else "📍 —",
                    inline=False
                )
            embeds.append(embed)
        await interaction.followup.send(embeds=embeds[:10])

    lista.description_localizations = {
        "en-US": "List all bosses of a type",
        "en-GB": "List all bosses of a type",
        "es-ES": "Lista todos los bosses de un tipo",
    }
    lista.name_localizations = {
        "en-US": "list",
        "en-GB": "list",
        "es-ES": "lista",
    }

    # ====================== SETUP LIMPEZA ======================
    @app_commands.command(name="setup_limpeza", description="Ativa limpeza diária automática deste canal no horário definido")
    @app_commands.describe(hora="Hora do cleanup (0-23)", minuto="Minuto do cleanup (0-59)")
    @app_commands.checks.has_permissions(administrator=True)
    @app_commands.allowed_contexts(guilds=True, dms=False, private_channels=False)
    async def setup_limpeza(self, interaction: discord.Interaction, hora: int, minuto: int):
        await interaction.response.defer(ephemeral=True)
        loc = interaction.locale
        if not (0 <= hora <= 23 and 0 <= minuto <= 59):
            await interaction.followup.send(t(interaction.guild_id, "cleanup_invalid", locale=loc), ephemeral=True)
            return
        cfg, data = get_channel_config(interaction.guild_id, interaction.channel_id)
        if cfg is None:
            await interaction.followup.send(t(interaction.guild_id, "cleanup_channel_not_registered", locale=loc), ephemeral=True)
            return
        cfg["cleanup_enabled"] = True
        cfg["cleanup_time"] = f"{hora:02d}:{minuto:02d}"
        save_guilds(data)
        tz_name = cfg.get("timezone", "America/Sao_Paulo")
        await interaction.followup.send(t(interaction.guild_id, "cleanup_enabled", locale=loc, time=f"{hora:02d}:{minuto:02d}", tz=tz_name), ephemeral=True)

    setup_limpeza.description_localizations = {
        "en-US": "Enable daily automatic cleanup for this channel at the defined time",
        "en-GB": "Enable daily automatic cleanup for this channel at the defined time",
        "es-ES": "Activa la limpieza diaria automática de este canal a la hora definida",
    }
    setup_limpeza.name_localizations = {
        "en-US": "setup_cleanup",
        "en-GB": "setup_cleanup",
        "es-ES": "setup_limpieza",
    }

    # ====================== DESATIVAR LIMPEZA ======================
    @app_commands.command(name="desativar_limpeza", description="Desativa a limpeza diária automática deste canal")
    @app_commands.checks.has_permissions(administrator=True)
    @app_commands.allowed_contexts(guilds=True, dms=False, private_channels=False)
    async def desativar_limpeza(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        loc = interaction.locale
        cfg, data = get_channel_config(interaction.guild_id, interaction.channel_id)
        if cfg is None:
            await interaction.followup.send(t(interaction.guild_id, "cleanup_channel_not_registered_short", locale=loc), ephemeral=True)
            return
        cfg["cleanup_enabled"] = False
        save_guilds(data)
        await interaction.followup.send(t(interaction.guild_id, "cleanup_disabled", locale=loc), ephemeral=True)

    desativar_limpeza.description_localizations = {
        "en-US": "Disable the daily automatic cleanup for this channel",
        "en-GB": "Disable the daily automatic cleanup for this channel",
        "es-ES": "Desactiva la limpieza diaria automática de este canal",
    }
    desativar_limpeza.name_localizations = {
        "en-US": "disable_cleanup",
        "en-GB": "disable_cleanup",
        "es-ES": "desactivar_limpieza",
    }

    # ====================== SPAWN ======================
    @app_commands.command(name="spawn", description="Exibe o próximo horário de spawn de um boss pelo ID")
    async def spawn(self, interaction: discord.Interaction, boss_id: str):
        await interaction.response.defer()
        loc = interaction.locale
        cfg, _ = get_channel_config(interaction.guild_id, interaction.channel_id)
        if cfg is None:
            await interaction.followup.send(t(interaction.guild_id, "err_channel_not_registered", locale=loc), ephemeral=True)
            return

        gid = str(interaction.guild_id)
        tz_name = cfg.get("timezone", "America/Sao_Paulo")
        try:
            tz = ZoneInfo(tz_name)
        except Exception:
            tz = ZoneInfo("America/Sao_Paulo")

        bosses = await api_client.get_boss_schedule()
        boss = next((b for b in bosses if b["id"] == boss_id.lower()), None)
        if boss is None:
            await interaction.followup.send(t(gid, "spawn_not_found", locale=loc, boss_id=boss_id), ephemeral=True)
            return

        now = datetime.now(tz)
        spawn_time = next_spawn_time(boss["start_time"], boss["respawn_minutes"], now)
        diff_min = (spawn_time - now).total_seconds() / 60
        h = int(diff_min // 60)
        m = int(diff_min % 60)
        time_str = t(gid, "upcoming_time_hours", locale=loc, h=h, m=m) if h > 0 else t(gid, "upcoming_time_mins", locale=loc, m=m)
        emoji = EMOJIS.get(boss["type"], "🔴")
        color = COLORS.get(boss["type"], 0x555555)
        boss_loc = " | ".join(filter(None, [boss.get("layer"), boss.get("word"), boss.get("map")]))

        embed = discord.Embed(
            title=f"{emoji} [{boss['type']}] {get_boss_name(boss, gid, loc)}",
            color=color,
            description=t(gid, "spawn_now", locale=loc, time=now.strftime("%H:%M"), tz=tz_name)
        )
        embed.add_field(name=t(gid, "spawn_next_label", locale=loc), value=t(gid, "spawn_next_value", locale=loc, time=spawn_time.strftime("%H:%M"), time_str=time_str), inline=False)
        if boss_loc:
            embed.add_field(name=t(gid, "spawn_location", locale=loc), value=boss_loc, inline=False)
        await interaction.followup.send(embed=embed)

    spawn.description_localizations = {
        "en-US": "Show the next spawn time of a boss by ID",
        "en-GB": "Show the next spawn time of a boss by ID",
        "es-ES": "Muestra el próximo horario de spawn de un boss por ID",
    }
    spawn.name_localizations = {
        "en-US": "spawn",
        "en-GB": "spawn",
        "es-ES": "spawn",
    }

    # ====================== ALARME ======================
    @app_commands.command(name="alarme", description="Cria um alarme para mencionar um usuário ou cargo daqui a X minutos")
    @app_commands.describe(
        minutos="Minutos até o alarme disparar (1–1440)",
        alvo="Usuário ou cargo a ser mencionado",
        mensagem="Mensagem opcional do alarme"
    )
    @app_commands.checks.has_permissions(administrator=True)
    @app_commands.allowed_contexts(guilds=True, dms=False, private_channels=False)
    async def alarme(self, interaction: discord.Interaction, minutos: int, alvo: Union[discord.Member, discord.Role], mensagem: str):
        await interaction.response.defer(ephemeral=True)
        loc = interaction.locale
        gid = str(interaction.guild_id)
        if not (1 <= minutos <= 1440):
            await interaction.followup.send(t(gid, "alarm_range", locale=loc), ephemeral=True)
            return

        mention_str = alvo.mention
        fire_at = datetime.now(timezone.utc) + timedelta(minutes=minutos)
        alarm_id = f"alarm_{uuid.uuid4().hex[:8]}"

        alarm = {
            "id": alarm_id,
            "guild_id": gid,
            "channel_id": str(interaction.channel_id),
            "mention_str": mention_str,
            "fire_at": fire_at.isoformat(),
            "mensagem": mensagem,
            "created_by": str(interaction.user)
        }

        alarms_data = load_json(f"data/scheduled_alarms/{gid}.json")
        alarms_data[alarm_id] = alarm
        os.makedirs("data/scheduled_alarms", exist_ok=True)
        with open(f"data/scheduled_alarms/{gid}.json", "w", encoding="utf-8") as f:
            json.dump(alarms_data, f, indent=2, ensure_ascii=False)

        alerts_cog = self.bot.cogs.get("Alerts")
        if alerts_cog is not None:
            alerts_cog.scheduled_alarms.setdefault(gid, {})[alarm_id] = alarm

        unit = t(gid, "alarm_minute", locale=loc) if minutos == 1 else t(gid, "alarm_minutes", locale=loc)
        embed = discord.Embed(
            title=t(gid, "alarm_title", locale=loc),
            color=0xFF8C00,
            description=t(gid, "alarm_desc", locale=loc, minutes=minutos, unit=unit)
        )
        embed.add_field(name=t(gid, "alarm_field_target", locale=loc), value=mention_str, inline=True)
        embed.add_field(name=t(gid, "alarm_field_fire", locale=loc), value=fire_at.strftime("%H:%M UTC"), inline=True)
        embed.add_field(name=t(gid, "alarm_field_id", locale=loc), value=f"`{alarm_id}`", inline=False)
        if mensagem:
            embed.add_field(name=t(gid, "alarm_field_message", locale=loc), value=mensagem, inline=False)
        embed.set_footer(text=t(gid, "alarm_footer", locale=loc, user=interaction.user))
        await interaction.followup.send(embed=embed, ephemeral=True)

    alarme.description_localizations = {
        "en-US": "Create an alarm to mention a user or role after X minutes",
        "en-GB": "Create an alarm to mention a user or role after X minutes",
        "es-ES": "Crea una alarma para mencionar un usuario o rol en X minutos",
    }
    alarme.name_localizations = {
        "en-US": "alarm",
        "en-GB": "alarm",
        "es-ES": "alarma",
    }

    # ====================== LISTAR ALARMES ======================
    @app_commands.command(name="listar_alarmes", description="Lista todos os alarmes pendentes neste canal")
    @app_commands.checks.has_permissions(administrator=True)
    @app_commands.allowed_contexts(guilds=True, dms=False, private_channels=False)
    async def listar_alarmes(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        loc = interaction.locale
        gid = str(interaction.guild_id)
        channel_id = str(interaction.channel_id)
        now = datetime.now(timezone.utc)

        alarms_raw = load_json(f"data/scheduled_alarms/{gid}.json")
        channel_alarms = [a for a in alarms_raw.values() if a.get("channel_id") == channel_id]

        if not channel_alarms:
            await interaction.followup.send(t(gid, "alarms_none", locale=loc), ephemeral=True)
            return

        channel_alarms.sort(key=lambda a: a.get("fire_at", ""))
        embed = discord.Embed(
            title=t(gid, "alarms_title", locale=loc),
            color=0xFF8C00,
            description=t(gid, "alarms_count", locale=loc, count=len(channel_alarms))
        )

        for alarm in channel_alarms[:25]:
            try:
                fire_at = datetime.fromisoformat(alarm["fire_at"])
                remaining = fire_at - now
                total_sec = int(remaining.total_seconds())
                if total_sec < 0:
                    time_str = t(gid, "alarms_firing_soon", locale=loc)
                else:
                    h, rem = divmod(total_sec, 3600)
                    m, s = divmod(rem, 60)
                    time_str = t(gid, "upcoming_time_hours", locale=loc, h=h, m=m) if h > 0 else f"{m}min {s}s"
            except (KeyError, ValueError):
                time_str = "?"

            field_name = f"`{alarm['id']}` — em {time_str}"
            field_value = (
                f"{t(gid, 'alarms_field_target', locale=loc)} {alarm.get('mention_str', '?')}\n"
                f"{t(gid, 'alarms_field_created_by', locale=loc)} {alarm.get('created_by', '?')}"
            )
            if alarm.get("mensagem"):
                field_value += f"\n{t(gid, 'alarms_field_message', locale=loc)} {alarm['mensagem']}"
            embed.add_field(name=field_name, value=field_value, inline=False)

        embed.set_footer(text=t(gid, "alarms_footer", locale=loc))
        await interaction.followup.send(embed=embed, ephemeral=True)

    listar_alarmes.description_localizations = {
        "en-US": "List all pending alarms in this channel",
        "en-GB": "List all pending alarms in this channel",
        "es-ES": "Lista todas las alarmas pendientes en este canal",
    }
    listar_alarmes.name_localizations = {
        "en-US": "list_alarms",
        "en-GB": "list_alarms",
        "es-ES": "listar_alarmas",
    }

    # ====================== CANCELAR ALARME ======================
    @app_commands.command(name="cancelar_alarme", description="Cancela um alarme pendente pelo ID")
    @app_commands.describe(alarm_id="ID do alarme (ex: alarm_3f7a1b2c)")
    @app_commands.checks.has_permissions(administrator=True)
    @app_commands.allowed_contexts(guilds=True, dms=False, private_channels=False)
    async def cancelar_alarme(self, interaction: discord.Interaction, alarm_id: str):
        await interaction.response.defer(ephemeral=True)
        loc = interaction.locale
        gid = str(interaction.guild_id)
        file_path = f"data/scheduled_alarms/{gid}.json"
        alarms = load_json(file_path)

        if alarm_id not in alarms:
            await interaction.followup.send(t(gid, "alarm_not_found", locale=loc, id=alarm_id), ephemeral=True)
            return

        alarm = alarms.pop(alarm_id)
        os.makedirs("data/scheduled_alarms", exist_ok=True)
        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(alarms, f, indent=2, ensure_ascii=False)

        alerts_cog = self.bot.cogs.get("Alerts")
        if alerts_cog is not None:
            alerts_cog.scheduled_alarms.get(gid, {}).pop(alarm_id, None)

        await interaction.followup.send(
            t(gid, "alarm_cancelled", locale=loc, id=alarm_id, target=alarm.get("mention_str", "?"), created_by=alarm.get("created_by", "?")),
            ephemeral=True
        )

    cancelar_alarme.description_localizations = {
        "en-US": "Cancel a pending alarm by ID",
        "en-GB": "Cancel a pending alarm by ID",
        "es-ES": "Cancela una alarma pendiente por ID",
    }
    cancelar_alarme.name_localizations = {
        "en-US": "cancel_alarm",
        "en-GB": "cancel_alarm",
        "es-ES": "cancelar_alarma",
    }


async def setup(bot):
    await bot.add_cog(Commands(bot))
