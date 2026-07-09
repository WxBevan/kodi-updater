import difflib
import gzip
import json
import re
import shutil
import subprocess
import sys
import unicodedata
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

try:
    import xbmcvfs
except Exception:
    xbmcvfs = None

try:
    import xbmc
except Exception:
    xbmc = None


def kodi_path(path):
    if xbmcvfs:
        return Path(xbmcvfs.translatePath(path))
    return Path(path)


IPTV_OUTPUT_DIR = kodi_path("special://profile/addon_data/plugin.video.fenlight/iptv")
IPTV_CACHE_DIR = IPTV_OUTPUT_DIR / "cache"


IPTV_SIMPLE_SETTINGS_DIR = kodi_path("special://profile/addon_data/pvr.iptvsimple")

IPTV_SIMPLE_M3U_SPECIAL = "special://userdata/addon_data/plugin.video.fenlight/iptv/IPTV.m3u"
IPTV_SIMPLE_EPG_SPECIAL = "special://userdata/addon_data/plugin.video.fenlight/iptv/IPTV-EPG.xml"

# =========================
# CONFIG
# =========================

SERVER = ""
USERNAME = ""
PASSWORD = ""

INPUT_JSON = str(IPTV_CACHE_DIR / "live_streams.json")
OUTPUT_FILE = str(IPTV_OUTPUT_DIR / "IPTV.m3u")
REPORT_FILE = str(IPTV_OUTPUT_DIR / "IPTV-Report.txt")
OUTPUT_EPG_FILE = str(IPTV_OUTPUT_DIR / "IPTV-EPG.xml")
OUTPUT_FORMAT = "ts"  # use "m3u8" if you prefer

DOWNLOAD_LIVE_STREAMS = True
# Built dynamically by build_live_streams_url() so SERVER/USERNAME/PASSWORD are validated first.
LIVE_STREAMS_URL = ""

DOWNLOAD_EPG = True
EPG_URL = "https://epgshare01.online/epgshare01/epg_ripper_UK1.xml.gz"
EPG_GZ_FILE = str(IPTV_CACHE_DIR / "epg_ripper_UK1.xml.gz")

# If True, channels are only written to the M3U if they can be matched to the EPGShare XML.
# This keeps Kodi clean and avoids channels with blank guide data.
REQUIRE_EPG_MATCH = True

# Confidence thresholds. Anything below these goes to the report instead of being guessed silently.
MIN_STREAM_MATCH_SCORE = 260
MIN_EPG_MATCH_SCORE = 500
MIN_SCORE_GAP = 75  # Best result must beat second-best by this much unless it is very high confidence.
HIGH_CONFIDENCE_SCORE = 1000

# Optional exact EPG overrides for the rare cases where fuzzy matching is not enough.
# Keep this small. The script works without it; this is just a safety valve.
EPG_ID_OVERRIDES = {
    # "sky_sports_main_event": "SkySpMainEvHD.uk",
}


# =========================
# WANTED CHANNELS
# Source of truth is now the channel you want, not the provider's tvg-id.
# provider_epg_ids keeps your old reliable selection path intact when the provider still uses those IDs.
# aliases are used only as fallback when the provider JSON uses different IDs/names.
# epg_aliases help the script discover the matching EPGShare XMLTV ID automatically.
# reject terms prevent bad close matches such as Sky Sports Main Event -> Sky Sports Box Office.
# =========================

WANTED_CHANNELS = [
    # Sports
    {
        "key": "sky_sports_box_office",
        "name": "Sky Sports Box Office",
        "group": "Sports",
        "provider_epg_ids": ["skysportsboxoffice.uk"],
        "aliases": ["sky sports box office", "skysp box off", "skyspboxoff"],
        "epg_aliases": ["skysp box off", "skyspboxoff"],
        "reject": ["main event", "racing", "news", "premier league", "football", "cricket", "golf", "f1", "tennis"],
        "enabled_default": False,
    },
    {
        "key": "sky_sports_main_event",
        "name": "Sky Sports Main Event",
        "group": "Sports",
        "provider_epg_ids": ["skysportsmainevent.uk"],
        "aliases": ["sky sports main event", "skysp main ev", "skyspmainev", "main event"],
        "epg_aliases": ["skysp main ev", "skyspmainev", "SkySpMainEvHD"],
        "reject": ["box office", "racing", "news", "mix", "action", "plus", "+1"],
        "enabled_default": True,
    },
    {
        "key": "sky_sports_premier_league",
        "name": "Sky Sports Premier League",
        "group": "Sports",
        "provider_epg_ids": ["skysportspremiereleague.uk"],
        "aliases": ["sky sports premier league", "sky sports pl", "skysp pl", "skysppl"],
        "epg_aliases": ["skysp pl", "skysppl"],
        "reject": ["box office", "racing", "news", "mix", "action", "plus", "+1"],
        "enabled_default": True,
    },
    {
        "key": "sky_sports_football",
        "name": "Sky Sports Football",
        "group": "Sports",
        "provider_epg_ids": ["skysportsfootball.uk"],
        "aliases": ["sky sports football", "skysp fball", "skyspfball"],
        "epg_aliases": ["skysp fball", "skyspfball"],
        "reject": ["box office", "racing", "news", "mix", "action", "plus", "+1"],
        "enabled_default": True,
    },
    {
        "key": "sky_sports_cricket",
        "name": "Sky Sports Cricket",
        "group": "Sports",
        "provider_epg_ids": ["skysportscricket.uk"],
        "aliases": ["sky sports cricket", "skysp cricket", "skyspcricket"],
        "epg_aliases": ["skysp cricket", "skyspcricket"],
        "reject": ["box office", "racing", "news", "mix", "action", "plus", "+1"],
        "enabled_default": True,
    },
    {
        "key": "sky_sports_golf",
        "name": "Sky Sports Golf",
        "group": "Sports",
        "provider_epg_ids": ["skysportsgolf.uk"],
        "aliases": ["sky sports golf", "skysp golf", "skyspgolf"],
        "epg_aliases": ["skysp golf", "skyspgolf"],
        "reject": ["box office", "racing", "news", "mix", "action", "plus", "+1"],
        "enabled_default": True,
    },
    {
        "key": "sky_sports_f1",
        "name": "Sky Sports F1",
        "group": "Sports",
        "provider_epg_ids": ["skysportsf1.uk"],
        "aliases": ["sky sports f1", "skysp f1", "skyspf1"],
        "epg_aliases": ["skysp f1", "skyspf1"],
        "reject": ["box office", "racing", "news", "mix", "action", "plus", "+1"],
        "enabled_default": False,
    },
    {
        "key": "sky_sports_tennis",
        "name": "Sky Sports Tennis",
        "group": "Sports",
        "provider_epg_ids": ["skysportstennis.uk"],
        "aliases": ["sky sports tennis", "skysp tennis"],
        "epg_aliases": ["skysp tennis"],
        "reject": ["box office", "racing", "news", "mix", "action", "plus", "+1"],
        "enabled_default": True,
    },
    {
        "key": "tnt_sports_1",
        "name": "TNT Sports 1",
        "group": "Sports",
        "provider_epg_ids": ["tntsports1.uk"],
        "aliases": ["tnt sports 1", "tnt sport 1"],
        "epg_aliases": ["tnt sports 1"],
        "reject": ["box office", "ultimate", "2", "3", "4", "5", "6", "7", "8", "9", "10"],
        "enabled_default": True,
    },
    {
        "key": "tnt_sports_2",
        "name": "TNT Sports 2",
        "group": "Sports",
        "provider_epg_ids": ["tntsports2.uk"],
        "aliases": ["tnt sports 2", "tnt sport 2"],
        "epg_aliases": ["tnt sports 2"],
        "reject": ["box office", "ultimate", "1", "3", "4", "5", "6", "7", "8", "9", "10"],
        "enabled_default": True,
    },
    {
        "key": "tnt_sports_3",
        "name": "TNT Sports 3",
        "group": "Sports",
        "provider_epg_ids": ["tntsports3.uk"],
        "aliases": ["tnt sports 3", "tnt sport 3"],
        "epg_aliases": ["tnt sports 3"],
        "reject": ["box office", "ultimate", "1", "2", "4", "5", "6", "7", "8", "9", "10"],
        "enabled_default": True,
    },
    {
        "key": "tnt_sports_4",
        "name": "TNT Sports 4",
        "group": "Sports",
        "provider_epg_ids": ["tntsports4.uk"],
        "aliases": ["tnt sports 4", "tnt sport 4"],
        "epg_aliases": ["tnt sports 4"],
        "reject": ["box office", "ultimate", "1", "2", "3", "5", "6", "7", "8", "9", "10"],
        "enabled_default": True,
    },
    {
        "key": "mutv",
        "name": "MUTV",
        "group": "Sports",
        "provider_epg_ids": ["mutv.uk"],
        "aliases": ["mutv", "man utd tv", "manchester united tv"],
        "epg_aliases": ["mutv"],
        "enabled_default": False,
    },

    # BBC core
    {
        "key": "bbc_one",
        "name": "BBC 1",
        "group": "BBC",
        "provider_epg_ids": ["bbc1.uk"],
        "aliases": ["bbc one", "bbc one london", "bbc1", "bbc 1"],
        "epg_aliases": ["bbc one london", "bbc one lon", "bbc one hd"],
        "reject": ["cbbc", "cbeebies", "two", "three", "four", "parliament", "scotland", "wales", "alba"],
        "enabled_default": True,
    },
    {
        "key": "bbc_two",
        "name": "BBC 2",
        "group": "BBC",
        "provider_epg_ids": ["bbc2.uk"],
        "aliases": ["bbc two", "bbc2", "bbc 2"],
        "epg_aliases": ["bbc two hd"],
        "reject": ["one", "three", "four", "parliament", "cbbc", "cbeebies", "alba"],
        "enabled_default": True,
    },
    {
        "key": "bbc_parliament",
        "name": "BBC Parliament",
        "group": "BBC",
        "provider_epg_ids": ["bbcparliament.uk"],
        "aliases": ["bbc parliament"],
        "epg_aliases": ["bbc parliament"],
        "enabled_default": True,
    },
    {
        "key": "bbc_one_wales",
        "name": "BBC One Wales",
        "group": "BBC",
        "provider_epg_ids": ["bbconewales.uk"],
        "aliases": ["bbc one wales", "bbc one wal"],
        "epg_aliases": ["bbc one wales", "bbc one wal"],
        "reject": ["two"],
        "enabled_default": False,
    },

    # ITV core
    {"key": "itv1", "name": "ITV1", "group": "ITV", "provider_epg_ids": ["itv1.uk"], "aliases": ["itv1", "itv 1"], "epg_aliases": ["itv1 hd"], "reject": ["plus 1", "+1", "itv2", "itv3", "itv4"], "enabled_default": True},
    {"key": "itv2", "name": "ITV2", "group": "ITV", "provider_epg_ids": ["itv2.uk"], "aliases": ["itv2", "itv 2"], "epg_aliases": ["itv2 hd"], "reject": ["plus 1", "+1", "itv1", "itv3", "itv4"], "enabled_default": True},
    {"key": "itv3", "name": "ITV3", "group": "ITV", "provider_epg_ids": ["itv3.uk"], "aliases": ["itv3", "itv 3"], "epg_aliases": ["itv3 hd"], "reject": ["plus 1", "+1", "itv1", "itv2", "itv4"], "enabled_default": True},
    {"key": "itv4", "name": "ITV4", "group": "ITV", "provider_epg_ids": ["itv4.uk"], "aliases": ["itv4", "itv 4"], "epg_aliases": ["itv4 hd"], "reject": ["plus 1", "+1", "itv1", "itv2", "itv3"], "enabled_default": True},

    # Channel 4 / 5 core
    {"key": "channel_4", "name": "Channel 4", "group": "Channel 4 & 5", "provider_epg_ids": ["channel4.uk"], "aliases": ["channel 4"], "epg_aliases": ["channel 4 hd"], "reject": ["plus 1", "+1", "4seven", "film4", "e4", "more4"], "enabled_default": True},
    {"key": "channel_5", "name": "Channel 5", "group": "Channel 4 & 5", "provider_epg_ids": ["channel5.uk"], "aliases": ["channel 5"], "epg_aliases": ["channel 5 hd"], "reject": ["plus 1", "+1", "5star", "5usa", "5select", "5action"], "enabled_default": True},

]



# Compatibility dicts so your existing scoring logic still works.
WANTED_EPG_IDS = {
    epg_id.lower(): channel["group"]
    for channel in WANTED_CHANNELS
    for epg_id in channel.get("provider_epg_ids", [])
}

DISPLAY_NAME_OVERRIDES = {
    epg_id.lower(): channel["name"]
    for channel in WANTED_CHANNELS
    for epg_id in channel.get("provider_epg_ids", [])
}

CHANNEL_BY_KEY = {channel["key"]: channel for channel in WANTED_CHANNELS}



# =========================
# FRIENDLY ERROR HANDLING
# =========================

class GeneratorError(Exception):
    """Raised for expected user-fixable generator problems."""


def mask_secret(value, keep=2):
    value = str(value or "")
    if not value:
        return ""
    if len(value) <= keep * 2:
        return "*" * len(value)
    return value[:keep] + "***" + value[-keep:]


def redact_url(url):
    # Avoid printing username/password into console logs.
    redacted = url
    if USERNAME:
        redacted = redacted.replace(urllib.parse.quote_plus(USERNAME), mask_secret(USERNAME))
        redacted = redacted.replace(USERNAME, mask_secret(USERNAME))
    if PASSWORD:
        redacted = redacted.replace(urllib.parse.quote_plus(PASSWORD), mask_secret(PASSWORD))
        redacted = redacted.replace(PASSWORD, mask_secret(PASSWORD))
    return redacted


def normalised_server():
    server = clean(SERVER).rstrip("/")
    if not server:
        raise GeneratorError(
            "Login details are missing. Fill in Xtream Server, Username and Password in the Accounts screen."
        )
    if not server.startswith(("http://", "https://")):
        raise GeneratorError(
            f"SERVER must start with http:// or https://. Current value: {SERVER!r}"
        )
    return server


def validate_login_config():
    missing = []
    if not clean(SERVER):
        missing.append("SERVER")
    if not clean(USERNAME):
        missing.append("USERNAME")
    if not clean(PASSWORD):
        missing.append("PASSWORD")

    if missing:
        raise GeneratorError(
            "Missing login details: " + ", ".join(missing) +
            ". Fill these in under Accounts → Xtream IPTV and run Generate IPTV again."
        )

    normalised_server()


def build_live_streams_url():
    server = normalised_server()
    query = urllib.parse.urlencode({
        "username": USERNAME,
        "password": PASSWORD,
        "action": "get_live_streams",
    })
    return f"{server}/player_api.php?{query}"


def write_failure_report(message):
    lines = [
        "Generation failed.",
        "",
        message,
        "",
        "No new IPTV.m3u or IPTV-EPG.xml was written by this failed run.",
    ]
    Path(REPORT_FILE).write_text("\n".join(lines) + "\n", encoding="utf-8")


def short_file_preview(path, limit=600):
    path = Path(path)
    if not path.exists():
        return ""
    data = path.read_bytes()[:limit]
    return data.decode("utf-8", errors="replace").strip()


def explain_bad_live_streams_response(payload):
    """
    Xtream/XUI panels usually return a list for action=get_live_streams.
    Bad/expired credentials often return an object, empty response, HTML, or an error string instead.
    """
    if isinstance(payload, list):
        if not payload:
            return (
                "The provider login worked enough to return JSON, but it returned 0 live streams. "
                "Double-check the subscription, server URL, username and password."
            )
        return ""

    if isinstance(payload, dict):
        user_info = payload.get("user_info") or {}
        auth = str(user_info.get("auth", "")).lower()
        status = str(user_info.get("status", "")).lower()
        message = payload.get("message") or payload.get("error") or payload.get("msg") or ""

        if auth in {"0", "false", "none"}:
            return "Login info not working: the provider returned auth=0. Double-check SERVER, USERNAME and PASSWORD."
        if status and status not in {"active", "1", "true"}:
            return f"Login info not working: subscription status is {status!r}. Double-check or renew the account."
        if message:
            return f"Provider did not return live streams. Message from provider: {message}"

        return (
            "Provider did not return the expected live-stream list. "
            "This usually means the login details are wrong, expired, or this server does not support "
            "player_api.php?action=get_live_streams."
        )

    return (
        "Provider response was not a live-stream list. "
        "Double-check SERVER, USERNAME and PASSWORD."
    )


def load_and_validate_live_streams(path):
    path = Path(path)
    try:
        payload = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except json.JSONDecodeError:
        preview = short_file_preview(path)
        raise GeneratorError(
            "Login info not working, server URL is wrong, or the provider did not return JSON.\n"
            f"First response text was:\n{preview[:600]}"
        )

    reason = explain_bad_live_streams_response(payload)
    if reason:
        raise GeneratorError(reason)

    # Basic shape check. Some providers return a list of strings/nulls when broken.
    valid_items = [item for item in payload if isinstance(item, dict) and get_stream_id(item)]
    if not valid_items:
        raise GeneratorError(
            "The provider returned JSON, but no usable live streams with stream_id were found. "
            "Double-check the provider, login details, or API compatibility."
        )

    return payload

# =========================
# BASIC HELPERS
# =========================

def clean(value):
    if value is None:
        return ""
    return str(value).replace('"', "'").strip()


def get_field(item, *names):
    for name in names:
        value = item.get(name)
        if value not in (None, ""):
            return value
    return ""


def get_stream_name(item):
    return clean(get_field(item, "name", "title", "stream_name"))


def get_provider_epg(item):
    return clean(get_field(item, "epg_channel_id", "tvg_id", "tvg-id", "channel_id")).lower()


def get_stream_id(item):
    return get_field(item, "stream_id", "id", "streamId")


def get_logo(item):
    return clean(get_field(item, "stream_icon", "logo", "tvg_logo", "tvg-logo"))


def is_adult(item):
    value = item.get("is_adult", 0)
    return value == 1 or value == "1" or str(value).lower() == "true"


def upper_name(item):
    return get_stream_name(item).upper()


def normalise_epg(epg):
    return clean(epg).lower()


def get_output_epg(item):
    # This is now the EPGShare XMLTV ID once matched.
    return item.get("_xmltv_id") or item.get("_forced_epg_id") or get_provider_epg(item)


# =========================
# TEXT MATCHING HELPERS
# =========================

