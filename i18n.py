import json
import os

GUILDS_FILE = "data/guilds.json"
SUPPORTED_LANGS = {"pt", "en", "es"}

_translations: dict = {}
_guild_lang_cache: dict = {}


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
    """Returns the explicitly configured guild language, or None if not set."""
    gid = str(guild_id)
    if gid in _guild_lang_cache:
        return _guild_lang_cache[gid]
    try:
        with open(GUILDS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        lang = data.get("guilds", {}).get(gid, {}).get("language")
    except Exception:
        lang = None
    if lang not in SUPPORTED_LANGS:
        lang = None
    _guild_lang_cache[gid] = lang
    return lang


def set_guild_lang(guild_id, lang: str):
    _guild_lang_cache[str(guild_id)] = lang


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
