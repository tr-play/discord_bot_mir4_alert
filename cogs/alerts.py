import discord
from discord.ext import commands, tasks
import json
import os
import glob
import asyncio
import uuid
from datetime import datetime, timedelta, time as dt_time, timezone
from zoneinfo import ZoneInfo
from i18n import t, get_boss_name

BOSSES_FILE = "data/bosses.json"
GUILDS_FILE = "data/guilds.json"

def alerts_file(guild_id):
    return f"data/alerts/{guild_id}.json"

def completed_file(guild_id):
    return f"data/completed/{guild_id}.json"

COLORS = {
    "PICO": 0xD85A30,
    "PRACA": 0x00BFFF,
    "MB": 0xBA7517,
    "WB": 0xFF0000,
    "EVENTO": 0xFFD700,
}

EMOJIS = {
    "PICO": "🔶",
    "PRACA": "🟦",
    "MB": "🔴",
    "WB": "🌍",
    "EVENTO": "🎉",
}

REACTION_EMOJI = "✅"

DEFAULT_ALERT_MINUTES = {
    "PICO": 10, "PRACA": 10, "MB": 15, "WB": 20, "EVENTO": 30
}

DEFAULT_META = {
    "PICO": 5, "PRACA": 8, "MB": 12, "WB": 20, "EVENTO": 15
}

def load_json(file_path):
    if not os.path.exists(file_path):
        return {}
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except:
        return {}