def normalise_text(value):
    text = clean(value).lower()
    text = unicodedata.normalize("NFKD", text)

    # common visual/superscript/provider noise
    replacements = {
        "ᴿᴬᵂ": " raw ",
        "ᴴᴰ": " hd ",
        "ᵁᴴᴰ": " uhd ",
        "³⁸⁴⁰ᴾ": " 3840p ",
        "ʰᵉᵛᶜ": " hevc ",
        "⁴ᵏ": " 4k ",
        "&": " and ",
        "+1": " plus 1 ",
        "+": " plus ",
    }
    for old, new in replacements.items():
        text = text.replace(old.lower(), new)

    # Expand common EPGShare abbreviations before punctuation is stripped.
    abbreviation_replacements = {
        "skysp": "sky sports ",
        "boxoff": "box office",
        "mainev": "main event",
        "main ev": "main event",
        "fball": "football",
        "natgeo": "nat geo national geographic",
        "comedycent": "comedy central",
        "disc.": "discovery ",
        "disc ": "discovery ",
        "sci": "science",
        "plnt": "planet",
        "bbcone": "bbc one",
        "skypremiere": "sky premiere",
        "skyhistory": "sky history",
        "cartoon.netwrk": "cartoon network",
        "cartoon.net": "cartoon network",
    }
    for old, new in abbreviation_replacements.items():
        text = text.replace(old, new)

    text = re.sub(r"[^a-z0-9]+", " ", text)
    text = re.sub(r"\bhd\b|\buhd\b|\bsd\b|\buk\b", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def compact_text(value):
    return re.sub(r"[^a-z0-9]+", "", normalise_text(value))


def text_tokens(value):
    return set(normalise_text(value).split())


def contains_term(text, compact, term):
    term_norm = normalise_text(term)
    term_compact = compact_text(term)
    if not term_norm and not term_compact:
        return False

    words = term_norm.split()
    tokens = set(text.split())

    if len(words) == 1:
        word = words[0]
        if word.isdigit():
            return word in tokens
        return word in tokens or (len(term_compact) >= 4 and term_compact in compact)

    return term_norm in text or (len(term_compact) >= 4 and term_compact in compact)


def alias_match_score(search_text, aliases):
    text = normalise_text(search_text)
    compact = compact_text(search_text)
    tokens = set(text.split())
    score = 0
    best_ratio = 0

    for alias in aliases:
        alias_norm = normalise_text(alias)
        alias_compact = compact_text(alias)
        if not alias_norm or not alias_compact:
            continue

        if alias_norm == text:
            score += 300
        if alias_norm in text:
            score += 220
        if alias_compact in compact:
            score += 220

        words = [word for word in alias_norm.split() if word not in {"tv", "channel"}]
        if words:
            found = sum(1 for word in words if word in tokens or compact_text(word) in compact)
            if found == len(words):
                score += 160 + len(words) * 8
            else:
                score += int((found / len(words)) * 90)

        ratio = difflib.SequenceMatcher(None, alias_compact, compact).ratio()
        best_ratio = max(best_ratio, ratio)

    score += int(best_ratio * 90)
    return score


def apply_reject_penalties(score, search_text, reject_terms, penalty=450):
    text = normalise_text(search_text)
    compact = compact_text(search_text)
    for term in reject_terms:
        if contains_term(text, compact, term):
            score -= penalty
    return score


def stream_search_text(item):
    return " ".join([
        get_stream_name(item),
        get_provider_epg(item),
        clean(get_field(item, "category_name", "category", "group")),
    ])


# =========================
# DISPLAY NAME HELPERS
# =========================

def display_name(item):
    if item.get("_display_name"):
        return item["_display_name"]

    provider_epg = get_provider_epg(item)
    if provider_epg in DISPLAY_NAME_OVERRIDES:
        return DISPLAY_NAME_OVERRIDES[provider_epg]

    if item.get("_wanted_name"):
        return item["_wanted_name"]

    name = get_stream_name(item)

    # Remove repeated provider prefixes / stray colons at the start.
    changed = True
    while changed:
        changed = False
        original = name

        for prefix in ["NOW:", "UK:", "VIP:", "IRL:"]:
            if name.upper().startswith(prefix):
                name = name[len(prefix):].strip()

        name = name.lstrip(":").strip()

        if name != original:
            changed = True

    remove_bits = [
        "◉",
        "ᴿᴬᵂ",
        "ᴴᴰ",
        "ᵁᴴᴰ",
        "³⁸⁴⁰ᴾ",
        "ʰᵉᵛᶜ",
        "⁴ᵏ",
        "& ³⁸⁴⁰ᴾ",
    ]

    for bit in remove_bits:
        name = name.replace(bit, "")

    name = " ".join(name.split())
    return name


def sort_name(item):
    canonical = item.get("_canonical", get_output_epg(item))
    name = display_name(item).upper()

    # Keep visible name as "4K Sky Sports Main Event",
    # but sort it beside normal "Sky Sports Main Event".
    if canonical == "sky_sports_main_event_4k":
        return "SKY SPORTS MAIN EVENT Z 4K"

    return name


# =========================
# ORIGINAL STREAM SELECTION LOGIC, PRESERVED AS FIRST CHOICE
# =========================

def group_for(item):
    if item.get("_group"):
        return item["_group"]
    epg = get_provider_epg(item)
    return WANTED_EPG_IDS.get(epg, "")


def quality_score(item):
    name = upper_name(item)
    group = group_for(item)
    epg = get_provider_epg(item)
    score = 0

    # =========================
    # SPECIFIC FIXES
    # =========================

    # Amber incorrectly gives CBBC the BBC1 EPG sometimes.
    if epg == "bbc1.uk":
        if "BBC 1" in name or "BBC ONE" in name:
            score += 1000
        if "CBBC" in name or "CBEEBIES" in name:
            score -= 1000

    if epg == "bbc2.uk":
        if "BBC 2" in name or "BBC TWO" in name:
            score += 1000
        if "CBBC" in name or "CBEEBIES" in name:
            score -= 1000

    if epg == "skysportsboxoffice.uk":
        if "BOX OFFICE" in name:
            score += 1000
        if "MAIN EVENT" in name and "BOX OFFICE" not in name:
            score -= 500

    # =========================
    # GENERAL SCORING
    # =========================

    # VIP is no longer globally preferred because some VIP streams black-screen.
    # The only VIP duplicate we add deliberately is the separate 4K Sky Sports Main Event below.
    if name.startswith("NOW:"):
        score += 70 if group in {"Sports", "Entertainment", "Kids"} else 20

    if name.startswith("UK:"):
        score += 40 if group in {"BBC", "ITV", "Channel 4 & 5", "News", "Documentary", "Music"} else 25

    if name.startswith("VIP:"):
        score += 25

    if "4K" in name or "UHD" in name or "3840" in name or "³⁸⁴⁰" in name:
        score += 45

    if "RAW" in name or "ᴿᴬᵂ" in name:
        score += 35

    if "HD" in name or "ᴴᴰ" in name:
        score += 20

    if "HEVC" in name or "ʰᵉᵛᶜ" in name:
        score -= 50

    if "SD" in name:
        score -= 80

    if "+1" in name or " PLUS 1" in name:
        score -= 100

    if "PPV" in name:
        score -= 100

    if "REPLAY" in name:
        score -= 100

    if "NO EVENT" in name:
        score -= 100

    if "IRL:" in name:
        score -= 100

    return score


def choose_from_exact_provider_epg(wanted, options):
    """
    This keeps your existing behaviour for providers that still use your old epg_channel_id values.
    The only change is that the chosen item gets metadata added afterwards.
    """
    key = wanted["key"]

    # For normal Sky Sports Main Event, avoid picking the VIP stream.
    # We add the VIP/4K version separately as a second channel.
    if key == "sky_sports_main_event":
        non_vip_options = [
            item for item in options
            if not upper_name(item).startswith("VIP:")
        ]

        if non_vip_options:
            best_item = max(non_vip_options, key=quality_score)
        else:
            best_item = max(options, key=quality_score)
    else:
        best_item = max(options, key=quality_score)

    output = dict(best_item)
    output["_group"] = wanted["group"]
    output["_canonical"] = key
    output["_wanted_name"] = wanted["name"]
    output["_variants"] = len(options)
    output["_score"] = quality_score(best_item)
    output["_stream_match_method"] = "exact_provider_epg"
    output["_stream_match_score"] = "exact"
    return output


# =========================
# FUZZY STREAM FALLBACK
# Only used if exact provider EPG IDs are missing.
# =========================

def stream_match_score(wanted, item):
    if is_adult(item):
        return -9999

    if not get_stream_id(item):
        return -9999

    search = stream_search_text(item)
    aliases = [wanted["name"]] + wanted.get("aliases", []) + wanted.get("provider_epg_ids", [])
    score = alias_match_score(search, aliases)
    score = apply_reject_penalties(score, search, wanted.get("reject", []), penalty=450)

    name = upper_name(item)

    # provider/source quality hints should break ties between multiple valid-looking streams
    if name.startswith("NOW:") and wanted["group"] in {"Sports", "Entertainment", "Kids"}:
        score += 40
    if name.startswith("UK:") and wanted["group"] in {"BBC", "ITV", "Channel 4 & 5", "News", "Documentary", "Music"}:
        score += 35
    if "HEVC" in name or "ʰᵉᵛᶜ" in name:
        score -= 40
    if "SD" in name:
        score -= 70
    if "+1" in name or " PLUS 1" in name:
        score -= 120

    return score


def find_stream_fuzzy(wanted, streams):
    scored = []
    for item in streams:
        score = stream_match_score(wanted, item)
        if score >= MIN_STREAM_MATCH_SCORE:
            temp = dict(item)
            temp["_group"] = wanted["group"]
            # Use your original quality_score as the main tie-breaker among valid fuzzy candidates.
            scored.append((score, quality_score(temp), item))

    scored.sort(key=lambda row: (row[0], row[1]), reverse=True)

    if not scored:
        return None, []

    best_match_score, best_quality_score, best_item = scored[0]
    second_score = scored[1][0] if len(scored) > 1 else -9999

    if best_match_score < HIGH_CONFIDENCE_SCORE and best_match_score - second_score < MIN_SCORE_GAP:
        return None, scored[:5]

    output = dict(best_item)
    output["_group"] = wanted["group"]
    output["_canonical"] = wanted["key"]
    output["_wanted_name"] = wanted["name"]
    output["_variants"] = len(scored)
    output["_score"] = best_quality_score
    output["_stream_match_method"] = "fuzzy_name_fallback"
    output["_stream_match_score"] = best_match_score
    return output, scored[:5]


def vip_main_event_score(item):
    name = upper_name(item)
    score = quality_score(item)

    if name.startswith("VIP:"):
        score += 1000

    if "SKY SPORTS MAIN" in name or "MAIN EVENT" in name:
        score += 500

    if "4K" in name or "UHD" in name or "3840" in name or "³⁸⁴⁰" in name:
        score += 300

    if "SD" in name:
        score -= 500

    return score


def find_extra_vip_main_event(streams):
    candidates = []

    for item in streams:
        epg = get_provider_epg(item)
        name = upper_name(item)

        # First preference: exact old provider EPG ID, as before.
        exact_old_match = epg == "skysportsmainevent.uk"

        # Fallback: name-based if provider EPG ID changed.
        fuzzy_name_match = (
            "MAIN EVENT" in name
            and ("SKY" in name or "SKY SPORTS" in name)
        )

        if not exact_old_match and not fuzzy_name_match:
            continue

        if not name.startswith("VIP:"):
            continue

        if is_adult(item):
            continue

        candidates.append(item)

    if not candidates:
        return None

    best_item = max(candidates, key=vip_main_event_score)

    output = dict(best_item)
    output["_group"] = "Sports"
    output["_canonical"] = "sky_sports_main_event_4k"
    output["_wanted_name"] = "4K Sky Sports Main Event"
    output["_variants"] = len(candidates)
    output["_score"] = vip_main_event_score(best_item)
    output["_display_name"] = "4K Sky Sports Main Event"
    output["_stream_match_method"] = "vip_main_event_extra"
    output["_stream_match_score"] = "special"

    return output


def find_first_mutv(streams):
    """
    Take the first MUTV-looking item in the full JSON, even if it has no EPG ID.
    This uses the true JSON order, not num, stream_id, or quality score.
    """
    for item in streams:
        name = upper_name(item)

        if "MUTV" not in name:
            continue

        if is_adult(item):
            continue

        output = dict(item)
        output["_group"] = "Sports"
        output["_canonical"] = "mutv"
        output["_wanted_name"] = "MUTV"
        output["_variants"] = 1
        output["_score"] = 0
        output["_display_name"] = "MUTV"
        output["_forced_epg_id"] = "mutv.uk"
        output["_stream_match_method"] = "first_mutv_special"
        output["_stream_match_score"] = "special"

        return output

    return None


# =========================
# EPGSHARE XMLTV MATCHING + FILTERING
# =========================

def open_xml_or_gz(path):
    path = Path(path)
    if path.suffix.lower() == ".gz":
        return gzip.open(path, "rb")
    return path.open("rb")


def load_epg_root(path):
    with open_xml_or_gz(path) as f:
        return ET.parse(f).getroot()


def get_epg_channels(root):
    channels = []
    for channel in root.findall("channel"):
        channel_id = clean(channel.get("id"))
        names = [clean(node.text) for node in channel.findall("display-name") if clean(node.text)]
        if channel_id:
            channels.append({"id": channel_id, "names": names})
    return channels


def epg_candidate_text(channel):
    return " ".join([channel["id"]] + channel.get("names", []))


def epg_match_score(wanted, channel):
    search = epg_candidate_text(channel)
    aliases = [wanted["name"]] + wanted.get("aliases", []) + wanted.get("epg_aliases", [])
    score = alias_match_score(search, aliases)
    score = apply_reject_penalties(score, search, wanted.get("reject", []), penalty=1400)

    raw = search.lower()
    if "hd" in raw or "uhd" in raw:
        score += 8

    return score


def find_epg_match(wanted, epg_channels):
    override = EPG_ID_OVERRIDES.get(wanted["key"])
    if override:
        for channel in epg_channels:
            if channel["id"] == override:
                return channel, HIGH_CONFIDENCE_SCORE, [(HIGH_CONFIDENCE_SCORE, channel)]

    scored = []
    for channel in epg_channels:
        score = epg_match_score(wanted, channel)
        scored.append((score, channel))

    scored.sort(key=lambda row: row[0], reverse=True)
    best_score, best_channel = scored[0]
    second_score = scored[1][0] if len(scored) > 1 else -9999

    if best_score < MIN_EPG_MATCH_SCORE:
        return None, best_score, scored[:5]

    if best_score < HIGH_CONFIDENCE_SCORE and best_score - second_score < MIN_SCORE_GAP:
        return None, best_score, scored[:5]

    return best_channel, best_score, scored[:5]


def attach_epg_matches(channels, epg_channels, dropped):
    matched = []

    for item in channels:
        canonical = item.get("_canonical")

        # The extra 4K Main Event uses the same guide data as normal Main Event.
        lookup_key = "sky_sports_main_event" if canonical == "sky_sports_main_event_4k" else canonical
        wanted = CHANNEL_BY_KEY.get(lookup_key)

        if not wanted:
            dropped.append({
                "name": display_name(item),
                "reason": f"No wanted-channel metadata for {canonical}",
                "stream_item": item,
            })
            continue

        epg_channel, epg_score, epg_alternatives = find_epg_match(wanted, epg_channels)

        if not epg_channel and REQUIRE_EPG_MATCH:
            dropped.append({
                "name": wanted["name"],
                "reason": f"No confident EPGShare match. Best score={epg_score}",
                "stream_item": item,
                "epg_alternatives": epg_alternatives,
            })
            continue

        output = dict(item)
        if epg_channel:
            output["_xmltv_id"] = epg_channel["id"]
            output["_xmltv_display_names"] = epg_channel.get("names", [])
            output["_epg_match_score"] = epg_score
        else:
            output["_xmltv_id"] = get_provider_epg(item)
            output["_xmltv_display_names"] = []
            output["_epg_match_score"] = "none"

        matched.append(output)

    return matched


def write_filtered_epg(root, selected_xmltv_ids):
    selected_xmltv_ids = set(selected_xmltv_ids)

    new_root = ET.Element(root.tag, root.attrib)

    channel_count = 0
    programme_count = 0

    for channel in root.findall("channel"):
        if channel.get("id") in selected_xmltv_ids:
            new_root.append(channel)
            channel_count += 1

    for programme in root.findall("programme"):
        if programme.get("channel") in selected_xmltv_ids:
            new_root.append(programme)
            programme_count += 1

    try:
        ET.indent(new_root, space="  ")
    except AttributeError:
        pass

    tree = ET.ElementTree(new_root)
    tree.write(OUTPUT_EPG_FILE, encoding="utf-8", xml_declaration=True)
    return channel_count, programme_count


# =========================
# DOWNLOAD HELPERS
# =========================

def download_file(url, output_path, description="file"):
    output_path = Path(output_path)
    curl = shutil.which("curl.exe") or shutil.which("curl")

    if output_path.exists():
        output_path.unlink()

    if curl:
        command = [curl, "-L", "--fail", "--compressed", url, "-o", str(output_path)]
        printable_command = [redact_url(part) if part == url else part for part in command]
        print("Running:", " ".join(printable_command))
        result = subprocess.run(command, capture_output=True, text=True)
        if result.returncode != 0:
            details = (result.stderr or result.stdout or "").strip()
            raise GeneratorError(
                f"Could not download {description}. Check the server URL and connection.\n{details}"
            )
    else:
        print(f"curl not found. Downloading with Python: {redact_url(url)}")
        request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                output_path.write_bytes(response.read())
        except Exception as error:
            raise GeneratorError(
                f"Could not download {description}. Check the URL and connection.\n{error}"
            ) from error

    if not output_path.exists() or output_path.stat().st_size == 0:
        raise GeneratorError(
            f"Could not download {description}. The server returned an empty file: {output_path}"
        )


def maybe_download_live_streams():
    if DOWNLOAD_LIVE_STREAMS:
        validate_login_config()
        print("Downloading live_streams.json...")
        download_file(build_live_streams_url(), INPUT_JSON, description="live streams JSON")


def maybe_download_epg():
    if DOWNLOAD_EPG:
        print("Downloading EPGShare XMLTV file...")
        download_file(EPG_URL, EPG_GZ_FILE, description="EPGShare XMLTV file")


# =========================
# CHANNEL SELECTION
# =========================

def build_exact_provider_buckets(streams):
    buckets = {}
    for item in streams:
        if is_adult(item):
            continue
        epg = get_provider_epg(item)
        if not epg or epg == "ts":
            continue
        buckets.setdefault(epg, []).append(item)
    return buckets


def choose_channels(streams):
    exact_buckets = build_exact_provider_buckets(streams)
    chosen = []
    dropped = []

    for wanted in WANTED_CHANNELS:
        # MUTV stays as your special case because the provider sometimes gives it no EPG ID.
        if wanted["key"] == "mutv":
            continue

        exact_options = []
        for epg_id in wanted.get("provider_epg_ids", []):
            exact_options.extend(exact_buckets.get(epg_id.lower(), []))

        if exact_options:
            chosen.append(choose_from_exact_provider_epg(wanted, exact_options))
            continue

        # Fallback only if the exact old EPG-ID route cannot find this channel.
        fuzzy_item, alternatives = find_stream_fuzzy(wanted, streams)
        if fuzzy_item:
            chosen.append(fuzzy_item)
        else:
            dropped.append({
                "name": wanted["name"],
                "reason": "No exact provider EPG ID and no confident fuzzy stream match",
                "stream_alternatives": alternatives,
            })

    # Add one extra VIP/4K Sky Sports Main Event as a separate channel.
    extra_vip_main_event = find_extra_vip_main_event(streams)
    if extra_vip_main_event:
        chosen.append(extra_vip_main_event)

    # Add MUTV from the first MUTV-looking entry in the full JSON.
    first_mutv = find_first_mutv(streams)
    if first_mutv:
        chosen.append(first_mutv)
    else:
        dropped.append({"name": "MUTV", "reason": "No MUTV-looking stream found"})

    group_order = {
        "Sports": 1,
        "BBC": 2,
        "ITV": 3,
        "Channel 4 & 5": 4,
        "Entertainment": 5,
        "News": 6,
        "Documentary": 7,
        "Music": 8,
        "Kids": 9,
    }

    chosen.sort(
        key=lambda item: (
            group_order.get(item.get("_group"), 99),
            sort_name(item),
        )
    )

    return chosen, dropped


# =========================
# OUTPUT WRITERS
# =========================

def write_m3u(channels):
    lines = [f'#EXTM3U x-tvg-url="{OUTPUT_EPG_FILE}"']

    for item in channels:
        name = display_name(item)
        stream_id = get_stream_id(item)
        logo = get_logo(item)
        epg_id = get_output_epg(item)
        group = clean(item.get("_group"))

        stream_url = f"{SERVER}/live/{USERNAME}/{PASSWORD}/{stream_id}.{OUTPUT_FORMAT}"

        lines.append(
            f'#EXTINF:-1 '
            f'tvg-id="{epg_id}" '
            f'tvg-name="{name}" '
            f'tvg-logo="{logo}" '
            f'group-title="{group}",'
            f'{name}'
        )
        lines.append(stream_url)

    Path(OUTPUT_FILE).write_text("\n".join(lines) + "\n", encoding="utf-8")


def describe_stream_alternatives(alternatives):
    lines = []
    for row in alternatives[:5]:
        if len(row) == 3:
            match_score, quality, item = row
            lines.append(
                f"    candidate stream_score={match_score} quality={quality} | "
                f"name={get_stream_name(item)} | epg={get_provider_epg(item)} | stream_id={get_stream_id(item)}"
            )
    return lines


def describe_epg_alternatives(alternatives):
    lines = []
    for score, channel in alternatives[:5]:
        lines.append(
            f"    candidate epg_score={score} | id={channel.get('id')} | names={', '.join(channel.get('names', [])[:3])}"
        )
    return lines


def write_report(channels, dropped, filtered_epg_stats=None):
    lines = [
        f"Total selected channels: {len(channels)}",
        f"Filtered EPG channels/programmes: {filtered_epg_stats or 'not written'}",
        "",
    ]

    current_group = None

    for item in channels:
        group = item.get("_group")

        if group != current_group:
            current_group = group
            lines.append("")
            lines.append(f"===== {group} =====")

        xmltv_names = ", ".join(item.get("_xmltv_display_names", [])[:3])
        lines.append(
            f'{display_name(item)} | '
            f'original={get_stream_name(item)} | '
            f'stream_id={get_stream_id(item)} | '
            f'provider_epg={get_provider_epg(item)} | '
            f'xmltv_id={get_output_epg(item)} | '
            f'xmltv_names={xmltv_names} | '
            f'variants={item.get("_variants")} | '
            f'quality_score={item.get("_score")} | '
            f'stream_method={item.get("_stream_match_method")} | '
            f'stream_match_score={item.get("_stream_match_score")} | '
            f'epg_match_score={item.get("_epg_match_score")}'
        )

    lines.append("")
    lines.append("===== DROPPED / NEEDS REVIEW =====")

    if not dropped:
        lines.append("No dropped channels.")
    else:
        for item in dropped:
            lines.append("")
            lines.append(f"{item.get('name')} | {item.get('reason')}")
            lines.extend(describe_stream_alternatives(item.get("stream_alternatives", [])))
            lines.extend(describe_epg_alternatives(item.get("epg_alternatives", [])))

    Path(REPORT_FILE).write_text("\n".join(lines) + "\n", encoding="utf-8")


# =========================
# MAIN
# =========================


def update_iptv_simple_paths():
    """
    Edit all existing IPTV Simple instance-settings-*.xml files and only change
    the M3U/EPG path entries. This avoids the issue where Kodi may be using
    instance-settings-5.xml while we only edited instance-settings-1.xml.
    """
    settings_dir = Path(IPTV_SIMPLE_SETTINGS_DIR)

    if not settings_dir.exists():
        raise GeneratorError(
            "IPTV Simple settings folder was not found. Open IPTV Simple once first, then run Generate IPTV again.[CR][CR]"
            "Missing:[CR]%s" % str(settings_dir)
        )

    settings_files = sorted(settings_dir.glob("instance-settings-*.xml"))

    if not settings_files:
        raise GeneratorError(
            "No IPTV Simple instance settings files were found. Open IPTV Simple once first, then run Generate IPTV again.[CR][CR]"
            "Folder:[CR]%s" % str(settings_dir)
        )

    updated_files = []

    for settings_file in settings_files:
        tree = ET.parse(str(settings_file))
        root = tree.getroot()

        def set_setting(setting_id, value=None):
            node = root.find("./setting[@id='%s']" % setting_id)
            if node is None:
                node = ET.SubElement(root, "setting")
                node.set("id", setting_id)

            if value is None:
                node.text = None
            else:
                node.text = str(value)

            if "default" in node.attrib:
                del node.attrib["default"]

        # Make sure this instance is enabled.
        set_setting("kodi_addon_instance_enabled", "true")

        # 0 = local path.
        set_setting("m3uPathType", "0")
        set_setting("m3uPath", IPTV_SIMPLE_M3U_SPECIAL)
        set_setting("m3uUrl", None)

        set_setting("epgPathType", "0")
        set_setting("epgPath", IPTV_SIMPLE_EPG_SPECIAL)
        set_setting("epgUrl", None)

        # Disable IPTV Simple's internal cache for this generated-file workflow.
        # This makes it more likely to re-read the newly generated files.
        set_setting("m3uCache", "false")
        set_setting("epgCache", "false")

        try:
            ET.indent(tree, space="    ")
        except Exception:
            pass

        tree.write(str(settings_file), encoding="utf-8", xml_declaration=False)
        updated_files.append(str(settings_file))

    return "[CR]".join(updated_files)

def _jsonrpc(method, params=None):
    if xbmc is None:
        raise GeneratorError("Kodi xbmc module unavailable.")

    request = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": method
    }

    if params is not None:
        request["params"] = params

    response = xbmc.executeJSONRPC(json.dumps(request))
    data = json.loads(response or "{}")

    if data.get("error"):
        raise GeneratorError("%s failed: %s" % (method, data.get("error")))

    return data.get("result")


