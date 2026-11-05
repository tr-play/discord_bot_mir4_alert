"""get_guild_lang() is called synchronously dozens of times per command, inside t() --
making it hit the network on every call would force t() to become async too, a large
ripple across every command in commands.py/account.py. Instead the in-memory cache is
kept exactly as it was (still synchronous), just populated differently: a bulk fetch at
bot startup (prewarm_guild_languages(), called once from bot.py's on_ready) instead of a
lazy per-guild read of data/guilds.json."""
import json
import os

import api_client

SUPPORTED_LANGS = {"pt", "en", "es"}

_translations: dict = {}
_guild_lang_cache: dict = {}
_prewarmed = False


def _load_lang(lang: str) -> dict:
    if lang not in _translations:
        path = os.path.join("i18n", f"{lang}.json")
        try:
            with open(path, "r", encoding="utf-8") as f:
                _translations[lang] = json.load(f)
        except Exception:
            _translations[lang] = {}
    return _translations[lang]


def _locale_to_lang(locale) -> str:
    """Map Discord locale (e.g. 'pt-BR', 'en-US') to our supported lang code."""
    s = str(locale).lower()
    if s.startswith("pt"):
        return "pt"
    if s.startswith("es"):
        return "es"
    if s.startswith("en"):
        return "en"
    return "pt"


def get_guild_lang(guild_id) -> str | None:
    """Returns the explicitly configured guild language, or None if not set. Purely a
    cache read now -- never touches the network or disk. A guild not seen by
    prewarm_guild_languages() (created after the last bot startup) simply reads as
    unset until /setup_language calls set_guild_lang() for it, or the bot restarts."""
    return _guild_lang_cache.get(str(guild_id))


def set_guild_lang(guild_id, lang: str):
    _guild_lang_cache[str(guild_id)] = lang


async def prewarm_guild_languages():
    """Populates the whole cache in one call -- run once from bot.py's on_ready, before
    any command can run. Safe to call again later (e.g. a manual admin refresh); it only
    ever adds/overwrites entries, never clears ones set by set_guild_lang() in between."""
    global _prewarmed
    try:
        languages = await api_client.get_all_guild_languages()
    except Exception as e:
        print(f"[i18n] Falha ao pré-aquecer idiomas das guilds: {e}")
        return
    for gid, lang in languages.items():
        if lang in SUPPORTED_LANGS:
            _guild_lang_cache[gid] = lang
    _prewarmed = True
    print(f"[i18n] {len(languages)} idiomas de guild pré-carregados.")


def _resolve_lang(guild_id, locale=None) -> str:
    """
    Priority: guild setting > user Discord locale > default (pt).
    Guild setting lets admins enforce a language for all members.
    User locale provides auto-detection when no guild setting exists.
    """
    guild_lang = get_guild_lang(guild_id)
    if guild_lang:
        return guild_lang
    if locale:
        return _locale_to_lang(locale)
    return "pt"


def t(guild_id, key: str, locale=None, **kwargs) -> str:
    lang = _resolve_lang(guild_id, locale)
    text = _load_lang(lang).get(key) or _load_lang("pt").get(key) or key
    return text.format(**kwargs) if kwargs else text


def get_boss_name(boss: dict, guild_id, locale=None) -> str:
    lang = _resolve_lang(guild_id, locale)
    if lang != "pt":
        return boss.get("name_en") or boss.get("name", "")
    return boss.get("name", "")
