import asyncio
import json
import os
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import discord
from discord.ext import commands, tasks

import api_client
from spawn_calc import upcoming_all
from cogs.account import BossAlertView
from i18n import get_boss_name
from guild_sync import sync_guild

PENDING_DM_DELETES_FILE = "data/pending_dm_deletes.json"
DM_TTL_MINUTES = 60  # how long a boss-alert DM stays before auto-deleting

# Mapa Boss (MB) is excluded from channel alerts on purpose -- it respawns too often for a
# channel-wide mention and would spam the configured role/@everyone every few minutes.
# It's unaffected here: personal DMs below still cover every type including MB.
CHANNEL_ALERT_TYPES = {"PICO", "PRACA", "WB", "EVENTO"}

COLORS = {
    "PICO": 0xD85A30,
    "PRACA": 0x00BFFF,
    "MB": 0xBA7517,
    "WB": 0xFF0000,
    "EVENTO": 0xFFD700,
}

# Generic site notifications (/api/discord/bot/notifications/claim) -- by kind
SITE_NOTIF_EMOJIS = {"test": "🔔", "watch": "⭐", "rank": "📈", "badge": "🏅", "reminder": "⏰"}
SITE_NOTIF_COLOR = 0x60A5FA
MAX_EMBEDS_PER_MESSAGE = 10  # Discord's hard limit

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


def alert_marks(lead_minutes: int, repeat_minutes: int | None) -> list[int]:
    """[lead_minutes] alone for a single alert, or [lead_minutes, lead_minutes - repeat,
    lead_minutes - 2*repeat, ...] (stopping before 0) when repetition is on."""
    marks = [lead_minutes]
    if repeat_minutes:
        m = lead_minutes - repeat_minutes
        while m > 0:
            marks.append(m)
            m -= repeat_minutes
    return marks


def mention_text(cfg: dict) -> str | None:
    mention_type = cfg.get("mentionType")
    target = cfg.get("mentionTargetId")
    if mention_type == "everyone":
        return "@everyone"
    if mention_type == "role" and target:
        return f"<@&{target}>"
    if mention_type == "user" and target:
        return f"<@{target}>"
    return None