def reload_pvr_manager():
    """
    Hard-restart IPTV Simple and Kodi's PVR manager so IPTV Simple rereads
    its updated instance settings and loads the generated M3U/EPG files.
    """
    if xbmc is None:
        return {
            "success": False,
            "message": "Kodi xbmc module unavailable, PVR not reloaded."
        }

    monitor = xbmc.Monitor()

    try:
        xbmc.executebuiltin("StopPVRManager")
        monitor.waitForAbort(2)

        _jsonrpc("Addons.SetAddonEnabled", {
            "addonid": "pvr.iptvsimple",
            "enabled": False
        })
        monitor.waitForAbort(2)

        _jsonrpc("Addons.SetAddonEnabled", {
            "addonid": "pvr.iptvsimple",
            "enabled": True
        })
        monitor.waitForAbort(3)

        xbmc.executebuiltin("StartPVRManager")
        monitor.waitForAbort(2)

        return {
            "success": True,
            "message": "IPTV Simple restarted and PVR manager restarted."
        }

    except Exception as exc:
        try:
            xbmc.executebuiltin("StartPVRManager")
        except Exception:
            pass

        return {
            "success": False,
            "message": "IPTV Simple hard reload failed: %s. Restart Kodi if Live TV does not appear." % str(exc)
        }

def run_generator():
    maybe_download_live_streams()

    input_path = Path(INPUT_JSON)
    if not input_path.exists():
        raise GeneratorError(
            f"Cannot find {INPUT_JSON}. Download failed, or put live_streams.json in the same folder as this script."
        )

    print("Loading and validating live streams JSON...")
    streams = load_and_validate_live_streams(input_path)

    # Only download/load the EPG after the provider login has been validated.
    maybe_download_epg()

    epg_path = Path(EPG_GZ_FILE)
    if not epg_path.exists():
        raise GeneratorError(
            f"Cannot find {EPG_GZ_FILE}. Download failed, or put the EPGShare .xml.gz file in the same folder as this script."
        )

    print("Loading EPGShare XMLTV...")
    try:
        epg_root = load_epg_root(epg_path)
    except Exception as error:
        raise GeneratorError(
            f"Could not read the EPGShare XMLTV file. Try deleting {EPG_GZ_FILE} and running again.\n{error}"
        ) from error

    epg_channels = get_epg_channels(epg_root)
    if not epg_channels:
        raise GeneratorError("The EPGShare file loaded, but no XMLTV channels were found.")
    print(f"EPGShare channels found: {len(epg_channels)}")

    print("Filtering and choosing streams...")
    channels, dropped = choose_channels(streams)

    print("Matching selected channels to EPGShare IDs...")
    channels = attach_epg_matches(channels, epg_channels, dropped)

    if not channels:
        write_report(channels, dropped, filtered_epg_stats="not written")
        raise GeneratorError(
            "No channels could be generated. Check IPTV-Report.txt for the dropped/review list."
        )

    print("Writing filtered EPG...")
    selected_xmltv_ids = [get_output_epg(item) for item in channels if get_output_epg(item)]
    epg_channel_count, epg_programme_count = write_filtered_epg(epg_root, selected_xmltv_ids)
    filtered_epg_stats = f"{epg_channel_count} channels / {epg_programme_count} programmes"

    print("Writing M3U...")
    write_m3u(channels)

    print("Writing report...")
    write_report(channels, dropped, filtered_epg_stats)
    iptv_simple_settings = update_iptv_simple_paths()
    pvr_reload = reload_pvr_manager()


    print("")
    print(f"Done. Created: {OUTPUT_FILE}")
    print(f"Filtered EPG: {OUTPUT_EPG_FILE}")
    print(f"Report: {REPORT_FILE}")
    print(f"Selected channels: {len(channels)}")
    print(f"Filtered EPG: {filtered_epg_stats}")
    if dropped:
        print(f"Review needed for dropped/uncertain items: {len(dropped)}")

    return {
        "success": True,
        "playlist": str(Path(OUTPUT_FILE)),
        "epg": str(Path(OUTPUT_EPG_FILE)),
        "report": str(Path(REPORT_FILE)),
        "channels": len(channels),
        "dropped": len(dropped),
        "filtered_epg": filtered_epg_stats,
        "iptv_simple_settings": iptv_simple_settings,
        "pvr_reload": pvr_reload,
    }

def generate(server, username, password):
    global SERVER, USERNAME, PASSWORD

    IPTV_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    IPTV_CACHE_DIR.mkdir(parents=True, exist_ok=True)

    SERVER = clean(server).rstrip("/")
    USERNAME = clean(username)
    PASSWORD = clean(password)

    try:
        return run_generator()
    except GeneratorError as error:
        message = str(error).strip()
        try:
            write_failure_report(message)
        except Exception:
            pass

        return {
            "success": False,
            "error": message,
            "playlist": str(Path(OUTPUT_FILE)),
            "epg": str(Path(OUTPUT_EPG_FILE)),
            "report": str(Path(REPORT_FILE)),
        }


def main():
    try:
        run_generator()
    except GeneratorError as error:
        message = str(error).strip()
        print("")
        print("Could not generate IPTV files.")
        print(message)
        try:
            write_failure_report(message)
            print(f"Failure report written to: {REPORT_FILE}")
        except Exception:
            pass
        sys.exit(1)



# ============================================================================
# FLAM LIVE TV CATALOGUE MODE
# This section deliberately overrides run_generator() from the original one-link
# generator.  The old helper functions above are still used for matching,
# downloading, EPG filtering, IPTV Simple setup, and PVR reload.
# ============================================================================

CATALOG_FILE = str(IPTV_OUTPUT_DIR / "IPTV-Catalog.json")
MAX_VARIANTS_PER_CHANNEL = 25
PLUGIN_CHANNEL_URL = "plugin://plugin.video.fenlight/?mode=accounts.iptv_play_channel&channel={channel_key}"

# Curated no-UK-EPG extras. These appear in Manage Channels, disabled by default.
# Keep this list intentionally small so G2G's huge catalogue does not flood the UI.
EXTRA_CHANNELS = [
    {"key": "dazn_1", "name": "DAZN 1", "group": "Non-UK Extras", "aliases": ["dazn 1", "dazn one"], "reject": ["de", "germany", "es", "spain", "it", "italy", "bar", "backup", "test"]},
    {"key": "dazn_2", "name": "DAZN 2", "group": "Non-UK Extras", "aliases": ["dazn 2", "dazn two"], "reject": ["de", "germany", "es", "spain", "it", "italy", "bar", "backup", "test"]},
    {"key": "dazn_3", "name": "DAZN 3", "group": "Non-UK Extras", "aliases": ["dazn 3", "dazn three"], "reject": ["de", "germany", "es", "spain", "it", "italy", "bar", "backup", "test"]},
    {"key": "dazn_4", "name": "DAZN 4", "group": "Non-UK Extras", "aliases": ["dazn 4", "dazn four"], "reject": ["de", "germany", "es", "spain", "it", "italy", "bar", "backup", "test"]},
    {"key": "nfl_network", "name": "NFL Network", "group": "Non-UK Extras", "aliases": ["nfl network", "nfl net"], "reject": ["nba", "nhl", "redzone", "red zone", "replay", "backup", "test"]},
    {"key": "nfl_redzone", "name": "NFL RedZone", "group": "Non-UK Extras", "aliases": ["nfl redzone", "nfl red zone"], "reject": ["network", "nba", "nhl", "replay", "backup", "test"]},
    {"key": "nba_tv", "name": "NBA TV", "group": "Non-UK Extras", "aliases": ["nba tv", "nba television"], "reject": ["nfl", "nhl", "replay", "backup", "test"]},
    {"key": "nhl_network", "name": "NHL Network", "group": "Non-UK Extras", "aliases": ["nhl network", "nhl tv"], "reject": ["nfl", "nba", "replay", "backup", "test"]},
    {"key": "mlb_network", "name": "MLB Network", "group": "Non-UK Extras", "aliases": ["mlb network", "mlb tv"], "reject": ["nfl", "nba", "nhl", "replay", "backup", "test"]},
    {"key": "espn", "name": "ESPN", "group": "Non-UK Extras", "aliases": ["espn"], "reject": ["espn 2", "espn2", "deportes", "college extra", "backup", "test"]},
    {"key": "espn_2", "name": "ESPN 2", "group": "Non-UK Extras", "aliases": ["espn 2", "espn2"], "reject": ["deportes", "college extra", "backup", "test"]},
    {"key": "ufc_fight_pass", "name": "UFC Fight Pass", "group": "Non-UK Extras", "aliases": ["ufc fight pass", "ufc tv"], "reject": ["replay", "backup", "test"]},
    {"key": "wwe_network", "name": "WWE Network", "group": "Non-UK Extras", "aliases": ["wwe network"], "reject": ["raw", "smackdown", "replay", "backup", "test"]},
]


# Preserve the known better EPGShare target for Sky Sports Football when both
# abbreviated and full-name XMLTV channels exist.
EPG_ID_OVERRIDES.update({
    "sky_sports_football": "Sky.Sports.Football.HD.uk",
})

def _catalog_path():
    return Path(CATALOG_FILE)


def _load_existing_enabled_states():
    path = _catalog_path()
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
        channels = data.get("channels", []) if isinstance(data, dict) else data
        return {str(item.get("key")): bool(item.get("enabled")) for item in channels if item.get("key")}
    except Exception:
        return {}


def load_catalog():
    path = _catalog_path()
    if not path.exists():
        raise GeneratorError("No IPTV channel catalogue found. Run Generate / Refresh Live TV first.")
    try:
        data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except Exception as error:
        raise GeneratorError("Could not read IPTV channel catalogue. Run Generate / Refresh Live TV again.\n%s" % str(error))
    if not isinstance(data, dict) or not isinstance(data.get("channels"), list):
        raise GeneratorError("IPTV channel catalogue is invalid. Run Generate / Refresh Live TV again.")
    return data


def save_catalog(catalog):
    _catalog_path().parent.mkdir(parents=True, exist_ok=True)
    _catalog_path().write_text(json.dumps(catalog, indent=2, sort_keys=False), encoding="utf-8")


def update_catalog_enabled_states(enabled_keys):
    catalog = load_catalog()
    enabled_keys = set(str(item) for item in enabled_keys)
    for item in catalog.get("channels", []):
        item["enabled"] = item.get("key") in enabled_keys
    save_catalog(catalog)
    return rebuild_from_catalog(reload_pvr=True)


def _channel_default_enabled(group_name, xmltv_id):
    # Normal UK EPG-matched channels are enabled by default.
    # Kids and Non-UK Extras stay available but disabled by default.
    if group_name in {"Kids", "Non-UK Extras"}:
        return False
    return bool(xmltv_id)


def _clean_category(item):
    return clean(get_field(item, "category_name", "category", "group"))


def _variant_quality_label(item):
    name = upper_name(item)
    bits = []
    if "4K" in name or "UHD" in name or "3840" in name or "³⁸⁴⁰" in name:
        bits.append("4K/UHD")
    elif "HD" in name or "ᴴᴰ" in name or "RAW" in name or "ᴿᴬᵂ" in name:
        bits.append("HD/RAW")
    elif "SD" in name:
        bits.append("SD")

    if name.startswith("VIP:"):
        bits.append("VIP")
    elif name.startswith("NOW:"):
        bits.append("NOW")
    elif name.startswith("UK:"):
        bits.append("UK")
    elif name.startswith("NZ:"):
        bits.append("NZ")

    return " / ".join(bits) if bits else "Standard"


def _safe_kodi_display_text(value):
    """Make provider stream labels Kodi-font friendly.

    Some provider names use Unicode modifier/superscript glyphs like ᴴᴰ,
    ᴿᴬᵂ, ᵁᴴᴰ, ⁴ᴷ and ³⁸⁴⁰ᴾ.  Kodi's dialog font often renders
    parts of those as square boxes, so only the UI label is normalised.
    The original provider name stays in the catalogue/report.
    """
    text = clean(value)
    if not text:
        return ""

    replacements = {
        "ᴴᴰ": "HD", "ʜᴅ": "HD", "ᴿᴬᵂ": "RAW", "ᵁᴴᴰ": "UHD",
        "⁴ᴷ": "4K", "³⁸⁴⁰ᴾ": "3840P", "²¹⁶⁰ᴾ": "2160P", "¹⁰⁸⁰ᴾ": "1080P",
        "ᴾ": "P", "ᴷ": "K", "ᴴ": "H", "ᴰ": "D", "ᴿ": "R", "ᴬ": "A", "ᵂ": "W", "ᵁ": "U",
        "⁰": "0", "¹": "1", "²": "2", "³": "3", "⁴": "4",
        "⁵": "5", "⁶": "6", "⁷": "7", "⁸": "8", "⁹": "9",
        "₀": "0", "₁": "1", "₂": "2", "₃": "3", "₄": "4",
        "₅": "5", "₆": "6", "₇": "7", "₈": "8", "₉": "9",
        "◉": "", "●": "", "•": "-",
    }
    for old, new in replacements.items():
        text = text.replace(old, new)

    # Strip other high Unicode symbols that Kodi may display as tofu boxes,
    # while keeping normal ASCII punctuation used by provider names.
    try:
        import unicodedata
        text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    except Exception:
        pass

    text = re.sub(r"\s+", " ", text).strip()
    text = re.sub(r"\s+([:;,.])", r"\1", text)
    return text


def _safe_stream_label(stream):
    name = _safe_kodi_display_text(stream.get("name") or "Unknown")
    return name or "Unknown"


def _variant_priority(item, wanted=None, match_score=0):
    temp = dict(item)
    if wanted:
        temp["_group"] = wanted.get("group", "")
    score = quality_score(temp)
    name = upper_name(item)

    # Prefer clean UK/NOW/VIP looking entries over random country packs.
    if name.startswith("NOW:"):
        score += 80
    if name.startswith("UK:"):
        score += 65
    if name.startswith("VIP:"):
        score += 45
    if name.startswith("NZ:"):
        score -= 60
    if "BACKUP" in name or "TEST" in name:
        score -= 100

    try:
        score += int(match_score) // 10
    except Exception:
        pass
    return score


def _stream_to_variant(item, wanted=None, method="exact_provider_epg", match_score="exact"):
    return {
        "name": get_stream_name(item),
        "stream_id": str(get_stream_id(item)),
        "provider_epg": get_provider_epg(item),
        "logo": get_logo(item),
        "category": _clean_category(item),
        "quality": _variant_quality_label(item),
        "quality_score": quality_score(dict(item, _group=(wanted or {}).get("group", ""))),
        "priority_score": _variant_priority(item, wanted, 0 if match_score == "exact" else match_score),
        "match_method": method,
        "match_score": match_score,
        "output_format": OUTPUT_FORMAT,
    }


def _unique_sorted_variants(variants):
    unique = {}
    for variant in variants:
        stream_id = str(variant.get("stream_id") or "")
        if not stream_id:
            continue
        old = unique.get(stream_id)
        if old is None or int(variant.get("priority_score", 0)) > int(old.get("priority_score", 0)):
            unique[stream_id] = variant
    result = list(unique.values())
    result.sort(key=lambda item: int(item.get("priority_score", 0)), reverse=True)
    return result[:MAX_VARIANTS_PER_CHANNEL]


def _make_channel_record(key, name, section, variants, xmltv_id="", xmltv_names=None, epg_score="", enabled=None, status="matched", previous_states=None):
    previous_states = previous_states or {}
    if enabled is None:
        if key in previous_states:
            enabled = bool(previous_states[key])
        else:
            enabled = _channel_default_enabled(section, xmltv_id)

    first_logo = ""
    for variant in variants:
        if variant.get("logo"):
            first_logo = variant.get("logo")
            break

    return {
        "key": key,
        "name": name,
        "section": section,
        "enabled": bool(enabled),
        "xmltv_id": xmltv_id or "",
        "xmltv_names": xmltv_names or [],
        "epg_match_score": epg_score,
        "epg_status": status,
        "logo": first_logo,
        "stream_count": len(variants),
        "streams": variants,
    }


