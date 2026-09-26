"""Spawn-time math shared by cogs/commands.py and cogs/alerts.py.

Mirrors xdoapp's src/lib/bossSpawnCalc.ts: boss start times are always
America/Sao_Paulo wall-clock, and Brazil has had no DST since 2019, so a
fixed UTC-3 offset is safe. Uses a fixed epoch anchor + modular arithmetic
(not a "rebuild today's start from now's calendar date" loop) so it stays
correct for spawns that fall between 00:00 and start_time -- a calendar-date
anchor would miscompute which day the anchor belongs to for those and return
a spawn later than the true next one.
"""
import math
from datetime import datetime, timezone, timedelta

SP_OFFSET = timedelta(hours=3)


def _anchor_ts(start_time: str) -> float:
    h, m = map(int, start_time.split(":"))
    anchor = datetime(1970, 1, 1, h, m, tzinfo=timezone.utc) + SP_OFFSET
    return anchor.timestamp()


def next_spawn_ts(start_time: str, respawn_minutes: int, now_ts: float) -> float:
    anchor_ts = _anchor_ts(start_time)
    respawn_s = respawn_minutes * 60
    steps = math.ceil((now_ts - anchor_ts) / respawn_s)
    return anchor_ts + steps * respawn_s


def next_spawn_time(start_time: str, respawn_minutes: int, now: datetime) -> datetime:
    ts = next_spawn_ts(start_time, respawn_minutes, now.timestamp())
    return datetime.fromtimestamp(ts, tz=now.tzinfo or timezone.utc)


def upcoming_spawns(start_time: str, respawn_minutes: int, now: datetime, minutes_ahead: float):
    """All spawns from `now` up to `minutes_ahead` minutes ahead, in `now`'s tz."""
    now_ts = now.timestamp()
    end_ts = now_ts + minutes_ahead * 60
    respawn_s = respawn_minutes * 60
    tz = now.tzinfo or timezone.utc
    t = next_spawn_ts(start_time, respawn_minutes, now_ts)
    spawns = []
    while t <= end_ts:
        spawns.append(datetime.fromtimestamp(t, tz=tz))
        t += respawn_s
    return spawns


def upcoming_all(bosses: list[dict], now: datetime):
    """(boss, spawn_time) pairs for every active boss's next spawn, sorted ascending."""
    pairs = [
        (boss, next_spawn_time(boss["start_time"], boss["respawn_minutes"], now))
        for boss in bosses
        if boss.get("active", True)
    ]
    pairs.sort(key=lambda pair: pair[1])
    return pairs
