"""All boss-alert data access for the bot goes through the xdoApp site's own API --
the bot never talks to Postgres directly. Reads (the boss schedule) hit a public,
unauthenticated route; writes (account linking, completions, notification dedupe,
notify-type toggle) hit routes under /api/discord/bot/* that require the shared
BOT_API_SECRET, mirroring how src/app/api/cron/send-push/route.ts already checks
CRON_SECRET on the site side.
"""
import json
import os
import time

import httpx

from config import SITE_BASE_URL, BOT_API_SECRET

BOSSES_FALLBACK_FILE = "data/bosses.json"
SCHEDULE_CACHE_TTL_SECONDS = 300

_client: httpx.AsyncClient | None = None
_schedule_cache: dict = {"data": None, "at": 0.0}


def _get_client() -> httpx.AsyncClient:
    global _client
    if _client is None:
        _client = httpx.AsyncClient(base_url=SITE_BASE_URL, timeout=10.0)
    return _client


def _bot_headers() -> dict:
    return {"Authorization": f"Bearer {BOT_API_SECRET}"}


async def _call(method: str, path: str, **kwargs) -> dict:
    """Wraps a write call to /api/discord/bot/*: network errors, timeouts, and a
    non-JSON body (an HTML error page from a proxy/edge during a deploy, a 5xx with no
    body, etc.) all become a normal {"status", "body"} result instead of an uncaught
    exception -- a transient site hiccup should surface as a friendly error message to
    the user, not a dead interaction with a raw traceback in the bot's own log."""
    try:
        resp = await _get_client().request(method, path, **kwargs)
    except httpx.HTTPError as e:
        print(f"[api_client] {method} {path} falhou (rede): {e}")
        return {"status": 0, "body": {"error": "network_error"}}
    try:
        body = resp.json()
    except (json.JSONDecodeError, ValueError):
        print(f"[api_client] {method} {path} devolveu resposta não-JSON (status {resp.status_code})")
        body = {"error": "bad_response"}
    return {"status": resp.status_code, "body": body}