def _epg_for_wanted(wanted, epg_channels):
    epg_channel, epg_score, epg_alternatives = find_epg_match(wanted, epg_channels)
    if epg_channel:
        return epg_channel.get("id", ""), epg_channel.get("names", []), epg_score, "matched", epg_alternatives
    return "", [], epg_score, "no_match", epg_alternatives


def _build_mutv_group(wanted, streams, epg_channels, previous_states):
    variants = []
    seen = set()
    for item in streams:
        if is_adult(item) or not get_stream_id(item):
            continue
        search = " ".join([get_stream_name(item), get_provider_epg(item), _clean_category(item)])
        if "mutv" not in compact_text(search) and "manchesterunited" not in compact_text(search):
            continue
        variant = _stream_to_variant(item, wanted, method="mutv_group", match_score="special")
        # Avoid G2G's random NZ entry beating better VIP/UK/NOW entries.
        name = upper_name(item)
        if name.startswith("VIP:"):
            variant["priority_score"] += 300
        if name.startswith("UK:"):
            variant["priority_score"] += 220
        if name.startswith("NOW:"):
            variant["priority_score"] += 180
        if get_provider_epg(item) == "mutv.uk":
            variant["priority_score"] += 220
        if name.startswith("NZ:"):
            variant["priority_score"] -= 120
        if variant["stream_id"] not in seen:
            variants.append(variant)
            seen.add(variant["stream_id"])

    variants = _unique_sorted_variants(variants)
    if not variants:
        return None, {"name": wanted["name"], "reason": "No MUTV-looking stream found"}

    xmltv_id, xmltv_names, epg_score, epg_status, epg_alternatives = _epg_for_wanted(wanted, epg_channels)
    return _make_channel_record(
        wanted["key"], wanted["name"], wanted["group"], variants,
        xmltv_id=xmltv_id, xmltv_names=xmltv_names, epg_score=epg_score,
        status=epg_status, previous_states=previous_states
    ), None


def _build_wanted_group(wanted, streams, exact_buckets, epg_channels, previous_states):
    if wanted.get("key") == "mutv":
        return _build_mutv_group(wanted, streams, epg_channels, previous_states)

    variants = []
    exact_options = []
    for epg_id in wanted.get("provider_epg_ids", []):
        exact_options.extend(exact_buckets.get(epg_id.lower(), []))

    if exact_options:
        for item in exact_options:
            if is_adult(item) or not get_stream_id(item):
                continue
            variants.append(_stream_to_variant(item, wanted, method="exact_provider_epg", match_score="exact"))
    else:
        scored = []
        for item in streams:
            score = stream_match_score(wanted, item)
            if score >= MIN_STREAM_MATCH_SCORE:
                temp = dict(item)
                temp["_group"] = wanted["group"]
                scored.append((score, quality_score(temp), item))
        scored.sort(key=lambda row: (row[0], row[1]), reverse=True)
        for score, _quality, item in scored[:MAX_VARIANTS_PER_CHANNEL]:
            variants.append(_stream_to_variant(item, wanted, method="fuzzy_name_fallback", match_score=score))

    variants = _unique_sorted_variants(variants)
    if not variants:
        return None, {
            "name": wanted["name"],
            "reason": "No exact provider EPG ID and no confident fuzzy stream match",
            "stream_alternatives": [],
        }

    xmltv_id, xmltv_names, epg_score, epg_status, epg_alternatives = _epg_for_wanted(wanted, epg_channels)
    return _make_channel_record(
        wanted["key"], wanted["name"], wanted["group"], variants,
        xmltv_id=xmltv_id, xmltv_names=xmltv_names, epg_score=epg_score,
        status=epg_status, previous_states=previous_states
    ), None


def _extra_match_score(extra, item):
    if is_adult(item) or not get_stream_id(item):
        return -9999
    search = stream_search_text(item)
    aliases = [extra["name"]] + extra.get("aliases", [])
    score = alias_match_score(search, aliases)
    score = apply_reject_penalties(score, search, extra.get("reject", []), penalty=500)
    name = upper_name(item)
    if "BACKUP" in name or "TEST" in name:
        score -= 150
    if name.startswith(("US:", "USA:", "CA:", "VIP:", "SPORTS:")):
        score += 30
    return score


def _build_extra_group(extra, streams, previous_states):
    scored = []
    # Cheap pre-filter first. G2G can have 55k+ streams, and running the full
    # fuzzy scorer for every curated extra would be slow.
    alias_compacts = [compact_text(alias) for alias in ([extra.get("name", "")] + extra.get("aliases", [])) if compact_text(alias)]
    for item in streams:
        if is_adult(item) or not get_stream_id(item):
            continue
        search_compact = compact_text(stream_search_text(item))
        if not any(alias and alias in search_compact for alias in alias_compacts):
            continue
        score = _extra_match_score(extra, item)
        if score >= MIN_STREAM_MATCH_SCORE:
            scored.append((score, item))
    scored.sort(key=lambda row: row[0], reverse=True)
    variants = []
    for score, item in scored[:MAX_VARIANTS_PER_CHANNEL]:
        variant = _stream_to_variant(item, extra, method="extra_allowlist", match_score=score)
        variant["priority_score"] += int(score) // 5
        variants.append(variant)
    variants = _unique_sorted_variants(variants)
    if not variants:
        return None
    return _make_channel_record(
        extra["key"], extra["name"], "Non-UK Extras", variants,
        xmltv_id="", xmltv_names=[], epg_score="none", status="no_uk_epg",
        enabled=previous_states.get(extra["key"], False) if extra["key"] in previous_states else False,
        previous_states=previous_states
    )


def build_channel_catalog(streams, epg_channels):
    previous_states = _load_existing_enabled_states()
    exact_buckets = build_exact_provider_buckets(streams)
    channels = []
    dropped = []

    for wanted in WANTED_CHANNELS:
        channel, drop = _build_wanted_group(wanted, streams, exact_buckets, epg_channels, previous_states)
        if channel:
            channels.append(channel)
        elif drop:
            dropped.append(drop)

    for extra in EXTRA_CHANNELS:
        channel = _build_extra_group(extra, streams, previous_states)
        if channel:
            channels.append(channel)

    group_order = {
        "Sports": 1,
        "BBC": 2,
        "ITV": 3,
        "Channel 4 & 5": 4,
        "Entertainment": 5,
        "News": 6,
        "Documentary": 7,
        "Music": 8,
        "Kids": 9,
        "Non-UK Extras": 99,
    }
    channels.sort(key=lambda item: (group_order.get(item.get("section"), 50), item.get("name", "").lower()))

    return {
        "version": 2,
        "mode": "grouped_plugin_resolver",
        "server": normalised_server() if clean(SERVER) else "",
        "output_format": OUTPUT_FORMAT,
        "channels": channels,
        "dropped": dropped,
    }


def _enabled_channels(catalog):
    return [item for item in catalog.get("channels", []) if item.get("enabled") and item.get("streams")]


def _channel_plugin_url(channel):
    return PLUGIN_CHANNEL_URL.format(channel_key=urllib.parse.quote_plus(channel.get("key", "")))


def write_m3u_from_catalog(catalog):
    lines = [f'#EXTM3U x-tvg-url="{OUTPUT_EPG_FILE}"']
    for channel in _enabled_channels(catalog):
        name = clean(channel.get("name"))
        epg_id = clean(channel.get("xmltv_id"))
        group = clean(channel.get("section"))
        logo = clean(channel.get("logo"))
        plugin_url = _channel_plugin_url(channel)
        lines.append(
            f'#EXTINF:-1 '
            f'tvg-id="{epg_id}" '
            f'tvg-name="{name}" '
            f'tvg-logo="{logo}" '
            f'group-title="{group}",'
            f'{name}'
        )
        lines.append(plugin_url)
    Path(OUTPUT_FILE).write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_report_from_catalog(catalog, filtered_epg_stats=None):
    channels = catalog.get("channels", [])
    enabled = _enabled_channels(catalog)
    disabled = [item for item in channels if not item.get("enabled")]
    lines = [
        "FLAM grouped Live TV catalogue report",
        "",
        "Total catalogue channels: %s" % len(channels),
        "Enabled guide channels: %s" % len(enabled),
        "Disabled catalogue channels: %s" % len(disabled),
        "Total stream variants: %s" % sum(len(item.get("streams", [])) for item in channels),
        "Filtered EPG channels/programmes: %s" % (filtered_epg_stats or "not written"),
        "",
    ]

    current_section = None
    for channel in channels:
        section = channel.get("section") or "Other"
        if section != current_section:
            current_section = section
            lines.append("")
            lines.append("===== %s =====" % section)
        status = "enabled" if channel.get("enabled") else "disabled"
        xmltv_names = ", ".join(channel.get("xmltv_names", [])[:3])
        lines.append(
            "%s | %s | streams=%s | xmltv_id=%s | xmltv_names=%s | epg_status=%s | epg_score=%s" % (
                channel.get("name"), status, len(channel.get("streams", [])),
                channel.get("xmltv_id"), xmltv_names, channel.get("epg_status"), channel.get("epg_match_score")
            )
        )
        for index, stream in enumerate(channel.get("streams", [])[:10], start=1):
            lines.append(
                "    %02d. %s | stream_id=%s | provider_epg=%s | quality=%s | priority=%s | method=%s" % (
                    index, stream.get("name"), stream.get("stream_id"), stream.get("provider_epg"),
                    stream.get("quality"), stream.get("priority_score"), stream.get("match_method")
                )
            )
        if len(channel.get("streams", [])) > 10:
            lines.append("    ... %s more variants" % (len(channel.get("streams", [])) - 10))

    lines.append("")
    lines.append("===== DROPPED / NEEDS REVIEW =====")
    dropped = catalog.get("dropped", [])
    if not dropped:
        lines.append("No dropped channels.")
    else:
        for item in dropped:
            lines.append("%s | %s" % (item.get("name"), item.get("reason")))

    Path(REPORT_FILE).write_text("\n".join(lines) + "\n", encoding="utf-8")


def _write_filtered_epg_for_catalog(catalog, epg_root):
    selected_xmltv_ids = [item.get("xmltv_id") for item in _enabled_channels(catalog) if item.get("xmltv_id")]
    if not selected_xmltv_ids:
        # Keep IPTV Simple happy with a valid empty XMLTV file.
        root = ET.Element("tv")
        ET.ElementTree(root).write(OUTPUT_EPG_FILE, encoding="utf-8", xml_declaration=True)
        return "0 channels / 0 programmes"
    channel_count, programme_count = write_filtered_epg(epg_root, selected_xmltv_ids)
    return "%s channels / %s programmes" % (channel_count, programme_count)


def rebuild_from_catalog(reload_pvr=True):
    catalog = load_catalog()
    epg_path = Path(EPG_GZ_FILE)
    if not epg_path.exists():
        raise GeneratorError("Cannot rebuild EPG because the cached EPGShare file is missing. Run Generate / Refresh Live TV again.")
    try:
        epg_root = load_epg_root(epg_path)
    except Exception as error:
        raise GeneratorError("Could not read cached EPGShare file. Run Generate / Refresh Live TV again.\n%s" % str(error))

    filtered_epg_stats = _write_filtered_epg_for_catalog(catalog, epg_root)
    write_m3u_from_catalog(catalog)
    write_report_from_catalog(catalog, filtered_epg_stats)
    iptv_simple_settings = update_iptv_simple_paths()
    pvr_reload = reload_pvr_manager() if reload_pvr else {"success": False, "message": "PVR reload skipped."}

    enabled = _enabled_channels(catalog)
    return {
        "success": True,
        "playlist": str(Path(OUTPUT_FILE)),
        "epg": str(Path(OUTPUT_EPG_FILE)),
        "report": str(Path(REPORT_FILE)),
        "catalog": str(Path(CATALOG_FILE)),
        "channels": len(enabled),
        "catalog_channels": len(catalog.get("channels", [])),
        "disabled": len(catalog.get("channels", [])) - len(enabled),
        "stream_variants": sum(len(item.get("streams", [])) for item in catalog.get("channels", [])),
        "dropped": len(catalog.get("dropped", [])),
        "filtered_epg": filtered_epg_stats,
        "iptv_simple_settings": iptv_simple_settings,
        "pvr_reload": pvr_reload,
    }


def run_generator():
    maybe_download_live_streams()

    input_path = Path(INPUT_JSON)
    if not input_path.exists():
        raise GeneratorError(
            f"Cannot find {INPUT_JSON}. Download failed, or put live_streams.json in the same folder as this script."
        )

    print("Loading and validating live streams JSON...")
    streams = load_and_validate_live_streams(input_path)

    maybe_download_epg()

    epg_path = Path(EPG_GZ_FILE)
    if not epg_path.exists():
        raise GeneratorError(
            f"Cannot find {EPG_GZ_FILE}. Download failed, or put the EPGShare .xml.gz file in the same folder as this script."
        )

    print("Loading EPGShare XMLTV...")
    try:
        epg_root = load_epg_root(epg_path)
    except Exception as error:
        raise GeneratorError(
            f"Could not read the EPGShare XMLTV file. Try deleting {EPG_GZ_FILE} and running again.\n{error}"
        ) from error

    epg_channels = get_epg_channels(epg_root)
    if not epg_channels:
        raise GeneratorError("The EPGShare file loaded, but no XMLTV channels were found.")
    print(f"EPGShare channels found: {len(epg_channels)}")

    print("Building grouped Live TV catalogue...")
    catalog = build_channel_catalog(streams, epg_channels)
    save_catalog(catalog)

    print("Writing filtered EPG and plugin M3U...")
    filtered_epg_stats = _write_filtered_epg_for_catalog(catalog, epg_root)
    write_m3u_from_catalog(catalog)

    print("Writing report...")
    write_report_from_catalog(catalog, filtered_epg_stats)

    iptv_simple_settings = update_iptv_simple_paths()
    pvr_reload = reload_pvr_manager()

    enabled = _enabled_channels(catalog)
    print("")
    print(f"Done. Created: {OUTPUT_FILE}")
    print(f"Filtered EPG: {OUTPUT_EPG_FILE}")
    print(f"Catalogue: {CATALOG_FILE}")
    print(f"Report: {REPORT_FILE}")
    print(f"Enabled channels: {len(enabled)}")
    print(f"Catalogue channels: {len(catalog.get('channels', []))}")
    print(f"Stream variants: {sum(len(item.get('streams', [])) for item in catalog.get('channels', []))}")
    print(f"Filtered EPG: {filtered_epg_stats}")

    return {
        "success": True,
        "playlist": str(Path(OUTPUT_FILE)),
        "epg": str(Path(OUTPUT_EPG_FILE)),
        "report": str(Path(REPORT_FILE)),
        "catalog": str(Path(CATALOG_FILE)),
        "channels": len(enabled),
        "catalog_channels": len(catalog.get("channels", [])),
        "disabled": len(catalog.get("channels", [])) - len(enabled),
        "stream_variants": sum(len(item.get("streams", [])) for item in catalog.get("channels", [])),
        "dropped": len(catalog.get("dropped", [])),
        "filtered_epg": filtered_epg_stats,
        "iptv_simple_settings": iptv_simple_settings,
        "pvr_reload": pvr_reload,
    }


def _settings_login_details():
    server = username = password = ""
    try:
        from caches.settings_cache import get_setting
        server = clean(get_setting("fenlight.xtream.server", "empty_setting"))
        username = clean(get_setting("fenlight.xtream.username", "empty_setting"))
        password = clean(get_setting("fenlight.xtream.password", "empty_setting"))
    except Exception:
        pass

    # Fallback for direct script testing where globals are already set.
    server = (server or SERVER).rstrip("/")
    username = username or USERNAME
    password = password or PASSWORD
    return server, username, password


def _stream_url_for_variant(variant):
    server, username, password = _settings_login_details()
    if not server or not username or not password:
        raise GeneratorError("Missing Xtream login details. Enter them in Accounts → Live TV.")
    fmt = variant.get("output_format") or OUTPUT_FORMAT
    return "%s/live/%s/%s/%s.%s" % (server.rstrip("/"), username, password, variant.get("stream_id"), fmt)


def play_channel(channel_key):
    """Resolve a plugin:// M3U item into a selected live stream URL."""
    import sys
    try:
        import xbmcgui
        import xbmcplugin
    except Exception as error:
        raise GeneratorError("Kodi playback modules unavailable: %s" % str(error))

    catalog = load_catalog()
    channel = None
    for item in catalog.get("channels", []):
        if item.get("key") == channel_key:
            channel = item
            break
    if not channel:
        raise GeneratorError("Live TV channel was not found in the catalogue. Run Generate / Refresh Live TV again.")

    streams = channel.get("streams", [])
    if not streams:
        raise GeneratorError("No stream variants found for %s." % channel.get("name", channel_key))

    if len(streams) == 1:
        chosen = streams[0]
    else:
        labels = []
        for index, stream in enumerate(streams, start=1):
            label = "%02d. %s" % (index, _safe_stream_label(stream))
            details = []
            if stream.get("quality"):
                details.append(stream.get("quality"))
            if stream.get("stream_id"):
                details.append("ID %s" % stream.get("stream_id"))
            if stream.get("provider_epg"):
                details.append(stream.get("provider_epg"))
            if details:
                label += "  [COLOR grey](%s)[/COLOR]" % " | ".join(details)
            labels.append(label)

        index = xbmcgui.Dialog().select(_safe_kodi_display_text(channel.get("name", "Live TV")), labels)
        if index < 0:
            try:
                xbmcplugin.setResolvedUrl(int(sys.argv[1]), False, xbmcgui.ListItem())
            except Exception:
                pass
            return None
        chosen = streams[index]

    url = _stream_url_for_variant(chosen)
    listitem = xbmcgui.ListItem(path=url)
    listitem.setProperty("IsPlayable", "true")
    try:
        listitem.setMimeType("video/MP2T")
    except Exception:
        pass
    xbmcplugin.setResolvedUrl(int(sys.argv[1]), True, listitem)
    return url


# ============================================================================
# FLAM LIVE TV CATALOGUE MODE v3
# Full-provider discovery layer.
#
# The old WANTED_CHANNELS list is now a known/core mapping layer only.  It is
# still used for better names, EPG aliases, reject rules, scoring and default
# enabled channels, but it is no longer the only set of channels that can appear.
#
# Catalogue rules:
#   - drop adult / VOD / movies / series / replay / 24-7 style junk
#   - build core known UK channels from WANTED_CHANNELS and enable them by default
#   - auto-discover every other provider_epg *.uk channel that can map to EPGShare
#   - keep those auto UK channels available but disabled by default
#   - keep curated non-UK extras available but disabled by default
#   - drop everything else
# ============================================================================

VOD_DROP_TERMS = [
    'vod', 'movie', 'movies', 'film collection', 'boxset', 'box set',
    'series', 'season ', 'episode ', 'catchup', 'catch up', 'replay',
    '24/7', '24-7', '247', 'on demand', 'ppv replay'
]

CORE_PROVIDER_EPG_IDS = {
    epg_id.lower()
    for channel in WANTED_CHANNELS
    for epg_id in channel.get('provider_epg_ids', [])
}

# Some *.uk IDs are technically UK entries but are usually not wanted as normal
# guide channels. They can still be added explicitly through WANTED_CHANNELS or
# EXTRA_CHANNELS if wanted later.
AUTO_UK_REJECT_TERMS = [
    'adult', 'xxx', 'babestation', 'playboy', 'redlight', 'sporty stuff tv',
    'test', 'backup', 'spare', 'offline', 'no event', 'event will start',
]


def _enabled_default_for_channel(key, section, xmltv_id, is_core=False):
    wanted = CHANNEL_BY_KEY.get(key) or {}

    if wanted.get("enabled_default") is False:
        return False

    if section in {"Kids", "Non-UK Extras", "US Extras"}:
        return False

    if is_core:
        return bool(xmltv_id)

    return False


