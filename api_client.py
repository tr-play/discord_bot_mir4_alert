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