class Alerts(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

        # Boss-alert DMs pending auto-delete -- persisted so a bot restart mid-TTL
        # doesn't leave them stuck forever (same reaper shape as before).
        self.pending_dm_deletes = load_json(PENDING_DM_DELETES_FILE).get("items", [])

        self.check_bosses.start()
        self.check_cleanup.start()
        self.check_scheduled_alarms.start()
        self.check_dm_expiry.start()
        self.check_site_notifications.start()
        self.check_guild_sync.start()

    def cog_unload(self):
        self.check_bosses.cancel()
        self.check_cleanup.cancel()
        self.check_scheduled_alarms.cancel()
        self.check_dm_expiry.cancel()
        self.check_site_notifications.cancel()
        self.check_guild_sync.cancel()

    # ====================== ALERTAS PESSOAIS POR DM + ALERTAS DE CANAL ======================
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
            subscribers = []

        for sub in subscribers:
            try:
                await self._process_subscriber(sub, upcoming, now)
            except Exception as e:
                print(f"[check_bosses] Erro processando {sub.get('discord_id')}: {e}")

        try:
            await self._process_channel_alerts(upcoming, now)
        except Exception as e:
            print(f"[check_bosses] Erro processando alertas de canal: {e}")

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

    # ====================== ALERTAS AUTOMÁTICOS POR CANAL ======================
    # New feature: unlike personal DMs (lead-time window + exact dedupe per spawn), a
    # channel alert can legitimately fire more than once for the same spawn (the repeat
    # feature) -- so the dedupe key includes *which* threshold ("mark") was hit, not just
    # the spawn itself. claim_channel_alert() atomically claims one (channel, boss_type,
    # spawn_time, mark) so the same mark never fires twice even though the check below is
    # a window (minutes_left <= mark), not exact-minute equality.
    async def _process_channel_alerts(self, upcoming, now):
        try:
            channel_alerts = await api_client.get_channel_alerts()
        except Exception as e:
            print(f"[check_bosses] Falha ao buscar alertas de canal: {e}")
            return
        if not channel_alerts:
            return

        by_channel: dict[tuple[str, str], list[dict]] = {}
        for cfg in channel_alerts:
            by_channel.setdefault((cfg["guildId"], cfg["channelId"]), []).append(cfg)

        for (guild_id, channel_id), configs in by_channel.items():
            try:
                await self._process_channel(guild_id, channel_id, configs, upcoming, now)
            except Exception as e:
                print(f"[check_bosses] Erro no canal {channel_id} ({guild_id}): {e}")

    async def _process_channel(self, guild_id, channel_id, configs, upcoming, now):
        channel = self.bot.get_channel(int(channel_id))
        if channel is None:
            await api_client.report_channel_health(guild_id, channel_id, success=False)
            return

        to_send = []  # (cfg, tied_bosses, spawn_time, minutes_left)
        for cfg in configs:
            boss_type = cfg["bossType"]
            same_type = [(b, s) for b, s in upcoming if b["type"] == boss_type]
            if not same_type:
                continue
            # `upcoming` is sorted ascending by spawn_time -- the first match is also the
            # earliest, and every boss of this type sharing that exact timestamp is the
            # "tied group" the site's own "Próximo Spawn" card already shows together.
            spawn_time = same_type[0][1]
            tied = [b for b, s in same_type if s == spawn_time]
            minutes_left = (spawn_time - now).total_seconds() / 60
            if minutes_left <= 0:
                continue

            marks = alert_marks(cfg["leadMinutes"], cfg.get("repeatMinutes"))
            due_marks = [m for m in marks if minutes_left <= m]
            if not due_marks:
                continue

            # Only the most urgent due mark actually sends; any smaller overdue mark (the
            # bot was down and missed a repeat tick) is claimed silently too, so it can't
            # fire a burst of catch-up messages on a later tick.
            fire_mark = max(due_marks)
            fired = await api_client.claim_channel_alert(guild_id, channel_id, boss_type, spawn_time.isoformat(), fire_mark)
            for m in due_marks:
                if m != fire_mark:
                    await api_client.claim_channel_alert(guild_id, channel_id, boss_type, spawn_time.isoformat(), m)
            if not fired:
                continue  # this mark was already notified on an earlier tick

            to_send.append((cfg, tied, spawn_time, minutes_left))

        if to_send:
            await self._send_channel_alert(channel, guild_id, channel_id, to_send)

    async def _send_channel_alert(self, channel, guild_id, channel_id, to_send):
        tz_name = to_send[0][0].get("timezone") or "America/Sao_Paulo"
        try:
            tz = ZoneInfo(tz_name)
        except Exception:
            tz = ZoneInfo("America/Sao_Paulo")

        # One mention per distinct (type, target) across the whole batch -- two boss types
        # both set to @everyone must not ping the channel twice in the same message.
        mentions = []
        seen = set()
        for cfg, *_ in to_send:
            key = (cfg.get("mentionType"), cfg.get("mentionTargetId"))
            text = mention_text(cfg)
            if text and key not in seen:
                seen.add(key)
                mentions.append(text)

        embeds = []
        for cfg, tied, spawn_time, minutes_left in sorted(to_send, key=lambda x: x[3]):
            boss_type = cfg["bossType"]
            lines = []
            for boss in tied[:20]:  # guard against an unreasonably large tied group blowing past embed limits
                loc = " · ".join(filter(None, [boss.get("layer"), boss.get("word"), boss.get("map")]))
                name = get_boss_name(boss, guild_id)
                lines.append(f"**{name}** — {loc}" if loc else f"**{name}**")
            title = f"{EMOJIS.get(boss_type, '🔴')} {boss_type} — respawna em {int(minutes_left)} min"
            if len(tied) > 1:
                title += f" · {len(tied)} juntos"
            embed = discord.Embed(title=title, description="\n".join(lines), color=COLORS.get(boss_type, 0x555555))
            embed.add_field(
                name="Horário do respawn",
                value=f"{spawn_time.astimezone(tz).strftime('%H:%M')} ({tz_name})",
                inline=False,
            )
            embeds.append(embed)

        content = " ".join(mentions) if mentions else None
        allowed_mentions = discord.AllowedMentions(everyone=True, roles=True, users=True)

        success = True
        for i in range(0, len(embeds), MAX_EMBEDS_PER_MESSAGE):
            try:
                await channel.send(
                    content=content if i == 0 else None,
                    embeds=embeds[i:i + MAX_EMBEDS_PER_MESSAGE],
                    allowed_mentions=allowed_mentions,
                )
            except (discord.Forbidden, discord.NotFound):
                success = False
                print(f"[check_bosses] Sem acesso ao canal {channel_id} ({guild_id}) pra postar alerta.")
                break
            except discord.HTTPException as e:
                success = False
                print(f"[check_bosses] Erro postando alerta de canal em {channel_id}: {e}")
                break

        await api_client.report_channel_health(guild_id, channel_id, success=success)

    # ====================== NOTIFICAÇÕES GENÉRICAS DO SITE ======================
    # Watchlist/ranking/badge/etc. events created on the site (user_notifications table).
    # The site renders the text and claims each row atomically when we fetch it, so there's
    # no dedupe to do here -- just DM whatever comes back. Unlike boss alerts these are not
    # time-boxed, so they're NOT scheduled for auto-delete.
    @tasks.loop(minutes=1)
    async def check_site_notifications(self):
        try:
            notifications = await api_client.claim_site_notifications()
        except Exception as e:
            print(f"[check_site_notifications] Falha ao buscar notificações: {e}")
            return
        if not notifications:
            return

        by_user: dict[str, list[dict]] = {}
        for n in notifications:
            by_user.setdefault(str(n.get("discord_id")), []).append(n)

        for discord_id, items in by_user.items():
            try:
                await self._send_site_notifications(discord_id, items)
            except Exception as e:
                print(f"[check_site_notifications] Erro enviando pra {discord_id}: {e}")
            await asyncio.sleep(1)  # same pacing as the boss DMs

    @check_site_notifications.before_loop
    async def before_site_notifications(self):
        await self.bot.wait_until_ready()

    async def _send_site_notifications(self, discord_id: str, items: list[dict]):
        try:
            user = await self.bot.fetch_user(int(discord_id))
        except (TypeError, ValueError):
            return
        except discord.NotFound:
            await api_client.unlink(discord_id)
            print(f"[check_site_notifications] Discord {discord_id} não existe mais, desvinculado.")
            return
        except discord.HTTPException as e:
            print(f"[check_site_notifications] Erro buscando usuário {discord_id}: {e}")
            return

        embeds = []
        for n in items:
            embed = discord.Embed(
                title=f"{SITE_NOTIF_EMOJIS.get(n.get('kind'), '🔔')} {n.get('title', '')}"[:256],
                description=(n.get("body") or "")[:4096] or None,
                color=SITE_NOTIF_COLOR,
                url=n.get("url") or None,
            )
            embed.set_footer(text="Open MIR4")
            embeds.append(embed)

        for i in range(0, len(embeds), MAX_EMBEDS_PER_MESSAGE):
            try:
                await user.send(embeds=embeds[i:i + MAX_EMBEDS_PER_MESSAGE])
            except discord.Forbidden:
                # DMs closed -- already claimed on the site, so these are dropped; the bell
                # on the site still has them
                print(f"[check_site_notifications] DM bloqueada por {discord_id}, pulando.")
                return
            except discord.HTTPException as e:
                print(f"[check_site_notifications] Erro enviando DM pra {discord_id}: {e}")
                return

    # ====================== LIMPEZA DIÁRIA ======================
    # Unrelated to boss alerts -- untouched by this rework other than the storage swap.
    @tasks.loop(minutes=1)
    async def check_cleanup(self):
        try:
            channels = await api_client.get_all_channels()
        except Exception as e:
            print(f"[check_cleanup] Falha ao buscar canais: {e}")
            return

        for ch in channels:
            if not ch.get("cleanup_enabled") or not ch.get("cleanup_time"):
                continue

            tz = ZoneInfo(ch.get("timezone") or "America/Sao_Paulo")
            local_now = datetime.now(tz)
            local_date = local_now.strftime("%Y-%m-%d")
            local_time = local_now.strftime("%H:%M")

            if local_time != ch["cleanup_time"]:
                continue
            if ch.get("cleanup_last_ran") == local_date:
                continue  # já rodou hoje

            await api_client.update_channel(ch["guild_id"], ch["channel_id"], cleanup_last_ran=local_date)

            channel = self.bot.get_channel(int(ch["channel_id"]))
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
    # Unrelated to boss alerts -- untouched by this rework other than the storage swap.
    # get_due_alarms() claims (deletes) due alarms atomically server-side, so there's no
    # local state to track between ticks any more.
    @tasks.loop(minutes=1)
    async def check_scheduled_alarms(self):
        try:
            alarms = await api_client.get_due_alarms()
        except Exception as e:
            print(f"[check_scheduled_alarms] Falha ao buscar alarmes vencidos: {e}")
            return

        for alarm in alarms:
            channel = self.bot.get_channel(int(alarm["channel_id"]))
            if channel is None:
                continue

            embed = discord.Embed(title="⏰ Alarme!", description=alarm.get("message") or "", color=0xFF8C00)
            embed.set_footer(text=f"Criado por {alarm.get('created_by', '?')}")

            try:
                await channel.send(content=alarm["mention_str"], embed=embed)
            except (discord.Forbidden, discord.HTTPException) as e:
                print(f"❌ Erro ao disparar alarme {alarm.get('id')}: {e}")

    @check_scheduled_alarms.before_loop
    async def before_scheduled_alarms(self):
        await self.bot.wait_until_ready()

    # ====================== SYNC PERIÓDICO (cargos, membros vinculados, permissões) ======
    # One loop per guild covers three things a design review found missing from the first
    # draft of this rework: roles and linked members only got synced once (at
    # /vincular_canal time, never again), and nothing ever checked whether the bot still
    # has permission to post/mention in a registered channel.
    @tasks.loop(minutes=30)
    async def check_guild_sync(self):
        for guild in self.bot.guilds:
            try:
                await sync_guild(self.bot, guild)
            except Exception as e:
                print(f"[check_guild_sync] Erro na guild {guild.id}: {e}")

    @check_guild_sync.before_loop
    async def before_guild_sync(self):
        await self.bot.wait_until_ready()


async def setup(bot):
    await bot.add_cog(Alerts(bot))