def _is_usable_live_stream(item):
    if not isinstance(item, dict):
        return False
    if is_adult(item):
        return False
    if not get_stream_id(item):
        return False

    stream_type = clean(get_field(item, 'stream_type', 'type')).lower()
    if stream_type and stream_type not in {'live', 'live_tv', 'tv', 'channel'}:
        return False

    search = normalise_text(' '.join([
        get_stream_name(item),
        get_provider_epg(item),
        _clean_category(item),
    ]))
    compact = compact_text(search)

    for term in VOD_DROP_TERMS:
        if contains_term(search, compact, term):
            return False

    return True


def _is_auto_uk_candidate(item):
    if not _is_usable_live_stream(item):
        return False
    epg = get_provider_epg(item)
    if not epg.endswith('.uk'):
        return False
    if epg in CORE_PROVIDER_EPG_IDS:
        return False

    search = normalise_text(' '.join([get_stream_name(item), _clean_category(item), epg]))
    compact = compact_text(search)
    for term in AUTO_UK_REJECT_TERMS:
        if contains_term(search, compact, term):
            return False
    return True


def _title_keep_acronyms(value):
    words = []
    for word in clean(value).split():
        raw = word.strip()
        if not raw:
            continue
        upper = raw.upper().strip()
        if upper in {'BBC', 'ITV', 'E4', 'E4+', 'MTV', 'CNN', 'CNBC', 'GB', 'HD', 'UHD', 'SD', 'TNT', 'BT', 'NFL', 'NBA', 'NHL', 'MLB', 'UFC', 'WWE', 'DAZN'}:
            words.append(upper)
        elif re.match(r'^[A-Z0-9]{2,}$', raw):
            words.append(raw)
        else:
            words.append(raw[:1].upper() + raw[1:].lower())
    return ' '.join(words).strip()


def _strip_quality_words(value):
    text = clean(value)
    text = re.sub(r'(?i)\b(HD|FHD|UHD|SD|RAW|HEVC|H265|H\.265|4K|2160P|1080P|720P|50FPS|60FPS)\b', ' ', text)
    text = text.replace('◉', ' ')
    text = re.sub(r'\s+', ' ', text).strip(' :-')
    return text


def _auto_display_name_from_item(item):
    name = display_name(dict(item))
    name = _strip_quality_words(name)
    name = name.replace('UK::', '').replace('UK:', '').strip(' :-')
    if not name:
        epg = get_provider_epg(item).replace('.uk', '')
        name = re.sub(r'([a-z])([A-Z])', r'\1 \2', epg)
        name = re.sub(r'[^a-zA-Z0-9]+', ' ', name)
    return _title_keep_acronyms(name)


def _best_variant_for_name(variants):
    if not variants:
        return None
    return max(variants, key=lambda item: int(item.get('priority_score', 0)))


def _infer_auto_uk_section(name, item=None):
    category = _clean_category(item or {})
    search = normalise_text(' '.join([name, category, get_provider_epg(item or {})]))
    compact = compact_text(search)

    def has(*terms):
        return any(contains_term(search, compact, term) for term in terms)

    if has('cbbc', 'cbeebies', 'cartoon', 'nickelodeon', 'nick jr', 'nicktoons', 'cartoonito', 'boomerang', 'pop max', 'pop kids', 'tiny pop'):
        return 'Kids'
    if has('sky sports', 'tnt sports', 'premier sports', 'eurosport', 'racing tv', 'skysp', 'sport'):
        return 'Sports'
    if has('bbc'):
        return 'BBC'
    if has('itv'):
        return 'ITV'
    if has('channel 4', 'channel4', 'e4', 'more4', 'film4', '4seven', 'channel 5', 'channel5', '5star', '5usa', '5select', '5action'):
        return 'Channel 4 & 5'
    if has('sky news', 'bbc news', 'gb news', 'cnn', 'cnbc', 'bloomberg', 'al jazeera', 'news'):
        return 'News'
    if has('discovery', 'animal planet', 'national geographic', 'nat geo', 'history', 'documentary', 'eden', 'crime investigation'):
        return 'Documentary'
    if has('mtv', 'now 70s', 'now 80s', 'now 90s', 'music', 'kiss', 'magic', 'kerrang'):
        return 'Music'
    if has('sky cinema', 'cinema', 'film4', 'movies'):
        return 'Movies'
    return 'Other UK Channels'


def _auto_key_from_provider_epg(provider_epg, fallback_name=''):
    base = provider_epg.lower().replace('.uk', '') or fallback_name.lower()
    base = normalise_text(base)
    base = re.sub(r'[^a-z0-9]+', '_', base).strip('_')
    if not base:
        base = re.sub(r'[^a-z0-9]+', '_', compact_text(fallback_name)).strip('_')
    return 'uk_%s' % base[:80]


def _pseudo_wanted_for_auto_uk(provider_epg, display_name, variants):
    epg_base = provider_epg.replace('.uk', '')
    aliases = [display_name, epg_base, provider_epg]
    for variant in variants[:6]:
        if variant.get('name'):
            aliases.append(_strip_quality_words(variant.get('name')))
        if variant.get('provider_epg'):
            aliases.append(variant.get('provider_epg'))
    return {
        'key': _auto_key_from_provider_epg(provider_epg, display_name),
        'name': display_name,
        'group': 'Other UK Channels',
        'aliases': aliases,
        'epg_aliases': aliases,
        'reject': ['plus 1', '+1'] if 'plus 1' not in normalise_text(display_name) else [],
        'provider_epg_ids': [provider_epg],
    }


def _build_auto_uk_groups(streams, epg_channels, previous_states, used_provider_epgs):
    buckets = {}
    for item in streams:
        if not _is_auto_uk_candidate(item):
            continue
        epg = get_provider_epg(item)
        if epg in used_provider_epgs:
            continue
        buckets.setdefault(epg, []).append(item)

    channels = []
    dropped = []

    for provider_epg, items in sorted(buckets.items()):
        variants = []
        pseudo = {'group': 'Other UK Channels'}
        for item in items:
            variant = _stream_to_variant(item, pseudo, method='auto_provider_epg_uk', match_score='auto')
            # Give same-EPG variants a slight boost. They are exact provider groups,
            # just not hand-defined in the old WANTED list.
            variant['priority_score'] += 80
            variants.append(variant)
        variants = _unique_sorted_variants(variants)
        if not variants:
            continue

        best = _best_variant_for_name(variants)
        # Need the original item matching the best variant for better section hints.
        best_item = None
        for item in items:
            if str(get_stream_id(item)) == str(best.get('stream_id')):
                best_item = item
                break

        display_name = _auto_display_name_from_item(best_item or items[0])
        wanted = _pseudo_wanted_for_auto_uk(provider_epg, display_name, variants)
        xmltv_id, xmltv_names, epg_score, epg_status, epg_alternatives = _epg_for_wanted(wanted, epg_channels)

        if not xmltv_id:
            dropped.append({
                'name': display_name,
                'reason': 'Auto UK provider_epg found but no confident EPGShare match: %s' % provider_epg,
                'epg_alternatives': epg_alternatives,
            })
            continue

        section = _infer_auto_uk_section(display_name, best_item)
        key = wanted['key']
        default_enabled = _enabled_default_for_channel(key, section, xmltv_id, is_core=False)
        enabled = previous_states.get(key, default_enabled) if key in previous_states else default_enabled

        # If EPGShare has a nicer name, use it only when provider name is too ugly.
        if xmltv_names and (not display_name or len(display_name) <= 3):
            display_name = _title_keep_acronyms(_strip_quality_words(xmltv_names[0]))

        channels.append(_make_channel_record(
            key, display_name, section, variants,
            xmltv_id=xmltv_id, xmltv_names=xmltv_names, epg_score=epg_score,
            enabled=enabled, status='auto_uk_epg_matched', previous_states=previous_states
        ))
        used_provider_epgs.add(provider_epg)

    return channels, dropped


def _mark_core_channel(channel):
    if channel:
        channel['catalog_source'] = 'core_mapping'
        channel['default_enabled_reason'] = 'core uk mapping' if channel.get('enabled') else 'core disabled by default'
    return channel


def _mark_auto_channel(channel):
    if channel:
        channel['catalog_source'] = 'auto_uk_epg'
        channel['default_enabled_reason'] = 'auto uk available, disabled by default'
    return channel


def _mark_extra_channel(channel):
    if channel:
        channel['catalog_source'] = 'curated_extra'
        channel['default_enabled_reason'] = 'non-uk extra disabled by default'
    return channel


def _clean_streams_for_catalog(streams):
    return [item for item in streams if _is_usable_live_stream(item)]


def build_channel_catalog(streams, epg_channels):
    previous_states = _load_existing_enabled_states()
    usable_streams = _clean_streams_for_catalog(streams)
    exact_buckets = build_exact_provider_buckets(usable_streams)
    channels = []
    dropped = []
    used_provider_epgs = set()

    # 1) Known/core mappings. These are your old default ticked list, except Kids.
    for wanted in WANTED_CHANNELS:
        channel, drop = _build_wanted_group(wanted, usable_streams, exact_buckets, epg_channels, previous_states)
        if channel:
            # Core mapped UK channels remain default enabled except Kids.
            if channel.get('key') not in previous_states:
                channel['enabled'] = _enabled_default_for_channel(
                    channel.get('key'), channel.get('section'), channel.get('xmltv_id'), is_core=True
                )
            channels.append(_mark_core_channel(channel))
            for epg_id in wanted.get('provider_epg_ids', []):
                used_provider_epgs.add(epg_id.lower())
        elif drop:
            dropped.append(drop)

    # 2) Auto-discover every other provider_epg *.uk channel that maps to EPGShare.
    auto_channels, auto_dropped = _build_auto_uk_groups(usable_streams, epg_channels, previous_states, used_provider_epgs)
    for item in auto_channels:
        channels.append(_mark_auto_channel(item))
    dropped.extend(auto_dropped)

    # 3) Curated non-UK extras. Always disabled unless previously enabled.
    for extra in EXTRA_CHANNELS:
        channel = _build_extra_group(extra, usable_streams, previous_states)
        if channel:
            channels.append(_mark_extra_channel(channel))

    group_order = {
        'Sports': 1,
        'BBC': 2,
        'ITV': 3,
        'Channel 4 & 5': 4,
        'Entertainment': 5,
        'Movies': 6,
        'News': 7,
        'Documentary': 8,
        'Music': 9,
        'Kids': 10,
        'Other UK Channels': 50,
        'Non-UK Extras': 99,
    }
    channels.sort(key=lambda item: (group_order.get(item.get('section'), 60), item.get('name', '').lower()))

    # Deduplicate by key in case a provider gives duplicate weird aliases. Prefer
    # core mapping, then auto UK, then extras.
    source_rank = {'core_mapping': 1, 'auto_uk_epg': 2, 'curated_extra': 3}
    deduped = {}
    for channel in channels:
        key = channel.get('key')
        if not key:
            continue
        old = deduped.get(key)
        if old is None or source_rank.get(channel.get('catalog_source'), 50) < source_rank.get(old.get('catalog_source'), 50):
            deduped[key] = channel
    channels = list(deduped.values())
    channels.sort(key=lambda item: (group_order.get(item.get('section'), 60), item.get('name', '').lower()))

    return {
        'version': 3,
        'mode': 'grouped_plugin_resolver_auto_uk',
        'server': normalised_server() if clean(SERVER) else '',
        'output_format': OUTPUT_FORMAT,
        'channels': channels,
        'dropped': dropped,
        'stats': {
            'raw_streams': len(streams),
            'usable_live_streams': len(usable_streams),
            'core_channels': len([c for c in channels if c.get('catalog_source') == 'core_mapping']),
            'auto_uk_channels': len([c for c in channels if c.get('catalog_source') == 'auto_uk_epg']),
            'curated_extra_channels': len([c for c in channels if c.get('catalog_source') == 'curated_extra']),
            'vod_or_non_live_dropped': max(0, len(streams) - len(usable_streams)),
        },
    }


# Fast EPG matching for auto-discovered UK channels. This intentionally uses a
# cheaper scorer than find_epg_match(), because auto discovery can touch hundreds
# of provider_epg *.uk groups.
def _prepare_epg_fast_index(epg_channels):
    index = []
    for channel in epg_channels:
        raw = epg_candidate_text(channel)
        text = normalise_text(raw)
        compact = compact_text(raw)
        index.append({
            'channel': channel,
            'text': text,
            'compact': compact,
            'tokens': set(text.split()),
        })
    return index


def _fast_auto_epg_score(alias_texts, epg_entry):
    score = 0
    best_ratio = 0.0
    tokens = epg_entry['tokens']
    compact = epg_entry['compact']
    text = epg_entry['text']

    for alias in alias_texts:
        alias_norm = normalise_text(alias)
        alias_compact = compact_text(alias)
        if not alias_norm or not alias_compact:
            continue
        words = [word for word in alias_norm.split() if word not in {'tv', 'channel', 'hd', 'uk'}]

        if alias_compact == compact:
            score += 2200
        elif alias_compact in compact or compact in alias_compact:
            score += 1200
        elif alias_norm in text:
            score += 1000
        elif words:
            found = sum(1 for word in words if word in tokens or compact_text(word) in compact)
            if found == len(words):
                score += 650 + len(words) * 40
            else:
                score += int((found / float(len(words))) * 250)

        # Only run SequenceMatcher when there is already some overlap.
        if words and any(word in tokens for word in words):
            ratio = difflib.SequenceMatcher(None, alias_compact, compact).ratio()
            if ratio > best_ratio:
                best_ratio = ratio

    score += int(best_ratio * 250)
    return score


def _fast_epg_for_auto(provider_epg, display_name, variants, epg_fast_index):
    epg_base = provider_epg.replace('.uk', '')
    alias_texts = [display_name, epg_base, provider_epg]
    for variant in variants[:4]:
        if variant.get('name'):
            alias_texts.append(_strip_quality_words(variant.get('name')))

    scored = []
    for entry in epg_fast_index:
        score = _fast_auto_epg_score(alias_texts, entry)
        if score > 0:
            scored.append((score, entry['channel']))

    scored.sort(key=lambda row: row[0], reverse=True)
    if not scored:
        return '', [], 0, 'no_match', []

    best_score, best_channel = scored[0]
    second_score = scored[1][0] if len(scored) > 1 else -9999
    alternatives = scored[:5]

    # Auto discovery needs to be stricter than known mappings because we do not
    # want random provider junk appearing in the picker as a UK channel.
    if best_score < 950:
        return '', [], best_score, 'no_match', alternatives
    if best_score < 1800 and best_score - second_score < 160:
        return '', [], best_score, 'ambiguous', alternatives

    return best_channel.get('id', ''), best_channel.get('names', []), best_score, 'auto_uk_epg_matched', alternatives


# Override the earlier _build_auto_uk_groups with the faster EPG matcher.
def _build_auto_uk_groups(streams, epg_channels, previous_states, used_provider_epgs):
    epg_fast_index = _prepare_epg_fast_index(epg_channels)
    buckets = {}
    for item in streams:
        if not _is_auto_uk_candidate(item):
            continue
        epg = get_provider_epg(item)
        if epg in used_provider_epgs:
            continue
        buckets.setdefault(epg, []).append(item)

    channels = []
    dropped = []

    for provider_epg, items in sorted(buckets.items()):
        variants = []
        pseudo = {'group': 'Other UK Channels'}
        for item in items:
            variant = _stream_to_variant(item, pseudo, method='auto_provider_epg_uk', match_score='auto')
            variant['priority_score'] += 80
            variants.append(variant)
        variants = _unique_sorted_variants(variants)
        if not variants:
            continue

        best = _best_variant_for_name(variants)
        best_item = None
        for item in items:
            if str(get_stream_id(item)) == str(best.get('stream_id')):
                best_item = item
                break

        display_name = _auto_display_name_from_item(best_item or items[0])
        xmltv_id, xmltv_names, epg_score, epg_status, epg_alternatives = _fast_epg_for_auto(provider_epg, display_name, variants, epg_fast_index)

        if not xmltv_id:
            dropped.append({
                'name': display_name,
                'reason': 'Auto UK provider_epg found but no confident EPGShare match: %s' % provider_epg,
                'epg_alternatives': epg_alternatives,
            })
            continue

        section = _infer_auto_uk_section(display_name, best_item)
        key = _auto_key_from_provider_epg(provider_epg, display_name)
        default_enabled = _enabled_default_for_channel(key, section, xmltv_id, is_core=False)
        enabled = previous_states.get(key, default_enabled) if key in previous_states else default_enabled

        channels.append(_make_channel_record(
            key, display_name, section, variants,
            xmltv_id=xmltv_id, xmltv_names=xmltv_names, epg_score=epg_score,
            enabled=enabled, status=epg_status, previous_states=previous_states
        ))
        used_provider_epgs.add(provider_epg)

    return channels, dropped


# Faster override again: pre-normalise aliases once per auto channel instead of
# once per EPG candidate.
def _normalised_alias_records(alias_texts):
    records = []
    seen = set()
    for alias in alias_texts:
        alias_norm = normalise_text(alias)
        alias_compact = compact_text(alias)
        if not alias_norm or not alias_compact or alias_compact in seen:
            continue
        seen.add(alias_compact)
        words = [word for word in alias_norm.split() if word not in {'tv', 'channel', 'hd', 'uk'}]
        records.append((alias_norm, alias_compact, words))
    return records


def _fast_auto_epg_score_records(alias_records, epg_entry):
    score = 0
    best_ratio = 0.0
    tokens = epg_entry['tokens']
    compact = epg_entry['compact']
    text = epg_entry['text']

    for alias_norm, alias_compact, words in alias_records:
        if alias_compact == compact:
            score += 2200
        elif alias_compact in compact or compact in alias_compact:
            score += 1200
        elif alias_norm in text:
            score += 1000
        elif words:
            found = 0
            for word in words:
                if word in tokens or word in compact:
                    found += 1
            if found == len(words):
                score += 650 + len(words) * 40
            else:
                score += int((found / float(len(words))) * 250)

        if words and score > 0 and any(word in tokens for word in words):
            ratio = difflib.SequenceMatcher(None, alias_compact, compact).ratio()
            if ratio > best_ratio:
                best_ratio = ratio

    score += int(best_ratio * 250)
    return score


def _fast_epg_for_auto(provider_epg, display_name, variants, epg_fast_index):
    epg_base = provider_epg.replace('.uk', '')
    alias_texts = [display_name, epg_base, provider_epg]
    for variant in variants[:4]:
        if variant.get('name'):
            alias_texts.append(_strip_quality_words(variant.get('name')))
    alias_records = _normalised_alias_records(alias_texts)

    scored = []
    for entry in epg_fast_index:
        score = _fast_auto_epg_score_records(alias_records, entry)
        if score > 0:
            scored.append((score, entry['channel']))

    scored.sort(key=lambda row: row[0], reverse=True)
    if not scored:
        return '', [], 0, 'no_match', []

    best_score, best_channel = scored[0]
    second_score = scored[1][0] if len(scored) > 1 else -9999
    alternatives = scored[:5]

    if best_score < 950:
        return '', [], best_score, 'no_match', alternatives
    if best_score < 1800 and best_score - second_score < 160:
        return '', [], best_score, 'ambiguous', alternatives

    return best_channel.get('id', ''), best_channel.get('names', []), best_score, 'auto_uk_epg_matched', alternatives