def save_json(file_path, data):
    dir_name = os.path.dirname(file_path)
    if dir_name:
        os.makedirs(dir_name, exist_ok=True)
    with open(file_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

def load_all_alerts():
    result = {}
    # Migração: active_alerts_{guild_id}.json na raiz → data/alerts/{guild_id}.json
    for old_path in glob.glob("active_alerts_*.json"):
        gid = old_path.removeprefix("active_alerts_").removesuffix(".json")
        new_path = alerts_file(gid)
        if not os.path.exists(new_path):
            os.makedirs(os.path.dirname(new_path), exist_ok=True)
            os.rename(old_path, new_path)
            print(f"📦 Migrado {old_path} → {new_path}")
    for path in glob.glob("data/alerts/*.json"):
        result.update(load_json(path))
    return result

def load_all_completed():
    result = {}
    # Migração: weekly_completed_{guild_id}.json na raiz → data/completed/{guild_id}.json
    for old_path in glob.glob("weekly_completed_*.json"):
        gid = old_path.removeprefix("weekly_completed_").removesuffix(".json")
        new_path = completed_file(gid)
        if not os.path.exists(new_path):
            os.makedirs(os.path.dirname(new_path), exist_ok=True)
            os.rename(old_path, new_path)
            print(f"📦 Migrado {old_path} → {new_path}")
    for path in glob.glob("data/completed/*.json"):
        result.update(load_json(path))
    return result

def save_guild_alerts(active_alerts, guild_id):
    guild_alerts = {k: v for k, v in active_alerts.items() if isinstance(v, dict) and v.get("guild_id") == guild_id}
    save_json(alerts_file(guild_id), guild_alerts)

def save_guild_completed(weekly_completed, guild_id):
    prefix = f"{guild_id}_"
    guild_completed = {k: v for k, v in weekly_completed.items() if k.startswith(prefix)}
    save_json(completed_file(guild_id), guild_completed)

def silenced_users_file(guild_id):
    return f"data/silenced_users/{guild_id}.json"

def scheduled_alarms_file(guild_id):
    return f"data/scheduled_alarms/{guild_id}.json"

def load_all_silenced_users():
    result = {}
    for path in glob.glob("data/silenced_users/*.json"):
        gid = os.path.basename(path).removesuffix(".json")
        result[gid] = load_json(path)
    return result

def save_guild_silenced_users(silenced_users, guild_id):
    save_json(silenced_users_file(guild_id), silenced_users.get(guild_id, {}))

class Alerts(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.active_alerts = load_all_alerts()
        self.weekly_completed = load_all_completed()
        self.silenced_users = load_all_silenced_users()

        # Carregar alarmes agendados de todos os guilds
        self.scheduled_alarms = {}
        for path in glob.glob("data/scheduled_alarms/*.json"):
            gid = os.path.basename(path).removesuffix(".json")
            self.scheduled_alarms[gid] = load_json(path)

        self.check_bosses.start()
        self.reset_weekly.start()
        self.check_cleanup.start()
        self.check_scheduled_alarms.start()

    def cog_unload(self):
        self.check_bosses.cancel()
        self.reset_weekly.cancel()
        self.check_cleanup.cancel()
        self.check_scheduled_alarms.cancel()

    # ====================== RESET SEMANAL ======================
    @tasks.loop(time=dt_time(hour=12, minute=0, tzinfo=ZoneInfo("America/Sao_Paulo")))
    async def reset_weekly(self):
        now = datetime.now(ZoneInfo("America/Sao_Paulo"))
        if now.weekday() == 6:  # Domingo às 12h BRT
            guild_ids = set(k.split("_")[0] for k in self.weekly_completed)
            self.weekly_completed = {}
            for gid in guild_ids:
                save_json(completed_file(gid), {})

            silenced_guild_ids = set(self.silenced_users.keys())
            self.silenced_users = {}
            for gid in silenced_guild_ids:
                save_json(silenced_users_file(gid), {})

            print("🔄 Ciclo semanal resetado (Domingo 12:00 BRT)")

    @reset_weekly.before_loop
    async def before_reset(self):
        await self.bot.wait_until_ready()

    # ====================== VERIFICAÇÃO PRINCIPAL ======================
    @tasks.loop(minutes=1)
    async def check_bosses(self):
        bosses_data = load_json(BOSSES_FILE)
        guilds_data = load_json(GUILDS_FILE)

        if not bosses_data.get("bosses"):
            return

        for guild_id, guild_data in guilds_data.get("guilds", {}).items():
            for channel_id, cfg in guild_data.get("channels", {}).items():
                channel = self.bot.get_channel(int(channel_id))
                if not channel:
                    continue

                tz_name = cfg.get("timezone", "America/Sao_Paulo")
                alert_types = cfg.get("alert_types", ["PICO", "PRACA", "MB", "WB", "EVENTO"])
                alert_minutes_dict = cfg.get("alert_minutes", DEFAULT_ALERT_MINUTES)
                meta_dict = cfg.get("meta", DEFAULT_META)
                role_ping = cfg.get("role_ping")
                user_pings = cfg.get("user_pings", [])

                try:
                    tz = ZoneInfo(tz_name)
                except:
                    tz = ZoneInfo("America/Sao_Paulo")

                now = datetime.now(tz)

                for boss in bosses_data["bosses"]:
                    if not boss.get("active", False):
                        continue
                    if boss["type"] not in alert_types:
                        continue

                    weekly_key = f"{guild_id}_{channel_id}_{boss['id']}_{boss['start']}"
                    if weekly_key in self.weekly_completed:
                        continue

                    alert_minutes = alert_minutes_dict.get(boss["type"], 10)
                    spawns = self.get_next_spawns(boss, tz, alert_minutes + 5)

                    for spawn_time in spawns:
                        diff = (spawn_time - now).total_seconds() / 60
                        if not (0.5 < diff <= alert_minutes):
                            continue

                        spawn_str = spawn_time.strftime("%Y-%m-%d %H:%M")
                        if self.is_alert_already_sent(guild_id, channel_id, boss["id"], spawn_str):
                            continue

                        # Envia se: tem cargo OU tem usuário não silenciado para marcar
                        guild_silenced = self.silenced_users.get(guild_id, {})
                        spawn_key = f"{guild_id}_{channel_id}_{boss['id']}_{boss['start']}"
                        channel_silenced = set(guild_silenced.get(spawn_key, []))
                        has_users_to_ping = any(uid not in channel_silenced for uid in user_pings)
                        if not role_ping and not has_users_to_ping:
                            continue

                        await self.send_boss_alert(channel, boss, spawn_time, tz_name, role_ping, user_pings, meta_dict, guild_id, channel_id)
                        await asyncio.sleep(2)

    def is_alert_already_sent(self, guild_id, channel_id, boss_id, spawn_str):
        for alert in self.active_alerts.values():
            if not isinstance(alert, dict):
                continue
            saved = alert.get("spawn_time", "")[:16].replace("T", " ")
            if (alert.get("guild_id") == guild_id and
                alert.get("channel_id") == channel_id and
                alert.get("boss_id") == boss_id and
                saved == spawn_str):
                return True
        return False

    def get_next_spawns(self, boss, tz, minutes_ahead):
        # boss["start"] is always stored in São Paulo time
        SP = ZoneInfo("America/Sao_Paulo")
        now_sp = datetime.now(SP)
        h, m = map(int, boss["start"].split(":"))
        today_start = now_sp.replace(hour=h, minute=m, second=0, microsecond=0)

        spawns = []
        t = today_start
        while t < now_sp:
            t += timedelta(minutes=boss["respawn"])
        while t <= now_sp + timedelta(minutes=minutes_ahead):
            spawns.append(t.astimezone(tz))
            t += timedelta(minutes=boss["respawn"])
        return spawns

    async def send_boss_alert(self, channel, boss, spawn_time, tz_name, role_ping, user_pings, meta_dict, guild_id, channel_id):
        try:
            alert_minutes = (spawn_time - datetime.now(ZoneInfo(tz_name))).total_seconds() / 60

            embed = discord.Embed(
                title=f"{EMOJIS.get(boss['type'], '🔴')} [{boss['type']}] {get_boss_name(boss, guild_id)}",
                color=COLORS.get(boss['type'], 0xFF0000),
                description=t(guild_id, "boss_alert_map", layer=boss['layer'], word=boss['word'], map=boss['map'])
            )
            embed.add_field(
                name=t(guild_id, "boss_alert_spawn_label"),
                value=t(guild_id, "boss_alert_spawn_value", minutes=int(alert_minutes), time=spawn_time.strftime("%H:%M")),
                inline=True
            )
            embed.set_footer(text=f"{t(guild_id, 'boss_alert_footer', tz=tz_name)} | Boss ID: {boss['id']}")

            parts = []
            if role_ping:
                parts.append(f"<@&{role_ping}>")
            guild_silenced = self.silenced_users.get(guild_id, {})
            spawn_key = f"{guild_id}_{channel_id}_{boss['id']}_{boss['start']}"
            channel_silenced = set(guild_silenced.get(spawn_key, []))
            for uid in user_pings:
                if uid not in channel_silenced:
                    parts.append(f"<@{uid}>")
            mention = " ".join(parts)

            message = await channel.send(content=mention or None, embed=embed)
            await message.add_reaction(REACTION_EMOJI)

            self.active_alerts[str(message.id)] = {
                "guild_id": guild_id,
                "channel_id": channel_id,
                "boss_id": boss["id"],
                "boss_start": boss["start"],
                "spawn_time": spawn_time.isoformat(),
                "type": boss["type"],
                "confirmed": [],
                "meta": meta_dict.get(boss["type"], 10)
            }
            save_guild_alerts(self.active_alerts, guild_id)

            print(f"✅ Alerta enviado → {boss['name']} ({boss['type']}) às {spawn_time.strftime('%H:%M')} | canal {channel_id}")

        except discord.DiscordServerError as e:
            print(f"⚠️ Discord 503 ao enviar {boss['name']}: {e}")
            await asyncio.sleep(3)
        except Exception as e:
            print(f"❌ Erro ao enviar alerta de {boss['name']}: {e}")

    # ====================== REAÇÕES ======================
    @commands.Cog.listener()
    async def on_reaction_add(self, reaction, user):
        print(f"[REACT] ➕ {user.name} reagiu com {reaction.emoji} na msg {reaction.message.id}")

        if user.bot or str(reaction.emoji) != REACTION_EMOJI:
            print(f"[REACT] ⏭️ Ignorado (bot={user.bot}, emoji={reaction.emoji})")
            return

        message_id = str(reaction.message.id)
        if message_id not in self.active_alerts:
            print(f"[REACT] ⚠️ Msg {message_id} não encontrada em active_alerts")
            return

        alert = self.active_alerts[message_id]
        if user.id in alert["confirmed"]:
            print(f"[REACT] ⏭️ {user.name} já confirmou antes")
            return

        alert["confirmed"].append(user.id)
        save_guild_alerts(self.active_alerts, alert["guild_id"])

        # Lê a meta atual do config do canal (não a salva no alerta, pois pode ter sido alterada)
        guilds_data = load_json(GUILDS_FILE)
        cfg = guilds_data.get("guilds", {}).get(alert["guild_id"], {}).get("channels", {}).get(alert["channel_id"], {})

        # Opt-out: se o usuário está na lista de user_pings, silencia para este boss específico
        user_pings = cfg.get("user_pings", [])
        if user.id in user_pings:
            gid = alert["guild_id"]
            cid = alert["channel_id"]
            spawn_key = f"{gid}_{cid}_{alert['boss_id']}_{alert['boss_start']}"
            self.silenced_users.setdefault(gid, {}).setdefault(spawn_key, [])
            if user.id not in self.silenced_users[gid][spawn_key]:
                self.silenced_users[gid][spawn_key].append(user.id)
                save_guild_silenced_users(self.silenced_users, gid)
                bosses_data = load_json(BOSSES_FILE)
                boss = next((b for b in bosses_data.get("bosses", []) if b["id"] == alert["boss_id"]), None)
                boss_name = f"{EMOJIS.get(alert['type'], '🔴')} [{alert['type']}] {get_boss_name(boss, gid)}" if boss else alert["boss_id"]
                print(f"[REACT] 🔕 {user.name} silenciado para menções neste alerta ({boss_name})")
                await reaction.message.channel.send(
                    t(gid, "boss_silenced_msg", user=user.mention, boss=boss_name),
                    delete_after=15
                )

        meta_dict = cfg.get("meta", {"PICO": 5, "PRACA": 5, "MB": 5, "WB": 5, "EVENTO": 5})
        current_meta = meta_dict.get(alert["type"], alert.get("meta", 5))

        print(f"[REACT] ✅ {user.name} confirmou | boss={alert['boss_id']} | {len(alert['confirmed'])}/{current_meta}")

        if len(alert["confirmed"]) >= current_meta:
            print(f"[REACT] 🎯 META ATINGIDA para boss={alert['boss_id']}!")
            await self.meta_reached(reaction.message, alert)

    @commands.Cog.listener()
    async def on_reaction_remove(self, reaction, user):
        print(f"[REACT] ➖ {user.name} removeu reação {reaction.emoji} da msg {reaction.message.id}")

        if user.bot or str(reaction.emoji) != REACTION_EMOJI:
            print(f"[REACT] ⏭️ Ignorado (bot={user.bot}, emoji={reaction.emoji})")
            return

        message_id = str(reaction.message.id)
        if message_id not in self.active_alerts:
            print(f"[REACT] ⚠️ Msg {message_id} não encontrada em active_alerts")
            return

        alert = self.active_alerts[message_id]
        if user.id in alert["confirmed"]:
            alert["confirmed"].remove(user.id)
            save_guild_alerts(self.active_alerts, alert["guild_id"])
            print(f"[REACT] ↩️ {user.name} removeu confirmação | boss={alert['boss_id']} | {len(alert['confirmed'])}/{alert['meta']}")

    async def meta_reached(self, message, alert):
        bosses_data = load_json(BOSSES_FILE)
        boss = next((b for b in bosses_data.get("bosses", []) if b["id"] == alert["boss_id"]), None)
        gid = alert["guild_id"]
        boss_name = f"{EMOJIS.get(alert['type'], '🔴')} [{alert['type']}] {get_boss_name(boss, gid)}" if boss else alert["boss_id"]

        embed = discord.Embed(
            title=t(gid, "boss_meta_title"),
            description=t(gid, "boss_meta_desc", boss=boss_name, count=len(alert["confirmed"])),
            color=0x00FF00
        )
        await message.channel.send(embed=embed)

        guild_id = alert["guild_id"]
        spawn_key = f"{guild_id}_{alert['channel_id']}_{alert['boss_id']}_{alert['boss_start']}"
        self.weekly_completed[spawn_key] = True
        save_guild_completed(self.weekly_completed, guild_id)

        self.active_alerts.pop(str(message.id), None)
        save_guild_alerts(self.active_alerts, guild_id)

        print(f"🎯 Meta atingida e boss silenciado: {alert['boss_id']} | canal {alert['channel_id']}")

    # ====================== LIMPEZA DIÁRIA ======================
    @tasks.loop(minutes=1)
    async def check_cleanup(self):
        data = load_json(GUILDS_FILE)
        channels_to_clean = []

        for guild_id, guild_data in data.get("guilds", {}).items():
            for channel_id, cfg in guild_data.get("channels", {}).items():
                cfg.setdefault("cleanup_enabled", False)
                cfg.setdefault("cleanup_time", None)
                cfg.setdefault("cleanup_last_ran", None)

                if not cfg["cleanup_enabled"] or not cfg["cleanup_time"]:
                    continue

                tz = ZoneInfo(cfg.get("timezone", "America/Sao_Paulo"))
                local_now = datetime.now(tz)
                local_date = local_now.strftime("%Y-%m-%d")
                local_time = local_now.strftime("%H:%M")

                if local_time != cfg["cleanup_time"]:
                    continue
                if cfg["cleanup_last_ran"] == local_date:
                    continue  # já rodou hoje

                cfg["cleanup_last_ran"] = local_date
                channels_to_clean.append(int(channel_id))

        if channels_to_clean:
            save_json(GUILDS_FILE, data)

        for channel_id in channels_to_clean:
            channel = self.bot.get_channel(channel_id)
            if channel is None:
                continue
            try:
                deleted = await channel.purge(limit=None, check=lambda m: not m.pinned, bulk=True)
                print(f"🧹 Cleanup: {len(deleted)} mensagens apagadas em #{channel.name} ({channel.guild.id})")
            except discord.Forbidden:
                print(f"⚠️ Sem permissão para limpar #{channel.name} ({channel.guild.id})")
            except discord.HTTPException as e:
                print(f"⚠️ Erro ao limpar #{channel.name}: {e}")

    @check_cleanup.before_loop
    async def before_cleanup(self):
        await self.bot.wait_until_ready()

    # ====================== ALARMES AGENDADOS ======================
    @tasks.loop(minutes=1)
    async def check_scheduled_alarms(self):
        now = datetime.now(timezone.utc)
        changed_guilds = set()

        for guild_id, alarms in list(self.scheduled_alarms.items()):
            to_remove = []
            for alarm_id, alarm in list(alarms.items()):
                try:
                    fire_at = datetime.fromisoformat(alarm["fire_at"])
                except (KeyError, ValueError):
                    to_remove.append(alarm_id)
                    continue

                if now < fire_at:
                    continue

                channel = self.bot.get_channel(int(alarm["channel_id"]))
                if channel is None:
                    to_remove.append(alarm_id)
                    changed_guilds.add(guild_id)
                    continue

                embed = discord.Embed(title=t(guild_id, "alarm_dispatch_title"), description=alarm.get("mensagem", ""), color=0xFF8C00)
                embed.set_footer(text=t(guild_id, "alarm_dispatch_footer", user=alarm.get("created_by", "?")))

                try:
                    await channel.send(content=alarm["mention_str"], embed=embed)
                except (discord.Forbidden, discord.HTTPException) as e:
                    print(f"❌ Erro ao disparar alarme {alarm_id}: {e}")

                to_remove.append(alarm_id)
                changed_guilds.add(guild_id)

            for alarm_id in to_remove:
                alarms.pop(alarm_id, None)

        for guild_id in changed_guilds:
            alarms_to_save = self.scheduled_alarms.get(guild_id, {})
            path = scheduled_alarms_file(guild_id)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                json.dump(alarms_to_save, f, indent=2, ensure_ascii=False)

    @check_scheduled_alarms.before_loop
    async def before_scheduled_alarms(self):
        await self.bot.wait_until_ready()

async def setup(bot):
    await bot.add_cog(Alerts(bot))
