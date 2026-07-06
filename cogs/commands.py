import discord
from discord.ext import commands
from discord import app_commands
import json
import os
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional, Union
from zoneinfo import ZoneInfo
from i18n import t, set_guild_lang, get_boss_name

GUILDS_FILE = "data/guilds.json"
BOSSES_FILE = "data/bosses.json"

def completed_file(guild_id):
    return f"data/completed/{guild_id}.json"

def silenced_users_file(guild_id):
    return f"data/silenced_users/{guild_id}.json"

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

DEFAULT_ALERT_MINUTES = {
    "PICO": 10, "PRACA": 10, "MB": 15, "WB": 20, "EVENTO": 30
}

DEFAULT_META = {
    "PICO": 5, "PRACA": 5, "MB": 5, "WB": 5, "EVENTO": 5
}

def load_json(file_path):
    if not os.path.exists(file_path):
        return {}
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except:
        return {}

def get_next_spawn(boss, tz):
    now = datetime.now(tz)
    h, m = map(int, boss["start"].split(":"))
    tp = now.replace(hour=h, minute=m, second=0, microsecond=0)
    while tp <= now:
        tp += timedelta(minutes=boss["respawn"])
    return tp

def load_guilds():
    if not os.path.exists(GUILDS_FILE):
        return {"guilds": {}}
    try:
        with open(GUILDS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except:
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
    cfg.setdefault("alert_types", ["PICO", "PRACA", "MB", "WB", "EVENTO"])
    cfg.setdefault("alert_minutes", DEFAULT_ALERT_MINUTES.copy())
    cfg.setdefault("meta", DEFAULT_META.copy())
    cfg.setdefault("role_ping", None)
    cfg.setdefault("user_pings", [])
    cfg.setdefault("cleanup_enabled", False)
    cfg.setdefault("cleanup_time", None)
    cfg.setdefault("cleanup_last_ran", None)
    return cfg, data


class Commands(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    # ====================== SETUP CANAL ======================
    @app_commands.command(name="setup_canal", description="Registra um canal para receber alertas")
    @app_commands.checks.has_permissions(administrator=True)
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
        cfg.setdefault("alert_types", ["PICO", "PRACA", "MB", "WB", "EVENTO"])
        cfg.setdefault("alert_minutes", DEFAULT_ALERT_MINUTES.copy())
        cfg.setdefault("meta", DEFAULT_META.copy())
        cfg.setdefault("role_ping", None)
        cfg.setdefault("user_pings", [])
        cfg.setdefault("cleanup_enabled", False)
        cfg.setdefault("cleanup_time", None)
        cfg.setdefault("cleanup_last_ran", None)
        save_guilds(data)
        await interaction.followup.send(t(interaction.guild_id, "canal_registered", locale=loc, channel=canal.mention), ephemeral=True)

    setup_canal.description_localizations = {
        "en-US": "Register a channel to receive alerts",
        "en-GB": "Register a channel to receive alerts",
        "es-ES": "Registra un canal para recibir alertas",
    }
    setup_canal.name_localizations = {
        "en-US": "setup_channel",
        "en-GB": "setup_channel",
        "es-ES": "setup_canal",
    }

    # ====================== REMOVE CANAL ======================
    @app_commands.command(name="remove_canal", description="Remove um canal de alertas")
    @app_commands.checks.has_permissions(administrator=True)
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
        "en-US": "Remove a channel from alerts",
        "en-GB": "Remove a channel from alerts",
        "es-ES": "Elimina un canal de alertas",
    }
    remove_canal.name_localizations = {
        "en-US": "remove_channel",
        "en-GB": "remove_channel",
        "es-ES": "remove_canal",
    }

    # ====================== SETUP TIMEZONE ======================
    @app_commands.command(name="setup_timezone", description="Define o fuso horário do canal atual")
    @app_commands.checks.has_permissions(administrator=True)
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
        except:
            await interaction.followup.send(t(interaction.guild_id, "timezone_invalid", locale=loc), ephemeral=True)
            return
        cfg["timezone"] = tz_name
        save_guilds(data)
        await interaction.followup.send(t(interaction.guild_id, "timezone_set", locale=loc, timezone=timezone), ephemeral=True)

    setup_timezone.description_localizations = {
        "en-US": "Set the timezone for the current channel",
        "en-GB": "Set the timezone for the current channel",
        "es-ES": "Define la zona horaria del canal actual",
    }
    setup_timezone.name_localizations = {
        "en-US": "setup_timezone",
        "en-GB": "setup_timezone",
        "es-ES": "setup_timezone",
    }

    # ====================== SETUP LANGUAGE ======================
    @app_commands.command(name="setup_language", description="Define o idioma do bot para este servidor")
    @app_commands.checks.has_permissions(administrator=True)
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

    # ====================== SETUP TIPOS ======================
    @app_commands.command(name="setup_tipos", description="Define os tipos de alerta do canal atual (PICO PRACA MB WB EVENTO)")
    @app_commands.checks.has_permissions(administrator=True)
    async def setup_tipos(self, interaction: discord.Interaction, tipos: str):
        await interaction.response.defer(ephemeral=True)
        loc = interaction.locale
        cfg, data = get_channel_config(interaction.guild_id, interaction.channel_id)
        if cfg is None:
            await interaction.followup.send(t(interaction.guild_id, "err_channel_not_registered", locale=loc), ephemeral=True)
            return
        valid_types = ["PICO", "PRACA", "MB", "WB", "EVENTO"]
        type_list = [tp.strip().upper() for tp in tipos.split() if tp.strip().upper() in valid_types]
        if not type_list:
            await interaction.followup.send(t(interaction.guild_id, "types_invalid", locale=loc), ephemeral=True)
            return
        cfg["alert_types"] = type_list
        save_guilds(data)
        await interaction.followup.send(t(interaction.guild_id, "types_set", locale=loc, types=type_list), ephemeral=True)

    setup_tipos.description_localizations = {
        "en-US": "Set alert types for the current channel (PICO PRACA MB WB EVENTO)",
        "en-GB": "Set alert types for the current channel (PICO PRACA MB WB EVENTO)",
        "es-ES": "Define los tipos de alerta del canal actual (PICO PRACA MB WB EVENTO)",
    }
    setup_tipos.name_localizations = {
        "en-US": "setup_types",
        "en-GB": "setup_types",
        "es-ES": "setup_tipos",
    }

    # ====================== SETUP ALERTA ======================
    @app_commands.command(name="setup_alerta", description="Minutos de antecedência por tipo no canal atual")
    @app_commands.checks.has_permissions(administrator=True)
    @app_commands.choices(tipo=[
        app_commands.Choice(name="PICO", value="PICO"),
        app_commands.Choice(name="PRACA", value="PRACA"),
        app_commands.Choice(name="MB", value="MB"),
        app_commands.Choice(name="WB", value="WB"),
        app_commands.Choice(name="EVENTO", value="EVENTO"),
    ])
    async def setup_alerta(self, interaction: discord.Interaction, tipo: str, minutos: int):
        await interaction.response.defer(ephemeral=True)
        loc = interaction.locale
        cfg, data = get_channel_config(interaction.guild_id, interaction.channel_id)
        if cfg is None:
            await interaction.followup.send(t(interaction.guild_id, "err_channel_not_registered", locale=loc), ephemeral=True)
            return
        if minutos < 1 or minutos > 60:
            await interaction.followup.send(t(interaction.guild_id, "alert_minutes_range", locale=loc), ephemeral=True)
            return
        cfg["alert_minutes"][tipo] = minutos
        save_guilds(data)
        await interaction.followup.send(t(interaction.guild_id, "alert_minutes_set", locale=loc, type=tipo, minutes=minutos), ephemeral=True)

    setup_alerta.description_localizations = {
        "en-US": "Minutes of advance notice per type in the current channel",
        "en-GB": "Minutes of advance notice per type in the current channel",
        "es-ES": "Minutos de anticipación por tipo en el canal actual",
    }
    setup_alerta.name_localizations = {
        "en-US": "setup_alert",
        "en-GB": "setup_alert",
        "es-ES": "setup_alerta",
    }

    # ====================== SETUP META ======================
    @app_commands.command(name="meta", description="Define meta de confirmações por tipo no canal atual")
    @app_commands.checks.has_permissions(administrator=True)
    @app_commands.choices(tipo=[
        app_commands.Choice(name="PICO", value="PICO"),
        app_commands.Choice(name="PRACA", value="PRACA"),
        app_commands.Choice(name="MB", value="MB"),
        app_commands.Choice(name="WB", value="WB"),
        app_commands.Choice(name="EVENTO", value="EVENTO"),
    ])
    async def setup_meta(self, interaction: discord.Interaction, tipo: str, quantidade: int):
        await interaction.response.defer(ephemeral=True)
        loc = interaction.locale
        cfg, data = get_channel_config(interaction.guild_id, interaction.channel_id)
        if cfg is None:
            await interaction.followup.send(t(interaction.guild_id, "err_channel_not_registered", locale=loc), ephemeral=True)
            return
        if quantidade < 1 or quantidade > 100:
            await interaction.followup.send(t(interaction.guild_id, "meta_range", locale=loc), ephemeral=True)
            return
        cfg["meta"][tipo] = quantidade
        save_guilds(data)
        await interaction.followup.send(t(interaction.guild_id, "meta_set", locale=loc, type=tipo, quantity=quantidade), ephemeral=True)

    setup_meta.description_localizations = {
        "en-US": "Set confirmation goal per type in the current channel",
        "en-GB": "Set confirmation goal per type in the current channel",
        "es-ES": "Define la meta de confirmaciones por tipo en el canal actual",
    }
    setup_meta.name_localizations = {
        "en-US": "goal",
        "en-GB": "goal",
        "es-ES": "meta",
    }

    # ====================== SETUP CARGO ======================
    @app_commands.command(name="setup_cargo", description="Define o cargo para mencionar no canal atual")
    @app_commands.checks.has_permissions(administrator=True)
    async def setup_cargo(self, interaction: discord.Interaction, cargo: discord.Role):
        await interaction.response.defer(ephemeral=True)
        loc = interaction.locale
        cfg, data = get_channel_config(interaction.guild_id, interaction.channel_id)
        if cfg is None:
            await interaction.followup.send(t(interaction.guild_id, "err_channel_not_registered", locale=loc), ephemeral=True)
            return
        cfg["role_ping"] = str(cargo.id)
        save_guilds(data)
        await interaction.followup.send(t(interaction.guild_id, "role_set", locale=loc, role=cargo.mention), ephemeral=True)

    setup_cargo.description_localizations = {
        "en-US": "Set the role to mention in the current channel",
        "en-GB": "Set the role to mention in the current channel",
        "es-ES": "Define el rol a mencionar en el canal actual",
    }
    setup_cargo.name_localizations = {
        "en-US": "setup_role",
        "en-GB": "setup_role",
        "es-ES": "setup_rol",
    }

    # ====================== REMOVE CARGO ======================
    @app_commands.command(name="remove_cargo", description="Remove o cargo de menção do canal atual")
    @app_commands.checks.has_permissions(administrator=True)
    async def remove_cargo(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        loc = interaction.locale
        cfg, data = get_channel_config(interaction.guild_id, interaction.channel_id)
        if cfg is None:
            await interaction.followup.send(t(interaction.guild_id, "err_channel_not_registered", locale=loc), ephemeral=True)
            return
        if not cfg.get("role_ping"):
            await interaction.followup.send(t(interaction.guild_id, "role_none", locale=loc), ephemeral=True)
            return
        cfg["role_ping"] = None
        save_guilds(data)
        await interaction.followup.send(t(interaction.guild_id, "role_removed", locale=loc), ephemeral=True)

    remove_cargo.description_localizations = {
        "en-US": "Remove the mention role from the current channel",
        "en-GB": "Remove the mention role from the current channel",
        "es-ES": "Elimina el rol de mención del canal actual",
    }
    remove_cargo.name_localizations = {
        "en-US": "remove_role",
        "en-GB": "remove_role",
        "es-ES": "remove_rol",
    }

    # ====================== ADD USUARIO ======================
    @app_commands.command(name="add_usuario", description="Adiciona usuário à lista de menções do canal atual")
    @app_commands.checks.has_permissions(administrator=True)
    async def add_usuario(self, interaction: discord.Interaction, usuario: discord.Member):
        await interaction.response.defer(ephemeral=True)
        loc = interaction.locale
        cfg, data = get_channel_config(interaction.guild_id, interaction.channel_id)
        if cfg is None:
            await interaction.followup.send(t(interaction.guild_id, "err_channel_not_registered", locale=loc), ephemeral=True)
            return
        user_pings = cfg.setdefault("user_pings", [])
        if usuario.id in user_pings:
            await interaction.followup.send(t(interaction.guild_id, "user_already_added", locale=loc, user=usuario.mention), ephemeral=True)
            return
        if len(user_pings) >= 25:
            await interaction.followup.send(t(interaction.guild_id, "user_limit", locale=loc), ephemeral=True)
            return
        user_pings.append(usuario.id)
        save_guilds(data)
        await interaction.followup.send(t(interaction.guild_id, "user_added", locale=loc, user=usuario.mention), ephemeral=True)

    add_usuario.description_localizations = {
        "en-US": "Add a user to the mention list for the current channel",
        "en-GB": "Add a user to the mention list for the current channel",
        "es-ES": "Agrega un usuario a la lista de menciones del canal actual",
    }
    add_usuario.name_localizations = {
        "en-US": "add_user",
        "en-GB": "add_user",
        "es-ES": "add_usuario",
    }

    # ====================== REMOVE USUARIO ======================
    @app_commands.command(name="remove_usuario", description="Remove usuário da lista de menções do canal atual")
    @app_commands.checks.has_permissions(administrator=True)
    async def remove_usuario(self, interaction: discord.Interaction, usuario: discord.Member):
        await interaction.response.defer(ephemeral=True)
        loc = interaction.locale
        cfg, data = get_channel_config(interaction.guild_id, interaction.channel_id)
        if cfg is None:
            await interaction.followup.send(t(interaction.guild_id, "err_channel_not_registered", locale=loc), ephemeral=True)
            return
        user_pings = cfg.get("user_pings", [])
        if usuario.id not in user_pings:
            await interaction.followup.send(t(interaction.guild_id, "user_not_in_list", locale=loc, user=usuario.mention), ephemeral=True)
            return
        user_pings.remove(usuario.id)
        save_guilds(data)
        await interaction.followup.send(t(interaction.guild_id, "user_removed", locale=loc, user=usuario.mention), ephemeral=True)

    remove_usuario.description_localizations = {
        "en-US": "Remove a user from the mention list for the current channel",
        "en-GB": "Remove a user from the mention list for the current channel",
        "es-ES": "Elimina un usuario de la lista de menciones del canal actual",
    }
    remove_usuario.name_localizations = {
        "en-US": "remove_user",
        "en-GB": "remove_user",
        "es-ES": "remove_usuario",
    }

    # ====================== CONFIG ======================
    @app_commands.command(name="config", description="Mostra as configurações do canal atual")
    async def config(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        loc = interaction.locale
        gid = str(interaction.guild_id)
        channel_id = str(interaction.channel_id)
        cfg, _ = get_channel_config(interaction.guild_id, interaction.channel_id)
        if cfg is None:
            await interaction.followup.send(t(interaction.guild_id, "err_channel_not_registered", locale=loc), ephemeral=True)
            return

        alert_types = cfg.get("alert_types", ["PICO", "PRACA", "MB", "WB", "EVENTO"])
        tz = cfg.get("timezone", "America/Sao_Paulo")
        role_ping = cfg.get("role_ping")
        user_pings = cfg.get("user_pings", [])

        word_conf = t(gid, "config_word_confirmations", locale=loc)
        word_min = t(gid, "config_word_minutes", locale=loc)

        meta_dict = cfg.get("meta", DEFAULT_META)
        meta_text = "\n".join([f"• **{tp}** → **{q}** {word_conf}" for tp, q in meta_dict.items()])

        minutes_dict = cfg.get("alert_minutes", DEFAULT_ALERT_MINUTES)
        minutes_text = "\n".join([f"• **{tp}** → {m} {word_min}" for tp, m in minutes_dict.items()])

        cargo_text = f"<@&{role_ping}>" if role_ping else t(gid, "config_not_configured", locale=loc)
        user_pings_text = " ".join(f"<@{uid}>" for uid in user_pings) if user_pings else t(gid, "config_none_configured", locale=loc)
        user_pings_footer = f" ({len(user_pings)}/25)" if user_pings else ""

        silenced_data = load_json(silenced_users_file(gid))
        channel_silenced = silenced_data.get(channel_id, [])
        if channel_silenced:
            silenced_text = t(gid, "config_silenced_count", locale=loc, count=len(channel_silenced)) + " ".join(f"<@{uid}>" for uid in channel_silenced)
        else:
            silenced_text = t(gid, "config_none_this_week", locale=loc)

        remaining_users = [uid for uid in user_pings if uid not in channel_silenced]
        if role_ping:
            notify_rule = t(gid, "config_notify_role", locale=loc)
        elif remaining_users:
            notify_rule = t(gid, "config_notify_users", locale=loc, count=len(remaining_users))
        else:
            notify_rule = t(gid, "config_notify_none", locale=loc)

        embed = discord.Embed(title=t(gid, "config_title", locale=loc), color=0x7F77DD)
        embed.add_field(name=t(gid, "config_field_channel", locale=loc), value=f"<#{interaction.channel_id}>", inline=False)
        embed.add_field(name=t(gid, "config_field_timezone", locale=loc), value=tz, inline=True)
        embed.add_field(name=t(gid, "config_field_types", locale=loc), value=", ".join(alert_types), inline=True)
        embed.add_field(name=t(gid, "config_field_minutes", locale=loc), value=minutes_text, inline=False)
        embed.add_field(name=t(gid, "config_field_meta", locale=loc), value=meta_text, inline=False)
        embed.add_field(name=t(gid, "config_field_role", locale=loc), value=cargo_text, inline=False)
        embed.add_field(name=t(gid, "config_field_users", locale=loc) + user_pings_footer, value=user_pings_text, inline=False)
        embed.add_field(name=t(gid, "config_field_silenced", locale=loc), value=silenced_text, inline=False)
        embed.add_field(name=t(gid, "config_field_notify", locale=loc), value=notify_rule, inline=False)
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
        channel_id = str(interaction.channel_id)
        tz_name = cfg.get("timezone", "America/Sao_Paulo")
        alert_types = cfg.get("alert_types", ["PICO", "PRACA", "MB", "WB", "EVENTO"])

        try:
            tz = ZoneInfo(tz_name)
        except:
            tz = ZoneInfo("America/Sao_Paulo")

        bosses_data = load_json(BOSSES_FILE)
        now = datetime.now(tz)

        upcoming = []
        for boss in bosses_data.get("bosses", []):
            if not boss.get("active", False):
                continue
            if boss["type"] not in alert_types:
                continue
            next_spawn = get_next_spawn(boss, tz)
            diff_min = (next_spawn - now).total_seconds() / 60
            upcoming.append((next_spawn, diff_min, boss))

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

        weekly_completed = load_json(completed_file(gid))

        for i, (spawn_time, diff_min, boss) in enumerate(top3, 1):
            emoji = EMOJIS.get(boss["type"], "🔴")
            h = int(diff_min // 60)
            m = int(diff_min % 60)
            time_str = t(gid, "upcoming_time_hours", locale=loc, h=h, m=m) if h > 0 else t(gid, "upcoming_time_mins", locale=loc, m=m)
            loc_str = " | ".join(filter(None, [boss.get("layer"), boss.get("word"), boss.get("map")]))
            weekly_key = f"{gid}_{channel_id}_{boss['id']}_{boss['start']}"
            silenced_tag = t(gid, "upcoming_meta_tag", locale=loc) if weekly_key in weekly_completed else ""
            embed.add_field(
                name=f"{i}. {emoji} [{boss['type']}] {get_boss_name(boss, gid, loc)}{silenced_tag}",
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
        bosses_data = load_json(BOSSES_FILE)
        tipo = tipo.upper()
        filtered = [b for b in bosses_data.get("bosses", []) if b.get("type") == tipo and b.get("active", False)]
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

    # ====================== SILENCIADOS ======================
    @app_commands.command(name="silenciados", description="Lista os bosses que já bateram a meta esta semana")
    async def silenciados(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        loc = interaction.locale
        cfg, _ = get_channel_config(interaction.guild_id, interaction.channel_id)
        if cfg is None:
            await interaction.followup.send(t(interaction.guild_id, "err_channel_not_registered", locale=loc), ephemeral=True)
            return

        gid = str(interaction.guild_id)
        channel_id = str(interaction.channel_id)
        weekly_completed = load_json(completed_file(gid))
        bosses_data = load_json(BOSSES_FILE)
        bosses_by_id = {b["id"]: b for b in bosses_data.get("bosses", [])}
        prefix = f"{gid}_{channel_id}_"
        silenced = [k for k in weekly_completed if k.startswith(prefix)]

        if not silenced:
            await interaction.followup.send(t(gid, "silenced_none", locale=loc), ephemeral=True)
            return

        fields = []
        for key in silenced:
            parts = key[len(prefix):].rsplit("_", 1)
            boss_id = parts[0]
            boss = bosses_by_id.get(boss_id)
            if boss:
                emoji = EMOJIS.get(boss["type"], "🔴")
                boss_loc = " | ".join(filter(None, [boss.get("layer"), boss.get("word"), boss.get("map")]))
                fields.append((
                    f"{emoji} [{boss['type']}] {get_boss_name(boss, gid, loc)}",
                    f"📍 {boss_loc}" if boss_loc else "📍 —"
                ))

        chunk_size = 25
        total_pages = (len(fields) + chunk_size - 1) // chunk_size
        embeds = []
        for page, i in enumerate(range(0, len(fields), chunk_size), 1):
            chunk = fields[i:i + chunk_size]
            title = t(gid, "silenced_title", locale=loc)
            if total_pages > 1:
                title += f" ({page}/{total_pages})"
            embed = discord.Embed(title=title, description=t(gid, "silenced_count", locale=loc, count=len(fields)), color=0x555555)
            for name, value in chunk:
                embed.add_field(name=name, value=value, inline=False)
            if page == total_pages:
                embed.set_footer(text=t(gid, "silenced_footer", locale=loc))
            embeds.append(embed)
        await interaction.followup.send(embeds=embeds[:10], ephemeral=True)

    silenciados.description_localizations = {
        "en-US": "List bosses that have already reached the goal this week",
        "en-GB": "List bosses that have already reached the goal this week",
        "es-ES": "Lista los bosses que ya alcanzaron la meta esta semana",
    }
    silenciados.name_localizations = {
        "en-US": "silenced",
        "en-GB": "silenced",
        "es-ES": "silenciados",
    }

    # ====================== SETUP LIMPEZA ======================
    @app_commands.command(name="setup_limpeza", description="Ativa limpeza diária automática deste canal no horário definido")
    @app_commands.describe(hora="Hora do cleanup (0-23)", minuto="Minuto do cleanup (0-59)")
    @app_commands.checks.has_permissions(administrator=True)
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
        except:
            tz = ZoneInfo("America/Sao_Paulo")

        bosses_data = load_json(BOSSES_FILE)
        boss = next((b for b in bosses_data.get("bosses", []) if b["id"] == boss_id.lower()), None)
        if boss is None:
            await interaction.followup.send(t(gid, "spawn_not_found", locale=loc, boss_id=boss_id), ephemeral=True)
            return

        now = datetime.now(tz)
        next_spawn = get_next_spawn(boss, tz)
        diff_min = (next_spawn - now).total_seconds() / 60
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
        embed.add_field(name=t(gid, "spawn_next_label", locale=loc), value=t(gid, "spawn_next_value", locale=loc, time=next_spawn.strftime("%H:%M"), time_str=time_str), inline=False)
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