def _build_extra_groups_fast(extras, streams, previous_states):
    search_index = []
    for item in streams:
        if not _is_usable_live_stream(item):
            continue
        search_index.append((compact_text(stream_search_text(item)), item))

    channels = []
    for extra in extras:
        alias_compacts = [compact_text(alias) for alias in ([extra.get('name', '')] + extra.get('aliases', [])) if compact_text(alias)]
        scored = []
        for search_compact, item in search_index:
            if not any(alias and alias in search_compact for alias in alias_compacts):
                continue
            score = _extra_match_score(extra, item)
            if score >= MIN_STREAM_MATCH_SCORE:
                scored.append((score, item))
        scored.sort(key=lambda row: row[0], reverse=True)
        variants = []
        for score, item in scored[:MAX_VARIANTS_PER_CHANNEL]:
            variant = _stream_to_variant(item, extra, method='extra_allowlist', match_score=score)
            variant['priority_score'] += int(score) // 5
            variants.append(variant)
        variants = _unique_sorted_variants(variants)
        if not variants:
            continue
        enabled = previous_states.get(extra['key'], False) if extra['key'] in previous_states else False
        channels.append(_mark_extra_channel(_make_channel_record(
            extra['key'], extra['name'], 'Non-UK Extras', variants,
            xmltv_id='', xmltv_names=[], epg_score='none', status='no_uk_epg',
            enabled=enabled, previous_states=previous_states
        )))
    return channels


# Final build_channel_catalog override using fast extras.
def build_channel_catalog(streams, epg_channels):
    previous_states = _load_existing_enabled_states()
    usable_streams = _clean_streams_for_catalog(streams)
    exact_buckets = build_exact_provider_buckets(usable_streams)
    channels = []
    dropped = []
    used_provider_epgs = set()

    for wanted in WANTED_CHANNELS:
        channel, drop = _build_wanted_group(wanted, usable_streams, exact_buckets, epg_channels, previous_states)
        if channel:
            if channel.get('key') not in previous_states:
                channel['enabled'] = _enabled_default_for_channel(
                    channel.get('key'), channel.get('section'), channel.get('xmltv_id'), is_core=True
                )
            channels.append(_mark_core_channel(channel))
            for epg_id in wanted.get('provider_epg_ids', []):
                used_provider_epgs.add(epg_id.lower())
        elif drop:
            dropped.append(drop)

    auto_channels, auto_dropped = _build_auto_uk_groups(usable_streams, epg_channels, previous_states, used_provider_epgs)
    channels.extend(_mark_auto_channel(item) for item in auto_channels)
    dropped.extend(auto_dropped)

    channels.extend(_build_extra_groups_fast(EXTRA_CHANNELS, usable_streams, previous_states))

    group_order = {
        'Sports': 1,
        'BBC': 2,
        'ITV': 3,
        'Channel 4 & 5': 4,
        'Entertainment': 5,
        'Movies': 6,
        'News': 7,
        'Documentary': 8,
        'Music': 9,
        'Kids': 10,
        'Other UK Channels': 50,
        'Non-UK Extras': 99,
    }

    source_rank = {'core_mapping': 1, 'auto_uk_epg': 2, 'curated_extra': 3}
    deduped = {}
    for channel in channels:
        key = channel.get('key')
        if not key:
            continue
        old = deduped.get(key)
        if old is None or source_rank.get(channel.get('catalog_source'), 50) < source_rank.get(old.get('catalog_source'), 50):
            deduped[key] = channel

    channels = list(deduped.values())
    channels.sort(key=lambda item: (group_order.get(item.get('section'), 60), item.get('name', '').lower()))

    return {
        'version': 3,
        'mode': 'grouped_plugin_resolver_auto_uk',
        'server': normalised_server() if clean(SERVER) else '',
        'output_format': OUTPUT_FORMAT,
        'channels': channels,
        'dropped': dropped,
        'stats': {
            'raw_streams': len(streams),
            'usable_live_streams': len(usable_streams),
            'core_channels': len([c for c in channels if c.get('catalog_source') == 'core_mapping']),
            'auto_uk_channels': len([c for c in channels if c.get('catalog_source') == 'auto_uk_epg']),
            'curated_extra_channels': len([c for c in channels if c.get('catalog_source') == 'curated_extra']),
            'vod_or_non_live_dropped': max(0, len(streams) - len(usable_streams)),
        },
    }


# Override again: build extras from already-cleaned live streams, so we do not
# repeat the expensive live/VOD filter after auto discovery.
def _build_extra_groups_fast(extras, streams, previous_states):
    search_index = [(compact_text(stream_search_text(item)), item) for item in streams]

    channels = []
    for extra in extras:
        alias_compacts = []
        for alias in [extra.get('name', '')] + extra.get('aliases', []):
            alias_compact = compact_text(alias)
            if alias_compact and alias_compact not in alias_compacts:
                alias_compacts.append(alias_compact)
        scored = []
        for search_compact, item in search_index:
            if not any(alias in search_compact for alias in alias_compacts):
                continue
            score = _extra_match_score(extra, item)
            if score >= MIN_STREAM_MATCH_SCORE:
                scored.append((score, item))
        scored.sort(key=lambda row: row[0], reverse=True)
        variants = []
        for score, item in scored[:MAX_VARIANTS_PER_CHANNEL]:
            variant = _stream_to_variant(item, extra, method='extra_allowlist', match_score=score)
            variant['priority_score'] += int(score) // 5
            variants.append(variant)
        variants = _unique_sorted_variants(variants)
        if not variants:
            continue
        enabled = previous_states.get(extra['key'], False) if extra['key'] in previous_states else False
        channels.append(_mark_extra_channel(_make_channel_record(
            extra['key'], extra['name'], 'Non-UK Extras', variants,
            xmltv_id='', xmltv_names=[], epg_score='none', status='no_uk_epg',
            enabled=enabled, previous_states=previous_states
        )))
    return channels


if __name__ == "__main__":
    main()


# ============================================================================
# FLAM LIVE TV CATALOGUE MODE v4
# UK + US EPG merge, grouped pattern extras, and boot-safe EPG refresh helpers.
# This block intentionally overrides the earlier grouped Live TV functions.
# ============================================================================

EPG_US_URL = "https://epgshare01.online/epgshare01/epg_ripper_US2.xml.gz"
EPG_US_GZ_FILE = str(IPTV_CACHE_DIR / "epg_ripper_US2.xml.gz")

# Fallback only: if the US-only source is missing/unreadable, use the all-sources
# file internally and still write one small filtered IPTV-EPG.xml for IPTV Simple.
EPG_ALL_URL = "https://epgshare01.online/epgshare01/epg_ripper_ALL_SOURCES1.xml.gz"
EPG_ALL_GZ_FILE = str(IPTV_CACHE_DIR / "epg_ripper_ALL_SOURCES1.xml.gz")
USE_ALL_SOURCES_US_FALLBACK = True

EPG_SOURCES = {
    "uk": {
        "url": EPG_URL,
        "file": EPG_GZ_FILE,
        "required": True,
        "label": "UK EPGShare XMLTV file",
    },
    "us": {
        "url": EPG_US_URL,
        "file": EPG_US_GZ_FILE,
        "required": False,
        "label": "US EPGShare XMLTV file",
    },
}

OPTIMISATION_VERSION = "safe-code-optimisations-v1"
_EPG_LOOKUP_CACHE = {}


def _now_seconds():
    try:
        import time
        return time.perf_counter()
    except Exception:
        return 0.0


def _new_timing_tracker():
    timings = []
    start = _now_seconds()
    last = [start]

    def mark(label):
        now = _now_seconds()
        if now and last[0]:
            timings.append({
                "stage": label,
                "seconds": round(max(0.0, now - last[0]), 3),
                "total_seconds": round(max(0.0, now - start), 3),
            })
        else:
            timings.append({"stage": label, "seconds": 0.0, "total_seconds": 0.0})
        last[0] = now

    return timings, mark


def _format_timings(timings):
    if not timings:
        return []
    lines = ["Timing:"]
    for item in timings:
        lines.append("  %s: %.3fs (total %.3fs)" % (
            item.get("stage", "stage"),
            float(item.get("seconds") or 0),
            float(item.get("total_seconds") or 0),
        ))
    try:
        lines.append("  Total measured: %.3fs" % float(timings[-1].get("total_seconds") or 0))
    except Exception:
        pass
    return lines


def _build_epg_lookup(epg_channels):
    by_id = {}
    by_compact = {}
    for channel in epg_channels or []:
        try:
            channel_id = clean(channel.get("id"))
        except Exception:
            channel_id = ""
        if not channel_id:
            continue
        by_id.setdefault(channel_id.lower(), channel)
        by_compact.setdefault(compact_text(channel_id), channel)
    return {"by_id": by_id, "by_compact": by_compact}


def _epg_lookup_for_channels(epg_channels):
    key = id(epg_channels)
    cached = _EPG_LOOKUP_CACHE.get(key)
    if cached is not None:
        return cached
    lookup = _build_epg_lookup(epg_channels)
    _EPG_LOOKUP_CACHE[key] = lookup
    return lookup


# Pattern extras create ONE guide/picker channel from many numbered provider entries.
# Example: 4K-WC -> click once in guide -> choose 4K-WC 1, 4K-WC 2, etc.
EXTRA_PATTERNS = [
    {
        "key": "4k_wc",
        "name": "4K World Cup",
        "section": "Non-UK Extras",
        "group": "Non-UK Extras",
        "aliases": ["4k-wc", "4k wc", "4k world cup", "world cup"],
        "pattern": r"\b4k\s*[-:]?\s*wc\s*\d*\b",
        "reject": ["vod", "replay", "backup", "test", "offline"],
        "enabled_default": False,
    },
]

USEFUL_US_EXTRA_TERMS = [
    "dazn", "nba", "nfl", "nhl", "mlb", "ufc", "espn", "bein", "wwe",
    "fox sports", "fs1", "fs2", "cbs sports", "nbc sports", "golf channel",
    "tennis channel", "redzone", "red zone", "sec network", "acc network",
]

AUTO_US_REJECT_TERMS = [
    "adult", "xxx", "test", "backup", "offline", "no event", "event will start",
    "replay", "vod", "movie", "movies", "series", "24/7", "24-7",
]


# These hints let curated extras get US EPG even when the provider stream itself
# has a blank epg_channel_id.  Exact IDs are tried first; aliases are used as a
# fallback against EPGShare display names.
EXTRA_US_EPG_HINTS = {
    "espn": {"ids": ["espn.us"], "aliases": ["ESPN"]},
    "espn_2": {"ids": ["espn2.us", "espn.2.us"], "aliases": ["ESPN2", "ESPN 2"]},
    "nfl_network": {"ids": ["nflnetwork.us", "nfl.network.us"], "aliases": ["NFL Network"]},
    "nfl_redzone": {"ids": ["nflredzone.us", "nfl.redzone.us", "nflredzonechannel.us"], "aliases": ["NFL RedZone", "NFL Red Zone"]},
    "nba_tv": {"ids": ["nbatv.us", "nba.tv.us"], "aliases": ["NBA TV", "NBA Television"]},
    "nhl_network": {"ids": ["nhlnetwork.us", "nhl.network.us"], "aliases": ["NHL Network"]},
    "mlb_network": {"ids": ["mlbnetwork.us", "mlb.network.us"], "aliases": ["MLB Network"]},
    "ufc_fight_pass": {"ids": ["ufcfightpass.us", "ufc.fightpass.us"], "aliases": ["UFC Fight Pass", "UFC TV"]},
    "wwe_network": {"ids": ["wwenetwork.us", "wwe.network.us"], "aliases": ["WWE Network"]},
}


def _region_from_epg_id(epg_id):
    """Return the EPG source region for an XMLTV/provider id.

    EPGShare region IDs are not always just `.uk` / `.us`. The US2 file
    uses ids like `ESPN.HD.us2`, `NBA.TV.HD.us2`, etc. The previous
    version only recognised `.us`, so `.us2` channels were incorrectly
    marked as UK. That meant the merged EPG writer looked for US channel IDs
    inside the UK EPG root, so the M3U had `tvg-id=ESPN.HD.us2` but the
    generated IPTV-EPG.xml did not contain that channel/programmes.
    """
    epg_id = clean(epg_id).lower()
    if re.search(r"\.us\d*$", epg_id):
        return "us"
    if re.search(r"\.uk\d*$", epg_id):
        return "uk"
    return "uk"


def _region_for_wanted(wanted):
    for epg_id in wanted.get("provider_epg_ids", []):
        region = _region_from_epg_id(epg_id)
        if region:
            return region
    return "uk"


def _epg_channels_from_data(epg_data, region="uk"):
    if isinstance(epg_data, dict):
        entry = epg_data.get(region) or {}
        channels = entry.get("channels", []) if isinstance(entry, dict) else []
        if channels:
            return channels

        # If the region-specific EPG source is not available, fall back to the
        # all-sources file but only expose IDs for the requested region.
        all_entry = epg_data.get("all") or {}
        all_channels = all_entry.get("channels", []) if isinstance(all_entry, dict) else []
        if all_channels and region in {"uk", "us"}:
            # All-sources can contain numbered region ids such as `.us2`.
            # Use the same region classifier as the rest of the generator.
            return [item for item in all_channels if _region_from_epg_id(item.get("id")) == region]
        return []
    return epg_data or []


def _epg_roots_from_data(epg_data):
    if isinstance(epg_data, dict):
        return {region: entry.get("root") for region, entry in epg_data.items() if isinstance(entry, dict) and entry.get("root") is not None}
    return {"uk": epg_data}


def _find_epg_channel_exact(provider_epgs, epg_channels):
    # EPGShare IDs are not always consistently punctuated/cased across files.
    # Build a small lookup once per EPG channel list instead of linearly scanning
    # the whole XMLTV channel list for every wanted/extra channel.
    wanted_ids = [clean(epg).lower() for epg in provider_epgs if clean(epg)]
    wanted_compacts = [compact_text(epg) for epg in provider_epgs if clean(epg)]
    if not wanted_ids and not wanted_compacts:
        return None

    lookup = _epg_lookup_for_channels(epg_channels)
    by_id = lookup.get("by_id", {})
    by_compact = lookup.get("by_compact", {})

    for wanted in wanted_ids:
        channel = by_id.get(wanted)
        if channel is not None:
            return channel
    for wanted in wanted_compacts:
        channel = by_compact.get(wanted)
        if channel is not None:
            return channel
    return None


def _epg_for_wanted(wanted, epg_channels):
    """Override: supports either a simple channel list or the v4 region dict."""
    region = wanted.get("epg_region") or _region_for_wanted(wanted)
    channels = _epg_channels_from_data(epg_channels, region)

    exact = _find_epg_channel_exact(wanted.get("provider_epg_ids", []), channels)
    if exact:
        return exact.get("id", ""), exact.get("names", []), HIGH_CONFIDENCE_SCORE, "%s_exact" % region, [(HIGH_CONFIDENCE_SCORE, exact)]

    epg_channel, epg_score, epg_alternatives = find_epg_match(wanted, channels)
    if epg_channel:
        return epg_channel.get("id", ""), epg_channel.get("names", []), epg_score, "%s_matched" % region, epg_alternatives
    return "", [], epg_score, "no_%s_match" % region, epg_alternatives


def _download_epg_sources():
    if not DOWNLOAD_EPG:
        return {"downloaded": False, "messages": ["DOWNLOAD_EPG is disabled."]}

    messages = []
    for region, source in EPG_SOURCES.items():
        try:
            print("Downloading %s..." % source.get("label", region))
            download_file(source["url"], source["file"], description=source.get("label", region))
            messages.append("%s downloaded" % region.upper())
        except Exception as error:
            message = "%s EPG download failed: %s" % (region.upper(), str(error))
            messages.append(message)
            if source.get("required"):
                raise GeneratorError(message)
            print(message)
    return {"downloaded": True, "messages": messages}


def _load_epg_sources(require_uk=True):
    epg_data = {}
    for region, source in EPG_SOURCES.items():
        path = Path(source["file"])
        if not path.exists():
            if source.get("required") and require_uk:
                raise GeneratorError("Missing %s. Run Generate / Refresh Live TV again." % source.get("label", region))
            continue
        try:
            root = load_epg_root(path)
            channels = get_epg_channels(root)
            if channels:
                epg_data[region] = {
                    "root": root,
                    "channels": channels,
                    "index": _build_epg_lookup(channels),
                    "path": str(path),
                    "label": source.get("label", region),
                }
                print("%s channels found: %s" % (region.upper(), len(channels)))
        except Exception as error:
            if source.get("required") and require_uk:
                raise GeneratorError("Could not read %s. Try deleting it and running again.\n%s" % (source.get("label", region), str(error)))
            print("Optional %s EPG could not be read: %s" % (region.upper(), str(error)))

    # If US2 is missing/broken, use the larger all-sources EPG as an internal fallback.
    # IPTV Simple still receives the small local filtered IPTV-EPG.xml, not the huge source.
    if USE_ALL_SOURCES_US_FALLBACK and "us" not in epg_data:
        all_path = Path(EPG_ALL_GZ_FILE)
        if DOWNLOAD_EPG and not all_path.exists():
            try:
                print("US EPG not available. Downloading all-sources EPG fallback...")
                download_file(EPG_ALL_URL, EPG_ALL_GZ_FILE, description="EPGShare all-sources fallback XMLTV file")
            except Exception as error:
                print("Optional all-sources EPG fallback failed: %s" % str(error))
        if all_path.exists():
            try:
                root = load_epg_root(all_path)
                channels = get_epg_channels(root)
                if channels:
                    epg_data["all"] = {
                        "root": root,
                        "channels": channels,
                        "index": _build_epg_lookup(channels),
                        "path": str(all_path),
                        "label": "EPGShare all-sources fallback XMLTV file",
                    }
                    print("ALL-SOURCES channels found: %s" % len(channels))
            except Exception as error:
                print("Optional all-sources EPG fallback could not be read: %s" % str(error))

    if require_uk and "uk" not in epg_data:
        raise GeneratorError("The UK EPG source did not load, so Live TV cannot be generated safely.")
    return epg_data

def _selected_xmltv_ids_by_region(catalog):
    selected = {}
    changed = False
    for channel in _enabled_channels(catalog):
        xmltv_id = clean(channel.get("xmltv_id"))
        if not xmltv_id:
            continue

        # Always derive the region from the actual XMLTV id. Existing catalogues
        # generated by the previous patch may have `epg_region: uk` stored for
        # IDs like `ESPN.HD.us2`, because `.us2` was not recognised. If we trust
        # the stale stored value, the writer searches the UK EPG root for US ids
        # and the Live Guide has no US programmes.
        region = _region_from_epg_id(xmltv_id)
        if clean(channel.get("epg_region")) != region:
            channel["epg_region"] = region
            changed = True
        selected.setdefault(region, set()).add(xmltv_id)

    if changed:
        try:
            save_catalog(catalog)
        except Exception:
            pass
    return selected


def _write_empty_epg():
    root = ET.Element("tv")
    tmp = Path(str(OUTPUT_EPG_FILE) + ".tmp")
    tmp.parent.mkdir(parents=True, exist_ok=True)
    ET.ElementTree(root).write(str(tmp), encoding="utf-8", xml_declaration=True)
    tmp.replace(OUTPUT_EPG_FILE)
    return "0 channels / 0 programmes"


def _write_merged_filtered_epg_for_catalog(catalog, epg_data):
    """Write one valid XMLTV file from UK/US/all EPG sources.

    Safe optimisation notes:
    - Keeps the required XMLTV order: all <channel> elements first, then all <programme> elements.
    - Avoids copy.deepcopy() for selected XML nodes. ElementTree can write the same
      element objects into the output tree, and we do not mutate the source nodes.
    - Skips ET.indent(); IPTV Simple does not need pretty XML, and indentation adds
      CPU time and file size on low-power devices.
    """
    selected_by_region = _selected_xmltv_ids_by_region(catalog)
    if not any(selected_by_region.values()):
        return _write_empty_epg()

    new_root = ET.Element("tv")
    channel_count = 0
    programme_count = 0
    seen_channels = set()
    seen_programmes = set()
    channel_nodes = []
    programme_nodes = []

    roots = _epg_roots_from_data(epg_data)

    # Carry useful root metadata across from the first available source.
    for source in roots.values():
        if source is not None:
            try:
                for attr, value in source.attrib.items():
                    new_root.set(attr, value)
            except Exception:
                pass
            break

    def collect_from_root(root, selected_ids):
        nonlocal channel_count, programme_count
        if root is None:
            return

        selected_lower = {clean(item).lower() for item in selected_ids if clean(item)}
        if not selected_lower:
            return

        for channel in root.findall("channel"):
            channel_id = clean(channel.get("id"))
            channel_key = channel_id.lower()
            if channel_key in selected_lower and channel_key not in seen_channels:
                channel_nodes.append(channel)
                seen_channels.add(channel_key)
                channel_count += 1

        for programme in root.findall("programme"):
            channel_id = clean(programme.get("channel"))
            channel_key = channel_id.lower()
            if channel_key not in selected_lower:
                continue
            key = (
                channel_key,
                programme.get("start"),
                programme.get("stop"),
                clean(programme.findtext("title"))
            )
            if key in seen_programmes:
                continue
            programme_nodes.append(programme)
            seen_programmes.add(key)
            programme_count += 1

    for region, selected_ids in selected_by_region.items():
        root = roots.get(region)
        if root is None:
            root = roots.get("all")
        collect_from_root(root, selected_ids)

    for channel in channel_nodes:
        new_root.append(channel)
    for programme in programme_nodes:
        new_root.append(programme)

    tmp = Path(str(OUTPUT_EPG_FILE) + ".tmp")
    tmp.parent.mkdir(parents=True, exist_ok=True)
    ET.ElementTree(new_root).write(str(tmp), encoding="utf-8", xml_declaration=True)
    tmp.replace(OUTPUT_EPG_FILE)
    return "%s channels / %s programmes" % (channel_count, programme_count)

