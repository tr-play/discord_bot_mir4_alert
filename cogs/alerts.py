import asyncio
import glob
import json
import os
from datetime import datetime, timedelta, time as dt_time, timezone
from zoneinfo import ZoneInfo

import discord
from discord.ext import commands, tasks

import api_client
from spawn_calc import upcoming_all
from cogs.account import BossAlertView

GUILDS_FILE = "data/guilds.json"
PENDING_DM_DELETES_FILE = "data/pending_dm_deletes.json"
DM_TTL_MINUTES = 60  # how long a boss-alert DM stays before auto-deleting

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


def load_json(file_path):
    if not os.path.exists(file_path):
        return {}
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_json(file_path, data):
    dir_name = os.path.dirname(file_path)
    if dir_name:
        os.makedirs(dir_name, exist_ok=True)
    with open(file_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def scheduled_alarms_file(guild_id):
    return f"data/scheduled_alarms/{guild_id}.json"


class Alerts(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

        # Freeform /alarme reminders -- unrelated to boss alerts, untouched by the
        # personal-DM rework below.
        self.scheduled_alarms = {}
        for path in glob.glob("data/scheduled_alarms/*.json"):
            gid = os.path.basename(path).removesuffix(".json")
            self.scheduled_alarms[gid] = load_json(path)

        # Boss-alert DMs pending auto-delete -- persisted so a bot restart mid-TTL
        # doesn't leave them stuck forever (same reaper shape as scheduled_alarms).
        self.pending_dm_deletes = load_json(PENDING_DM_DELETES_FILE).get("items", [])

        self.check_bosses.start()
        self.check_cleanup.start()
        self.check_scheduled_alarms.start()
        self.check_dm_expiry.start()

    def cog_unload(self):
        self.check_bosses.cancel()
        self.check_cleanup.cancel()
        self.check_scheduled_alarms.cancel()
        self.check_dm_expiry.cancel()

    # ====================== ALERTAS PESSOAIS POR DM ======================
    # Mirrors src/app/api/cron/send-push/route.ts's filtering (personal lead time, personal
    # enabled types, personal completion state, dedupe log) but delivers by Discord DM
    # instead of Web Push, and reaches boss/user data through api_client.py instead of a
    # database -- the bot has no direct Postgres access at all.
    @tasks.loop(minutes=1)
    async def check_bosses(self):
        schedule = await api_client.get_boss_schedule()
        if not schedule:
            return
        now = datetime.now(timezone.utc)
        upcoming = upcoming_all(schedule, now)

        try:
            subscribers = await api_client.get_subscribers()
        except Exception as e:
            print(f"[check_bosses] Falha ao buscar assinantes: {e}")
            return

        for sub in subscribers:
            try:
                await self._process_subscriber(sub, upcoming, now)
            except Exception as e:
                print(f"[check_bosses] Erro processando {sub.get('discord_id')}: {e}")

    @check_bosses.before_loop
    async def before_check_bosses(self):
        await self.bot.wait_until_ready()

    async def _process_subscriber(self, sub, upcoming, now):
        to_notify = []
        completed = set(sub.get("completed_boss_ids") or [])
        types_enabled = sub.get("notify_types_enabled") or {}

        for boss, spawn_time in upcoming:
            minutes_left = (spawn_time - now).total_seconds() / 60
            if not (0 < minutes_left <= sub["notify_lead_minutes"]):
                continue
            if not types_enabled.get(boss["type"], False):
                continue
            if boss["id"] in completed:
                continue

            claimed = await api_client.claim_notification(sub["discord_id"], boss["id"], spawn_time.isoformat())
            if claimed:
                to_notify.append((boss, spawn_time, minutes_left))

        if to_notify:
            await self._send_dm(sub, to_notify)
            await asyncio.sleep(1)  # pacing between DMs, mirrors the old per-channel sleep(2)

    async def _send_dm(self, sub, to_notify):
        try:
            discord_id = int(sub["discord_id"])
        except (TypeError, ValueError):
            return

        try:
            user = await self.bot.fetch_user(discord_id)
        except discord.NotFound:
            # Deleted Discord account -- will never resolve again, unlink proactively.
            await api_client.unlink(sub["discord_id"])
            print(f"[check_bosses] Discord {discord_id} não existe mais, desvinculado.")
            return
        except discord.HTTPException as e:
            print(f"[check_bosses] Erro buscando usuário {discord_id}: {e}")
            return

        tz_name = sub.get("timezone") or "America/Sao_Paulo"
        try:
            tz = ZoneInfo(tz_name)
        except Exception:
            tz = ZoneInfo("America/Sao_Paulo")

        types_present = sorted({boss["type"] for boss, _, _ in to_notify})
        mb_bosses = [(boss["id"], boss["name"]) for boss, _, _ in to_notify if boss["type"] == "MB"]
        view = BossAlertView(types_present, mb_bosses)

        if len(to_notify) == 1:
            boss, spawn_time, minutes_left = to_notify[0]
            loc = " | ".join(filter(None, [boss.get("layer"), boss.get("word"), boss.get("map")]))
            embed = discord.Embed(
                title=f"{EMOJIS.get(boss['type'], '🔴')} [{boss['type']}] {boss['name']}",
                color=COLORS.get(boss["type"], 0xFF0000),
                description=f"🗺️ {loc}" if loc else None,
            )
            embed.add_field(
                name="Spawn",
                value=f"em {int(minutes_left)} min ({spawn_time.astimezone(tz).strftime('%H:%M')} {tz_name})",
                inline=True,
            )
            embed.set_footer(text=f"Boss ID: {boss['id']}")
        else:
            lines = [
                f"{EMOJIS.get(b['type'], '🔴')} [{b['type']}] {b['name']} — em {int(m)} min "
                f"({s.astimezone(tz).strftime('%H:%M')} {tz_name})"
                for b, s, m in to_notify
            ]
            embed = discord.Embed(
                title=f"{len(to_notify)} bosses vão spawnar em breve!",
                color=0x7F77DD,
                description="\n".join(lines),
            )

        try:
            msg = await user.send(embed=embed, view=view)
            self._schedule_dm_delete(sub["discord_id"], msg.id)
        except discord.Forbidden:
            # DMs closed/bot blocked -- skip only this send. The dedupe row for this spawn is
            # already committed (claim_notification ran before we got here), so this exact
            # spawn won't retry, but the boss's next occurrence gets a fresh attempt -- self
            # heals once the user fixes their privacy settings.
            print(f"[check_bosses] DM bloqueada por {discord_id}, pulando.")
        except discord.HTTPException as e:
            print(f"[check_bosses] Erro enviando DM pra {discord_id}: {e}")

    def _schedule_dm_delete(self, discord_id, message_id):
        delete_at = (datetime.now(timezone.utc) + timedelta(minutes=DM_TTL_MINUTES)).isoformat()
        self.pending_dm_deletes.append({
            "discord_id": str(discord_id),
            "message_id": message_id,
            "delete_at": delete_at,
        })
        save_json(PENDING_DM_DELETES_FILE, {"items": self.pending_dm_deletes})

    @tasks.loop(minutes=1)
    async def check_dm_expiry(self):
        if not self.pending_dm_deletes:
            return
        now = datetime.now(timezone.utc)
        remaining = []
        changed = False
        for item in self.pending_dm_deletes:
            try:
                fire_at = datetime.fromisoformat(item["delete_at"])
            except (KeyError, ValueError):
                changed = True
                continue
            if now < fire_at:
                remaining.append(item)
                continue
            changed = True
            await self._delete_dm(item)

        if changed:
            self.pending_dm_deletes = remaining
            save_json(PENDING_DM_DELETES_FILE, {"items": self.pending_dm_deletes})

    @check_dm_expiry.before_loop
    async def before_dm_expiry(self):
        await self.bot.wait_until_ready()

    async def _delete_dm(self, item):
        try:
            discord_id = int(item["discord_id"])
        except (TypeError, ValueError, KeyError):
            return
        try:
            user = await self.bot.fetch_user(discord_id)
            dm_channel = await user.create_dm()
            await dm_channel.get_partial_message(item["message_id"]).delete()
        except discord.NotFound:
            pass  # already gone (user deleted it, or DM closed) -- nothing to do
        except discord.Forbidden:
            pass  # can't delete our own DM -- shouldn't normally happen, not worth retrying
        except discord.HTTPException as e:
            print(f"[check_dm_expiry] Erro apagando DM {item.get('message_id')}: {e}")

    # ====================== LIMPEZA DIÁRIA ======================
    # Unrelated to boss alerts -- untouched by this rework.
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
    # Unrelated to boss alerts -- untouched by this rework.
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

                embed = discord.Embed(title="⏰ Alarme!", description=alarm.get("mensagem", ""), color=0xFF8C00)
                embed.set_footer(text=f"Criado por {alarm.get('created_by', '?')}")

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