def _load_bosses_fallback() -> list[dict]:
    try:
        with open(BOSSES_FALLBACK_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        # Old bosses.json shape used "start"/"respawn" -- normalize to the API's
        # "start_time"/"respawn_minutes" so callers never have to special-case this.
        bosses = data.get("bosses", [])
        for boss in bosses:
            boss.setdefault("start_time", boss.get("start"))
            boss.setdefault("respawn_minutes", boss.get("respawn"))
        return bosses
    except Exception:
        return []


async def get_boss_schedule(force: bool = False) -> list[dict]:
    now = time.monotonic()
    if not force and _schedule_cache["data"] is not None and (now - _schedule_cache["at"]) < SCHEDULE_CACHE_TTL_SECONDS:
        return _schedule_cache["data"]

    try:
        resp = await _get_client().get("/api/boss-schedule")
        resp.raise_for_status()
        data = resp.json()
        _schedule_cache["data"] = data
        _schedule_cache["at"] = now
        return data
    except Exception as e:
        print(f"[api_client] Falha ao buscar /api/boss-schedule, usando fallback local: {e}")
        return _schedule_cache["data"] or _load_bosses_fallback()


async def claim_code(discord_id: str, discord_username: str, code: str) -> dict:
    return await _call(
        "POST",
        "/api/discord/bot/claim-code",
        headers=_bot_headers(),
        json={"code": code, "discord_id": discord_id, "discord_username": discord_username},
    )


async def unlink(discord_id: str) -> dict:
    return await _call(
        "POST", "/api/discord/bot/unlink", headers=_bot_headers(), json={"discord_id": discord_id}
    )


async def get_subscribers() -> list[dict]:
    result = await _call("GET", "/api/discord/bot/subscribers", headers=_bot_headers())
    return result["body"] if result["status"] == 200 and isinstance(result["body"], list) else []


async def claim_notification(discord_id: str, boss_id: str, spawn_time_iso: str) -> bool:
    result = await _call(
        "POST",
        "/api/discord/bot/claim-notification",
        headers=_bot_headers(),
        json={"discord_id": discord_id, "boss_id": boss_id, "spawn_time": spawn_time_iso},
    )
    return result["status"] == 200 and bool(result["body"].get("claimed"))


async def set_completion(discord_id: str, boss_id: str, done: bool) -> dict:
    method = "POST" if done else "DELETE"
    return await _call(
        method,
        "/api/discord/bot/completions",
        headers=_bot_headers(),
        json={"discord_id": discord_id, "boss_id": boss_id},
    )


async def set_notify_type(discord_id: str, boss_type: str, enabled: bool) -> dict:
    return await _call(
        "PATCH",
        "/api/discord/bot/notify-type",
        headers=_bot_headers(),
        json={"discord_id": discord_id, "type": boss_type, "enabled": enabled},
    )


async def claim_site_notifications() -> list[dict]:
    """Generic site notifications (watchlist, ranking, badges...) waiting for a Discord DM.
    The site claims them atomically on this call, so whatever comes back is ours to send
    exactly once -- already rendered (title/body in PT), each with the target discord_id."""
    result = await _call("POST", "/api/discord/bot/notifications/claim", headers=_bot_headers())
    return result["body"] if result["status"] == 200 and isinstance(result["body"], list) else []


# ====================== GUILD CONFIG / LANGUAGE ======================

async def get_guild_config(guild_id: str) -> dict:
    """{"language": str|None, "channels": [...]}. Replaces the per-guild read of
    data/guilds.json that cogs/commands.py's get_channel_config()/`/config` used to do."""
    result = await _call("GET", "/api/discord/bot/guild-config", headers=_bot_headers(), params={"guild_id": guild_id})
    return result["body"] if result["status"] == 200 and isinstance(result["body"], dict) else {"language": None, "channels": []}


async def set_guild_language(guild_id: str, language: str) -> dict:
    return await _call(
        "PATCH", "/api/discord/bot/guild-config", headers=_bot_headers(), json={"guild_id": guild_id, "language": language}
    )


async def get_all_guild_languages() -> dict:
    """{guild_id: language} for every guild -- the bulk fetch i18n.prewarm_guild_languages()
    calls once at startup."""
    result = await _call("GET", "/api/discord/bot/guild-languages", headers=_bot_headers())
    body = result["body"]
    return body.get("languages", {}) if result["status"] == 200 and isinstance(body, dict) else {}


# ====================== CHANNELS ======================

async def register_channel(guild_id: str, channel_id: str, guild_name: str | None, channel_name: str | None) -> dict:
    """/setup_canal."""
    return await _call(
        "POST",
        "/api/discord/bot/channels",
        headers=_bot_headers(),
        json={"guild_id": guild_id, "channel_id": channel_id, "guild_name": guild_name, "channel_name": channel_name},
    )


async def remove_channel(guild_id: str, channel_id: str) -> dict:
    """/remove_canal."""
    return await _call(
        "DELETE", "/api/discord/bot/channels", headers=_bot_headers(), json={"guild_id": guild_id, "channel_id": channel_id}
    )


async def update_channel(guild_id: str, channel_id: str, **fields) -> dict:
    """/setup_timezone, /setup_limpeza, /desativar_limpeza, and check_cleanup stamping
    cleanup_last_ran -- only the kwargs passed change (timezone, cleanup_enabled,
    cleanup_time, cleanup_last_ran)."""
    return await _call(
        "PATCH",
        "/api/discord/bot/channels",
        headers=_bot_headers(),
        json={"guild_id": guild_id, "channel_id": channel_id, **fields},
    )


async def get_all_channels() -> list[dict]:
    """Every registered channel across every guild -- feeds check_cleanup, replacing the
    old data/guilds.json walk."""
    result = await _call("GET", "/api/discord/bot/channels", headers=_bot_headers())
    body = result["body"]
    return body.get("channels", []) if result["status"] == 200 and isinstance(body, dict) else []


async def get_guild_channels(guild_id: str) -> list[dict]:
    """Registered channels for one guild -- feeds the 30-min permission-check routine, so
    it only checks channels that are actually registered instead of every text channel."""
    result = await _call("GET", "/api/discord/bot/channels", headers=_bot_headers(), params={"guild_id": guild_id})
    body = result["body"]
    return body.get("channels", []) if result["status"] == 200 and isinstance(body, dict) else []


# ====================== CANAL: VÍNCULO AO SITE ======================

async def claim_channel_code(code: str, guild_id: str, channel_id: str, guild_name: str | None, channel_name: str | None) -> dict:
    """/vincular_canal -- claims discord_guild_channels.owner_user_id for whoever
    generated the code on the site."""
    return await _call(
        "POST",
        "/api/discord/bot/claim-channel-code",
        headers=_bot_headers(),
        json={
            "code": code,
            "guild_id": guild_id,
            "channel_id": channel_id,
            "guild_name": guild_name,
            "channel_name": channel_name,
        },
    )


async def sync_guild_roles(guild_id: str, roles: list[dict]) -> dict:
    """roles: [{"role_id", "name", "color"}, ...]. Full-replace sync, called right after
    /vincular_canal and again every 30 min."""
    return await _call(
        "PUT", "/api/discord/bot/guild-roles", headers=_bot_headers(), json={"guild_id": guild_id, "roles": roles}
    )


async def sync_linked_members(guild_id: str, members: list[dict]) -> dict:
    """members: [{"discord_id", "username"}, ...] -- every member fetched from the guild;
    the site keeps only the intersection with a linked account."""
    return await _call(
        "PUT", "/api/discord/bot/guild-linked-members", headers=_bot_headers(), json={"guild_id": guild_id, "members": members}
    )


async def report_channel_health(guild_id: str, channel_id: str, success: bool | None = None, missing_permissions: list[str] | None = None) -> dict:
    """success=True resets the consecutive-failure counter; success=False increments it
    (auto-disabling the channel past the threshold server-side); missing_permissions
    overwrites the permission-warning list shown on the site's card. Pass only what
    changed -- None means "leave that part alone"."""
    payload: dict = {"guild_id": guild_id, "channel_id": channel_id}
    if success is not None:
        payload["success"] = success
    if missing_permissions is not None:
        payload["missing_permissions"] = missing_permissions
    return await _call("PUT", "/api/discord/bot/channel-health", headers=_bot_headers(), json=payload)


async def report_guild_removed(guild_id: str) -> dict:
    """on_guild_remove -- marks every channel of that guild inactive in one shot."""
    return await _call("POST", "/api/discord/bot/guild-removed", headers=_bot_headers(), json={"guild_id": guild_id})


# ====================== ALARMES AGENDADOS (/alarme) ======================

async def create_alarm(guild_id: str, channel_id: str, target_type: str, target_id: str, mention_str: str, fire_at_iso: str, created_by: str, message: str | None) -> dict:
    return await _call(
        "POST",
        "/api/discord/bot/alarms",
        headers=_bot_headers(),
        json={
            "guild_id": guild_id,
            "channel_id": channel_id,
            "target_type": target_type,
            "target_id": target_id,
            "mention_str": mention_str,
            "fire_at": fire_at_iso,
            "created_by": created_by,
            "message": message,
        },
    )


async def get_alarms(guild_id: str, channel_id: str) -> list[dict]:
    result = await _call("GET", "/api/discord/bot/alarms", headers=_bot_headers(), params={"guild_id": guild_id, "channel_id": channel_id})
    body = result["body"]
    return body.get("alarms", []) if result["status"] == 200 and isinstance(body, dict) else []


async def get_due_alarms() -> list[dict]:
    """Atomically claims (deletes) every alarm whose fire_at has passed -- once fired, an
    alarm is done, same as the old JSON pop-after-send."""
    result = await _call("GET", "/api/discord/bot/alarms", headers=_bot_headers(), params={"due": "1"})
    body = result["body"]
    return body.get("alarms", []) if result["status"] == 200 and isinstance(body, dict) else []


async def cancel_alarm(alarm_id: str) -> dict:
    """Returns {"status": 200, "body": {"alarm": {...}}} on success -- the caller echoes
    mention_str/created_by back in the confirmation message, same as before."""
    return await _call("DELETE", "/api/discord/bot/alarms", headers=_bot_headers(), json={"id": alarm_id})


# ====================== ALERTAS DE BOSS POR CANAL ======================

async def get_channel_alerts() -> list[dict]:
    """Every enabled alert on every active channel -- one call per check_bosses tick,
    same shape as get_subscribers()."""
    result = await _call("GET", "/api/discord/bot/channel-alerts", headers=_bot_headers())
    body = result["body"]
    return body.get("alerts", []) if result["status"] == 200 and isinstance(body, dict) else []


async def claim_channel_alert(guild_id: str, channel_id: str, boss_type: str, spawn_time_iso: str, mark_minutes: int) -> bool:
    result = await _call(
        "POST",
        "/api/discord/bot/claim-channel-alert",
        headers=_bot_headers(),
        json={
            "guild_id": guild_id,
            "channel_id": channel_id,
            "boss_type": boss_type,
            "spawn_time": spawn_time_iso,
            "mark_minutes": mark_minutes,
        },
    )
    return result["status"] == 200 and bool(result["body"].get("claimed"))