def _write_filtered_epg_for_catalog(catalog, epg_root_or_data):
    """Override: can write from UK-only root or from merged UK/US source dict."""
    if isinstance(epg_root_or_data, dict):
        return _write_merged_filtered_epg_for_catalog(catalog, epg_root_or_data)
    return _write_merged_filtered_epg_for_catalog(catalog, {"uk": {"root": epg_root_or_data, "channels": get_epg_channels(epg_root_or_data)}})


def _add_epg_metadata(channel, xmltv_id):
    region = _region_from_epg_id(xmltv_id)
    channel["epg_region"] = region
    return channel


def _make_channel_record(key, name, section, variants, xmltv_id="", xmltv_names=None, epg_score="", enabled=None, status="matched", previous_states=None):
    """Override: same record shape, plus epg_region for merged UK/US EPG writing."""
    previous_states = previous_states or {}
    if enabled is None:
        if key in previous_states:
            enabled = bool(previous_states[key])
        else:
            enabled = _channel_default_enabled(section, xmltv_id)

    first_logo = ""
    for variant in variants:
        if variant.get("logo"):
            first_logo = variant.get("logo")
            break

    record = {
        "key": key,
        "name": name,
        "section": section,
        "enabled": bool(enabled),
        "xmltv_id": xmltv_id or "",
        "epg_region": _region_from_epg_id(xmltv_id) if xmltv_id else "",
        "xmltv_names": xmltv_names or [],
        "epg_match_score": epg_score,
        "epg_status": status,
        "logo": first_logo,
        "stream_count": len(variants),
        "streams": variants,
    }
    return record


def _is_useful_us_candidate(item):
    if not _is_usable_live_stream(item):
        return False
    epg = get_provider_epg(item)
    if not epg.endswith(".us"):
        return False
    search = normalise_text(" ".join([get_stream_name(item), _clean_category(item), epg]))
    compact = compact_text(search)
    for term in AUTO_US_REJECT_TERMS:
        if contains_term(search, compact, term):
            return False
    return any(contains_term(search, compact, term) for term in USEFUL_US_EXTRA_TERMS)


def _infer_us_extra_name(provider_epg, best_item):
    name = _strip_quality_words(display_name(dict(best_item)))
    name = re.sub(r"(?i)^(US|USA|VIP|SPORTS|LIVE)\s*[:\-]+\s*", "", name).strip(" :-")
    if not name:
        base = provider_epg.replace(".us", "")
        name = re.sub(r"[^a-zA-Z0-9]+", " ", base).strip()
    return _title_keep_acronyms(name)


def _auto_us_key(provider_epg, fallback_name=""):
    base = provider_epg.lower().replace(".us", "") or fallback_name.lower()
    base = normalise_text(base)
    base = re.sub(r"[^a-z0-9]+", "_", base).strip("_")
    if not base:
        base = re.sub(r"[^a-z0-9]+", "_", compact_text(fallback_name)).strip("_")
    return "us_%s" % base[:80]


def _pseudo_wanted_for_us(provider_epg, display_name, variants):
    aliases = [display_name, provider_epg, provider_epg.replace(".us", "")]
    for variant in variants[:6]:
        if variant.get("name"):
            aliases.append(_strip_quality_words(variant.get("name")))
        if variant.get("provider_epg"):
            aliases.append(variant.get("provider_epg"))
    return {
        "key": _auto_us_key(provider_epg, display_name),
        "name": display_name,
        "group": "US Extras",
        "epg_region": "us",
        "aliases": aliases,
        "epg_aliases": aliases,
        "provider_epg_ids": [provider_epg],
        "reject": ["replay", "backup", "test"],
    }


def _build_auto_us_extra_groups(streams, epg_data, previous_states, used_provider_epgs):
    us_epg_channels = _epg_channels_from_data(epg_data, "us")
    if not us_epg_channels:
        return [], []

    buckets = {}
    for item in streams:
        if not _is_useful_us_candidate(item):
            continue
        epg = get_provider_epg(item)
        if epg in used_provider_epgs:
            continue
        buckets.setdefault(epg, []).append(item)

    channels = []
    dropped = []
    for provider_epg, items in sorted(buckets.items()):
        variants = []
        pseudo = {"group": "US Extras"}
        for item in items:
            variant = _stream_to_variant(item, pseudo, method="auto_provider_epg_us", match_score="auto")
            variant["priority_score"] += 70
            variants.append(variant)
        variants = _unique_sorted_variants(variants)
        if not variants:
            continue
        best = _best_variant_for_name(variants)
        best_item = items[0]
        for item in items:
            if str(get_stream_id(item)) == str(best.get("stream_id")):
                best_item = item
                break
        display_name = _infer_us_extra_name(provider_epg, best_item)
        wanted = _pseudo_wanted_for_us(provider_epg, display_name, variants)
        xmltv_id, xmltv_names, epg_score, epg_status, epg_alternatives = _epg_for_wanted(wanted, epg_data)
        if not xmltv_id:
            dropped.append({
                "name": display_name,
                "reason": "Useful US provider_epg found but no confident US EPGShare match: %s" % provider_epg,
                "epg_alternatives": epg_alternatives,
            })
            continue
        key = wanted["key"]
        enabled = previous_states.get(key, False) if key in previous_states else False
        channel = _make_channel_record(
            key, display_name, "US Extras", variants,
            xmltv_id=xmltv_id, xmltv_names=xmltv_names, epg_score=epg_score,
            enabled=enabled, status="us_epg_matched", previous_states=previous_states
        )
        channel["catalog_source"] = "auto_us_epg"
        channel["default_enabled_reason"] = "US extra available, disabled by default"
        channels.append(channel)
        used_provider_epgs.add(provider_epg)
    return channels, dropped


def _extra_exact_epg_from_variants(variants, epg_data, region="us"):
    epg_channels = _epg_channels_from_data(epg_data, region)
    provider_epgs = [variant.get("provider_epg") for variant in variants if clean(variant.get("provider_epg"))]
    exact = _find_epg_channel_exact(provider_epgs, epg_channels)
    if exact:
        return exact.get("id", ""), exact.get("names", []), HIGH_CONFIDENCE_SCORE, "%s_exact" % region
    return "", [], "none", "no_%s_epg" % region


def _variant_has_us_provider_epg(variants):
    for variant in variants or []:
        epg_id = clean(variant.get("provider_epg")).lower()
        if re.search(r"\.us\d*$", epg_id):
            return True
    return False


def _curated_extra_can_fuzzy_match_us_epg(extra, variants):
    """Avoid false US EPG matches for vague extras.

    DAZN-style extras often have no US EPG id in the provider data. If we let
    the generic fuzzy matcher search the whole US EPG, it can pick unrelated
    channels such as UPtv. Only fuzzy-match curated extras when we have an
    explicit hint table entry, or when at least one chosen stream variant has a
    real `.us` provider EPG id.
    """
    key = clean(extra.get("key"))
    return bool(EXTRA_US_EPG_HINTS.get(key)) or _variant_has_us_provider_epg(variants)


def _wanted_for_curated_extra_epg(extra, variants):
    key = clean(extra.get("key"))
    hint = EXTRA_US_EPG_HINTS.get(key, {})

    provider_ids = []
    for epg_id in hint.get("ids", []):
        if clean(epg_id) and clean(epg_id).lower() not in provider_ids:
            provider_ids.append(clean(epg_id).lower())
    for variant in variants:
        epg_id = clean(variant.get("provider_epg")).lower()
        if epg_id.endswith(".us") and epg_id not in provider_ids:
            provider_ids.append(epg_id)

    aliases = [extra.get("name", "")] + extra.get("aliases", []) + hint.get("aliases", [])
    for variant in variants[:10]:
        if variant.get("name"):
            aliases.append(_safe_kodi_display_text(_strip_quality_words(variant.get("name"))))
        if variant.get("provider_epg"):
            aliases.append(variant.get("provider_epg"))
            aliases.append(variant.get("provider_epg").replace(".us", ""))

    # De-duplicate while preserving order.
    seen = set()
    clean_aliases = []
    for alias in aliases:
        alias = clean(alias)
        key_alias = alias.lower()
        if alias and key_alias not in seen:
            clean_aliases.append(alias)
            seen.add(key_alias)

    return {
        "key": key,
        "name": extra.get("name", ""),
        "group": "US Extras",
        "epg_region": "us",
        "aliases": clean_aliases,
        "epg_aliases": clean_aliases,
        "provider_epg_ids": provider_ids,
        "reject": extra.get("reject", []) + ["deportes", "spanish", "mx", "br", "arg"],
    }


def _lookup_curated_extra_epg(extra, variants, epg_data):
    # Avoid false positives for extras that have no explicit US EPG hint and no
    # provider `.us` EPG id. Example from the bad output: DAZN 2 matched UPtv.us2.
    if not _curated_extra_can_fuzzy_match_us_epg(extra, variants):
        return "", [], "none", "curated_no_epg"

    wanted = _wanted_for_curated_extra_epg(extra, variants)
    xmltv_id, xmltv_names, epg_score, epg_status, epg_alternatives = _epg_for_wanted(wanted, epg_data)
    if xmltv_id:
        return xmltv_id, xmltv_names, epg_score, epg_status

    # Last fallback: if one of the chosen variants already has a .us provider_epg,
    # try exact channel-id matching directly.
    xmltv_id, xmltv_names, epg_score, epg_status = _extra_exact_epg_from_variants(variants, epg_data, "us")
    if xmltv_id:
        return xmltv_id, xmltv_names, epg_score, epg_status

    return "", [], epg_score, epg_status


def _refresh_catalog_extra_epg_metadata(catalog, epg_data):
    """Backfill US EPG IDs into existing catalogues.

    This matters if the catalogue was generated before the US EPG source was
    available, or if a user presses Rebuild/Refresh EPG Only after upgrading.
    """
    extras_by_key = {extra.get("key"): extra for extra in EXTRA_CHANNELS}
    changed = False
    for channel in catalog.get("channels", []):
        # Repair stale epg_region values from older builds. In particular,
        # `.us2` XMLTV ids were previously stored as UK.
        existing_xmltv = clean(channel.get("xmltv_id"))
        key = channel.get("key")
        extra = extras_by_key.get(key)
        if existing_xmltv:
            correct_region = _region_from_epg_id(existing_xmltv)
            if clean(channel.get("epg_region")) != correct_region:
                channel["epg_region"] = correct_region
                changed = True

            # Clear bogus fuzzy US EPG matches for curated extras. This repairs
            # existing catalogues where DAZN 2 was incorrectly mapped to UPtv.us2.
            if extra and correct_region == "us" and not _curated_extra_can_fuzzy_match_us_epg(extra, channel.get("streams", [])):
                channel["xmltv_id"] = ""
                channel["xmltv_names"] = []
                channel["epg_match_score"] = "none"
                channel["epg_status"] = "curated_no_epg"
                channel["epg_region"] = ""
                channel["section"] = "Non-UK Extras"
                changed = True
            continue

        if not extra:
            continue
        variants = channel.get("streams", []) or []
        xmltv_id, xmltv_names, epg_score, epg_status = _lookup_curated_extra_epg(extra, variants, epg_data)
        if not xmltv_id:
            continue
        channel["xmltv_id"] = xmltv_id
        channel["xmltv_names"] = xmltv_names
        channel["epg_match_score"] = epg_score
        channel["epg_status"] = epg_status
        channel["epg_region"] = _region_from_epg_id(xmltv_id)
        channel["section"] = "US Extras"
        changed = True
    if changed:
        save_catalog(catalog)
    return changed


def _build_extra_groups_with_epg(extras, streams, epg_data, previous_states):
    search_index = [(compact_text(stream_search_text(item)), item) for item in streams]
    channels = []
    for extra in extras:
        alias_compacts = []
        for alias in [extra.get("name", "")] + extra.get("aliases", []):
            alias_compact = compact_text(alias)
            if alias_compact and alias_compact not in alias_compacts:
                alias_compacts.append(alias_compact)
        scored = []
        for search_compact, item in search_index:
            if not any(alias in search_compact for alias in alias_compacts):
                continue
            score = _extra_match_score(extra, item)
            if score >= MIN_STREAM_MATCH_SCORE:
                scored.append((score, item))
        scored.sort(key=lambda row: row[0], reverse=True)
        variants = []
        for score, item in scored[:MAX_VARIANTS_PER_CHANNEL]:
            variant = _stream_to_variant(item, extra, method="extra_allowlist", match_score=score)
            variant["priority_score"] += int(score) // 5
            variants.append(variant)
        variants = _unique_sorted_variants(variants)
        if not variants:
            continue

        xmltv_id, xmltv_names, epg_score, epg_status = _lookup_curated_extra_epg(extra, variants, epg_data)
        section = "US Extras" if xmltv_id else "Non-UK Extras"
        enabled = previous_states.get(extra["key"], False) if extra["key"] in previous_states else False
        channel = _make_channel_record(
            extra["key"], extra["name"], section, variants,
            xmltv_id=xmltv_id, xmltv_names=xmltv_names, epg_score=epg_score,
            status=epg_status if xmltv_id else "curated_no_epg",
            enabled=enabled, previous_states=previous_states
        )
        channel["catalog_source"] = "curated_extra"
        channel["default_enabled_reason"] = "%s disabled by default" % section
        channels.append(channel)
    return channels


def _pattern_match_score(pattern_extra, item, search_raw=None, search_norm=None, compiled_pattern=None):
    search_raw = search_raw if search_raw is not None else " ".join([get_stream_name(item), get_provider_epg(item), _clean_category(item)])
    search_norm = search_norm if search_norm is not None else normalise_text(search_raw)
    matcher = compiled_pattern or re.compile(pattern_extra.get("pattern", ""), re.I)
    if not matcher.search(search_norm):
        return -9999
    score = 500 + _variant_priority(item, pattern_extra, 0)
    score = apply_reject_penalties(score, search_raw, pattern_extra.get("reject", []), penalty=500)
    return score


def _build_pattern_extra_groups(patterns, streams, previous_states):
    channels = []
    # Pre-normalise stream text once for all pattern extras. This keeps the
    # exact same matching logic but avoids repeated string cleaning on large
    # provider catalogues.
    stream_index = []
    for item in streams:
        search_raw = " ".join([get_stream_name(item), get_provider_epg(item), _clean_category(item)])
        stream_index.append((search_raw, normalise_text(search_raw), item))

    for pattern_extra in patterns:
        scored = []
        try:
            compiled_pattern = re.compile(pattern_extra.get("pattern", ""), re.I)
        except Exception:
            compiled_pattern = re.compile(r"a^")
        for search_raw, search_norm, item in stream_index:
            score = _pattern_match_score(pattern_extra, item, search_raw=search_raw, search_norm=search_norm, compiled_pattern=compiled_pattern)
            if score >= MIN_STREAM_MATCH_SCORE:
                scored.append((score, item))
        scored.sort(key=lambda row: row[0], reverse=True)
        variants = []
        for score, item in scored[:MAX_VARIANTS_PER_CHANNEL]:
            variant = _stream_to_variant(item, pattern_extra, method="extra_pattern", match_score=score)
            variant["priority_score"] += int(score) // 5
            variants.append(variant)
        variants = _unique_sorted_variants(variants)
        if not variants:
            continue
        key = pattern_extra["key"]
        enabled = previous_states.get(key, False) if key in previous_states else bool(pattern_extra.get("enabled_default", False))
        channel = _make_channel_record(
            key, pattern_extra["name"], pattern_extra.get("section", "Non-UK Extras"), variants,
            xmltv_id="", xmltv_names=[], epg_score="none", status="pattern_no_epg",
            enabled=enabled, previous_states=previous_states
        )
        channel["catalog_source"] = "pattern_extra"
        channel["default_enabled_reason"] = "pattern extra disabled by default"
        channels.append(channel)
    return channels


def build_channel_catalog(streams, epg_data):
    previous_states = _load_existing_enabled_states()
    usable_streams = _clean_streams_for_catalog(streams)
    exact_buckets = build_exact_provider_buckets(usable_streams)
    channels = []
    dropped = []
    used_provider_epgs = set()

    # 1) Known/core mappings. Old WANTED_CHANNELS are the default ticked list, except Kids.
    uk_epg_channels = _epg_channels_from_data(epg_data, "uk")
    for wanted in WANTED_CHANNELS:
        wanted["epg_region"] = "uk"
        channel, drop = _build_wanted_group(wanted, usable_streams, exact_buckets, uk_epg_channels, previous_states)
        if channel:
            if channel.get("key") not in previous_states:
                channel["enabled"] = _enabled_default_for_channel(
                    channel.get("key"), channel.get("section"), channel.get("xmltv_id"), is_core=True
                )
            channel = _mark_core_channel(channel)
            channel["epg_region"] = _region_from_epg_id(channel.get("xmltv_id")) if channel.get("xmltv_id") else "uk"
            channels.append(channel)
            for epg_id in wanted.get("provider_epg_ids", []):
                used_provider_epgs.add(epg_id.lower())
        elif drop:
            dropped.append(drop)

    # 2) Auto-discover every other provider_epg *.uk channel that maps to UK EPGShare.
    auto_channels, auto_dropped = _build_auto_uk_groups(usable_streams, uk_epg_channels, previous_states, used_provider_epgs)
    channels.extend(_mark_auto_channel(item) for item in auto_channels)
    dropped.extend(auto_dropped)

    # 3) Auto-discover useful provider_epg *.us channels that map to US EPGShare.
    us_channels, us_dropped = _build_auto_us_extra_groups(usable_streams, epg_data, previous_states, used_provider_epgs)
    channels.extend(us_channels)
    dropped.extend(us_dropped)

    # 4) Curated extras. They may get US EPG if their provider_epg matches the US EPG file.
    channels.extend(_build_extra_groups_with_epg(EXTRA_CHANNELS, usable_streams, epg_data, previous_states))

    # 5) Pattern grouped extras such as one 4K-WC entry containing 4K-WC 1, 2, 3...
    channels.extend(_build_pattern_extra_groups(EXTRA_PATTERNS, usable_streams, previous_states))

    group_order = {
        "Sports": 1,
        "BBC": 2,
        "ITV": 3,
        "Channel 4 & 5": 4,
        "Entertainment": 5,
        "Movies": 6,
        "News": 7,
        "Documentary": 8,
        "Music": 9,
        "Kids": 10,
        "Other UK Channels": 50,
        "US Extras": 80,
        "Non-UK Extras": 99,
    }

    source_rank = {"core_mapping": 1, "auto_uk_epg": 2, "auto_us_epg": 3, "curated_extra": 4, "pattern_extra": 5}
    deduped = {}
    for channel in channels:
        key = channel.get("key")
        if not key:
            continue
        old = deduped.get(key)
        if old is None or source_rank.get(channel.get("catalog_source"), 50) < source_rank.get(old.get("catalog_source"), 50):
            deduped[key] = channel

    channels = list(deduped.values())
    channels.sort(key=lambda item: (group_order.get(item.get("section"), 60), item.get("name", "").lower()))

    return {
        "version": 4,
        "mode": "grouped_plugin_resolver_auto_uk_us_epg",
        "server": normalised_server() if clean(SERVER) else "",
        "output_format": OUTPUT_FORMAT,
        "epg_sources": {region: {"url": source.get("url"), "file": source.get("file")} for region, source in EPG_SOURCES.items()},
        "channels": channels,
        "dropped": dropped,
        "stats": {
            "raw_streams": len(streams),
            "usable_live_streams": len(usable_streams),
            "core_channels": len([c for c in channels if c.get("catalog_source") == "core_mapping"]),
            "auto_uk_channels": len([c for c in channels if c.get("catalog_source") == "auto_uk_epg"]),
            "auto_us_channels": len([c for c in channels if c.get("catalog_source") == "auto_us_epg"]),
            "curated_extra_channels": len([c for c in channels if c.get("catalog_source") == "curated_extra"]),
            "pattern_extra_channels": len([c for c in channels if c.get("catalog_source") == "pattern_extra"]),
            "vod_or_non_live_dropped": max(0, len(streams) - len(usable_streams)),
        },
    }


def write_report_from_catalog(catalog, filtered_epg_stats=None):
    channels = catalog.get("channels", [])
    enabled = _enabled_channels(catalog)
    disabled = [item for item in channels if not item.get("enabled")]
    stats = catalog.get("stats", {}) if isinstance(catalog.get("stats"), dict) else {}
    lines = [
        "FLAM grouped Live TV catalogue report",
        "",
        "Mode: %s" % catalog.get("mode", "unknown"),
        "Total catalogue channels: %s" % len(channels),
        "Enabled channels: %s" % len(enabled),
        "Disabled channels: %s" % len(disabled),
        "Stream variants: %s" % sum(len(item.get("streams", [])) for item in channels),
        "Filtered EPG channels/programmes: %s" % (filtered_epg_stats or "not written"),
        "Stats: %s" % json.dumps(stats, sort_keys=True),
        "Optimisation version: %s" % catalog.get("optimisation_version", OPTIMISATION_VERSION),
        "",
    ]

    timing_lines = _format_timings(catalog.get("timings") or [])
    if timing_lines:
        lines.extend(timing_lines)
        lines.append("")

    current_section = None
    for channel in channels:
        section = channel.get("section") or "Other"
        if section != current_section:
            current_section = section
            lines.append("")
            lines.append("===== %s =====" % section)
        status = "enabled" if channel.get("enabled") else "disabled"
        xmltv_names = ", ".join(channel.get("xmltv_names", [])[:3])
        lines.append(
            "%s | %s | source=%s | streams=%s | xmltv_id=%s | region=%s | xmltv_names=%s | epg_status=%s | epg_score=%s" % (
                channel.get("name"), status, channel.get("catalog_source", ""), len(channel.get("streams", [])),
                channel.get("xmltv_id"), channel.get("epg_region", ""), xmltv_names,
                channel.get("epg_status"), channel.get("epg_match_score")
            )
        )
        for index, stream in enumerate(channel.get("streams", [])[:10], start=1):
            lines.append(
                "    %02d. %s | stream_id=%s | provider_epg=%s | quality=%s | priority=%s | method=%s" % (
                    index, stream.get("name"), stream.get("stream_id"), stream.get("provider_epg"),
                    stream.get("quality"), stream.get("priority_score"), stream.get("match_method")
                )
            )
        if len(channel.get("streams", [])) > 10:
            lines.append("    ... %s more variants" % (len(channel.get("streams", [])) - 10))

    lines.append("")
    lines.append("===== DROPPED / NEEDS REVIEW =====")
    dropped = catalog.get("dropped", [])
    if not dropped:
        lines.append("No dropped channels.")
    else:
        for item in dropped:
            lines.append("%s | %s" % (item.get("name"), item.get("reason")))

    Path(REPORT_FILE).write_text("\n".join(lines) + "\n", encoding="utf-8")


def rebuild_from_catalog(reload_pvr=True):
    timings, mark = _new_timing_tracker()
    catalog = load_catalog()
    mark("Load existing catalogue")
    epg_data = _load_epg_sources(require_uk=True)
    mark("Load cached EPG sources")
    _refresh_catalog_extra_epg_metadata(catalog, epg_data)
    mark("Repair/backfill EPG metadata")
    catalog = load_catalog()
    filtered_epg_stats = _write_filtered_epg_for_catalog(catalog, epg_data)
    mark("Write filtered EPG")
    write_m3u_from_catalog(catalog)
    mark("Write M3U")
    catalog["timings"] = timings
    catalog["optimisation_version"] = OPTIMISATION_VERSION
    write_report_from_catalog(catalog, filtered_epg_stats)
    mark("Write report")
    iptv_simple_settings = update_iptv_simple_paths()
    mark("Update IPTV Simple paths")
    pvr_reload = reload_pvr_manager() if reload_pvr else {"success": False, "message": "PVR reload skipped."}
    mark("Reload PVR" if reload_pvr else "Skip PVR reload")
    catalog["timings"] = timings
    write_report_from_catalog(catalog, filtered_epg_stats)
    enabled = _enabled_channels(catalog)
    return {
        "success": True,
        "playlist": str(Path(OUTPUT_FILE)),
        "epg": str(Path(OUTPUT_EPG_FILE)),
        "report": str(Path(REPORT_FILE)),
        "catalog": str(Path(CATALOG_FILE)),
        "channels": len(enabled),
        "catalog_channels": len(catalog.get("channels", [])),
        "disabled": len(catalog.get("channels", [])) - len(enabled),
        "stream_variants": sum(len(item.get("streams", [])) for item in catalog.get("channels", [])),
        "dropped": len(catalog.get("dropped", [])),
        "filtered_epg": filtered_epg_stats,
        "iptv_simple_settings": iptv_simple_settings,
        "pvr_reload": pvr_reload,
    }


def refresh_epg_only(reload_pvr=True, force=True):
    """Redownload EPG source files and rebuild only IPTV-EPG.xml from the existing catalogue."""
    timings, mark = _new_timing_tracker()
    catalog = load_catalog()
    mark("Load existing catalogue")
    _download_epg_sources()
    mark("Download EPG sources")
    epg_data = _load_epg_sources(require_uk=True)
    mark("Load EPG sources")
    _refresh_catalog_extra_epg_metadata(catalog, epg_data)
    mark("Repair/backfill EPG metadata")
    catalog = load_catalog()
    filtered_epg_stats = _write_filtered_epg_for_catalog(catalog, epg_data)
    mark("Write filtered EPG")
    catalog["timings"] = timings
    catalog["optimisation_version"] = OPTIMISATION_VERSION
    write_report_from_catalog(catalog, filtered_epg_stats)
    mark("Write report")
    pvr_reload = reload_pvr_manager() if reload_pvr else {"success": False, "message": "PVR reload skipped."}
    mark("Reload PVR" if reload_pvr else "Skip PVR reload")
    catalog["timings"] = timings
    write_report_from_catalog(catalog, filtered_epg_stats)
    return {
        "success": True,
        "epg": str(Path(OUTPUT_EPG_FILE)),
        "report": str(Path(REPORT_FILE)),
        "filtered_epg": filtered_epg_stats,
        "pvr_reload": pvr_reload,
    }


def refresh_epg_if_needed(max_age_hours=12, reload_pvr=True):
    """Service.py calls this on Kodi boot. It is safe to skip when not needed."""
    catalog_path = Path(CATALOG_FILE)
    epg_path = Path(OUTPUT_EPG_FILE)
    if not catalog_path.exists():
        return {"success": True, "skipped": True, "reason": "No IPTV catalogue found."}

    if epg_path.exists():
        try:
            import time
            age_seconds = time.time() - epg_path.stat().st_mtime
            if age_seconds < float(max_age_hours) * 3600:
                return {"success": True, "skipped": True, "reason": "EPG is fresh enough."}
        except Exception:
            pass

    try:
        if xbmc is not None and xbmc.Player().isPlaying():
            return {"success": True, "skipped": True, "reason": "Playback is active."}
    except Exception:
        pass

    return refresh_epg_only(reload_pvr=reload_pvr, force=True)


def run_generator(reload_pvr=True):
    timings, mark = _new_timing_tracker()

    maybe_download_live_streams()
    mark("Download/live stream cache")

    input_path = Path(INPUT_JSON)
    if not input_path.exists():
        raise GeneratorError(
            f"Cannot find {INPUT_JSON}. Download failed, or put live_streams.json in the same folder as this script."
        )

    print("Loading and validating live streams JSON...")
    streams = load_and_validate_live_streams(input_path)
    mark("Load and validate live streams")

    _download_epg_sources()
    mark("Download EPG sources")
    print("Loading EPGShare XMLTV sources...")
    epg_data = _load_epg_sources(require_uk=True)
    mark("Load EPG XML sources")

    print("Building grouped Live TV catalogue...")
    catalog = build_channel_catalog(streams, epg_data)
    mark("Build channel catalogue")
    catalog["optimisation_version"] = OPTIMISATION_VERSION
    catalog["timings"] = timings
    save_catalog(catalog)
    mark("Save catalogue")

    print("Writing filtered merged EPG and plugin M3U...")
    filtered_epg_stats = _write_filtered_epg_for_catalog(catalog, epg_data)
    mark("Write filtered EPG")
    write_m3u_from_catalog(catalog)
    mark("Write M3U")

    print("Writing report...")
    catalog["timings"] = timings
    write_report_from_catalog(catalog, filtered_epg_stats)
    mark("Write report")

    iptv_simple_settings = update_iptv_simple_paths()
    mark("Update IPTV Simple paths")
    pvr_reload = reload_pvr_manager() if reload_pvr else {"success": False, "message": "PVR reload skipped."}
    mark("Reload PVR" if reload_pvr else "Skip PVR reload")

    # Rewrite the report once at the end so it includes the final timing rows,
    # including IPTV Simple/PVR work. This does not affect M3U/EPG content.
    catalog["timings"] = timings
    write_report_from_catalog(catalog, filtered_epg_stats)

    enabled = _enabled_channels(catalog)
    print("")
    print(f"Done. Created: {OUTPUT_FILE}")
    print(f"Filtered EPG: {OUTPUT_EPG_FILE}")
    print(f"Catalogue: {CATALOG_FILE}")
    print(f"Report: {REPORT_FILE}")
    print(f"Enabled channels: {len(enabled)}")
    print(f"Catalogue channels: {len(catalog.get('channels', []))}")
    print(f"Stream variants: {sum(len(item.get('streams', [])) for item in catalog.get('channels', []))}")
    print(f"Filtered EPG: {filtered_epg_stats}")

    return {
        "success": True,
        "playlist": str(Path(OUTPUT_FILE)),
        "epg": str(Path(OUTPUT_EPG_FILE)),
        "report": str(Path(REPORT_FILE)),
        "catalog": str(Path(CATALOG_FILE)),
        "channels": len(enabled),
        "catalog_channels": len(catalog.get("channels", [])),
        "disabled": len(catalog.get("channels", [])) - len(enabled),
        "stream_variants": sum(len(item.get("streams", [])) for item in catalog.get("channels", [])),
        "dropped": len(catalog.get("dropped", [])),
        "filtered_epg": filtered_epg_stats,
        "iptv_simple_settings": iptv_simple_settings,
        "pvr_reload": pvr_reload,
        "timings": timings,
    }

# ============================================================================
# FLAM Live TV display-label cleanup v1
#
# User-facing stream labels are now cleaned in a provider-agnostic way:
#   - original provider names stay stored as "name" for matching/debugging
#   - selector uses "display_name" where available
#   - RAW / HEVC / H265 are hidden from the user-facing label
#   - clear quality labels are kept: 4K UHD, FHD, HD, SD
#   - common leading provider/country/source prefixes are removed only from labels
#
# This deliberately does not alter matching logic, stream IDs, EPG IDs, or URLs.
# ============================================================================

try:
    OPTIMISATION_VERSION = str(OPTIMISATION_VERSION) + "+display-label-cleanup-v1"
except Exception:
    OPTIMISATION_VERSION = "display-label-cleanup-v1"


def _display_quality_from_name(value):
    """Return a simple family-friendly quality label derived from provider text.

    This is label-derived only. Providers can mislabel streams, so this should
    not be treated as a guaranteed probed resolution.
    """
    text = _safe_kodi_display_text(value).upper()
    text = text.replace("FULLHD", "FULL HD")
    if re.search(r"(^|[^A-Z0-9])(4K|UHD|2160P?|3840P?)([^A-Z0-9]|$)", text):
        return "4K UHD"
    if re.search(r"(^|[^A-Z0-9])(FHD|FULL\s*HD|1080P?)([^A-Z0-9]|$)", text):
        return "FHD"
    if re.search(r"(^|[^A-Z0-9])(HD|720P?)([^A-Z0-9]|$)", text):
        return "HD"
    if re.search(r"(^|[^A-Z0-9])(SD|576P?|480P?)([^A-Z0-9]|$)", text):
        return "SD"
    return ""


def _remove_leading_provider_prefixes_for_display(value):
    text = clean(value)

    # Remove repeated provider/country/source prefixes commonly used by Xtream
    # providers. This is deliberately display-only and conservative: it targets
    # prefix tokens followed by ':' or '-' at the very start, not words in the
    # real channel name.
    prefix_pattern = re.compile(
        r"^\s*(?:"
        r"UK|US|USA|CA|AU|NZ|IE|IRL|VIP|NOW|LIVE|SPORTS|EVENT|"
        r"FHD|HD|UHD|SD|4K\s*[- ]?\s*WC|WC"
        r")\s*[:\-]+\s*",
        re.I
    )

    for _ in range(4):
        new_text = prefix_pattern.sub("", text).strip()
        if new_text == text:
            break
        text = new_text

    # Remove country hints such as "(US)" or "[UK]" when they are just labels.
    text = re.sub(r"(?i)\s*[\(\[]\s*(UK|US|USA|CA|AU|NZ|IE|IRL)\s*[\)\]]\s*", " ", text)
    return text.strip()


def _clean_variant_base_for_display(value):
    text = _safe_kodi_display_text(value)
    text = _remove_leading_provider_prefixes_for_display(text)

    # Remove technical/provider-quality tokens from the base name. The visible
    # quality is re-added as a simple label by _clean_variant_display_name().
    # Use lookarounds so adjacent quality tokens such as "UHD 3840P"
    # are all removed in one pass without consuming the separator needed by
    # the next match.
    text = re.sub(
        r"(?i)(?<![A-Z0-9])("
        r"4K|UHD|FHD|FULL\s*HD|HD|SD|RAW|HEVC|H265|H\.265|"
        r"3840P?|2160P?|1080P?|720P?|576P?|480P?|"
        r"50FPS|60FPS|25FPS|30FPS"
        r")(?![A-Z0-9])",
        " ",
        text,
    )

    # Remove common decorative provider markers.
    text = text.replace("◉", " ").replace("●", " ").replace("•", " ")
    text = re.sub(r"\s+", " ", text).strip(" :-|")
    return _title_keep_acronyms(text)


def _clean_variant_display_name_from_text(original_name, channel_name=""):
    original_name = clean(original_name)
    channel_name = clean(channel_name)

    quality = _display_quality_from_name(original_name)
    base = _clean_variant_base_for_display(original_name)

    # For normal channel groups, prefer the clean catalogue channel name where
    # the provider variant clearly describes the same channel. For broad event
    # groups like "4K World Cup", keep the more specific feed name from the
    # variant, e.g. "FOX Sports 1 · 4K UHD".
    if channel_name:
        base_compact = compact_text(base)
        channel_compact = compact_text(channel_name)
        if not base or len(base_compact) < 3:
            base = channel_name
        elif channel_compact and (
            channel_compact in base_compact
            or base_compact in channel_compact
        ):
            base = channel_name

    if not base:
        base = channel_name or "Stream"

    if quality:
        # Avoid duplicate labels such as "BBC One 4K · 4K UHD" where the base
        # somehow still contains the same quality wording.
        return "%s · %s" % (base, quality)
    return base


def _clean_variant_display_name(item, channel_name=""):
    return _clean_variant_display_name_from_text(get_stream_name(item), channel_name=channel_name)


def _variant_quality_label(item):
    """Override: keep only simple quality labels in catalogue/report/UI."""
    quality = _display_quality_from_name(get_stream_name(item))
    return quality or "Standard"


def _stream_to_variant(item, wanted=None, method="exact_provider_epg", match_score="exact"):
    """Override: keep original provider name and add a clean display_name.

    Runtime impact is tiny because this runs only when a stream has already
    matched a catalogue channel/extra. It does not perform provider-wide fuzzy
    matching or stream probing.
    """
    wanted = wanted or {}
    channel_name = clean(wanted.get("name") or wanted.get("display_name") or "")

    return {
        "name": get_stream_name(item),                 # original provider name
        "display_name": _clean_variant_display_name(item, channel_name=channel_name),
        "stream_id": str(get_stream_id(item)),
        "provider_epg": get_provider_epg(item),
        "logo": get_logo(item),
        "category": _clean_category(item),
        "quality": _variant_quality_label(item),
        "quality_score": quality_score(dict(item, _group=wanted.get("group", ""))),
        "priority_score": _variant_priority(item, wanted, 0 if match_score == "exact" else match_score),
        "match_method": method,
        "match_score": match_score,
        "output_format": OUTPUT_FORMAT,
    }


def _safe_stream_label(stream, channel_name=""):
    """Return the clean user-facing label for the Kodi stream picker."""
    display = clean(stream.get("display_name"))
    if display:
        return _safe_kodi_display_text(display)
    return _clean_variant_display_name_from_text(stream.get("name") or "Unknown", channel_name=channel_name)


def _build_picker_labels_for_streams(channel, streams):
    """Build clean selector labels and number duplicates without technical noise."""
    channel_name = clean(channel.get("name"))
    base_labels = [_safe_stream_label(stream, channel_name=channel_name) for stream in streams]

    counts = {}
    for label in base_labels:
        counts[label] = counts.get(label, 0) + 1

    seen = {}
    labels = []
    for index, (stream, base) in enumerate(zip(streams, base_labels), start=1):
        if counts.get(base, 0) > 1:
            seen[base] = seen.get(base, 0) + 1
            visible_base = "%s %s" % (base, seen[base])
        else:
            visible_base = base

        label = "%02d. %s" % (index, visible_base)

        # Keep stream ID only as a small grey debugging hint. Do not show RAW,
        # HEVC, provider EPG IDs or other confusing source labels.
        stream_id = clean(stream.get("stream_id"))
        if stream_id:
            label += "  [COLOR grey](ID %s)[/COLOR]" % stream_id
        labels.append(label)
    return labels


def play_channel(channel_key):
    """Override: resolve plugin:// M3U item with clean stream selector labels."""
    import sys
    try:
        import xbmcgui
        import xbmcplugin
    except Exception as error:
        raise GeneratorError("Kodi playback modules unavailable: %s" % str(error))

    catalog = load_catalog()
    channel = None
    for item in catalog.get("channels", []):
        if item.get("key") == channel_key:
            channel = item
            break
    if not channel:
        raise GeneratorError("Live TV channel was not found in the catalogue. Run Generate / Refresh Live TV again.")

    streams = channel.get("streams", [])
    if not streams:
        raise GeneratorError("No stream variants found for %s." % channel.get("name", channel_key))

    if len(streams) == 1:
        chosen = streams[0]
    else:
        labels = _build_picker_labels_for_streams(channel, streams)
        index = xbmcgui.Dialog().select(_safe_kodi_display_text(channel.get("name", "Live TV")), labels)
        if index < 0:
            try:
                xbmcplugin.setResolvedUrl(int(sys.argv[1]), False, xbmcgui.ListItem())
            except Exception:
                pass
            return None
        chosen = streams[index]

    url = _stream_url_for_variant(chosen)
    listitem = xbmcgui.ListItem(path=url)
    listitem.setProperty("IsPlayable", "true")
    try:
        listitem.setMimeType("video/MP2T")
    except Exception:
        pass
    xbmcplugin.setResolvedUrl(int(sys.argv[1]), True, listitem)
    return url

