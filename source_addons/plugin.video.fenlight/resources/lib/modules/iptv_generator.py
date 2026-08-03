# -*- coding: utf-8 -*-
"""FLAM Live TV generator.

Consolidated from the v2.1.85 runtime implementation. Historical override
layers were removed and the active fallback implementations now have explicit
names. Catalogue, delta/reference, EPG, Special Events, selection policy and
staged-commit behaviour are intentionally unchanged.
"""

import copy
import difflib
import hashlib
import gzip
import os
import shutil
import time
import json
import re
import sys
import unicodedata
import urllib.parse
import xml.etree.ElementTree as ET
from pathlib import Path
from functools import lru_cache
from collections import Counter

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
CATALOG_EPG_CACHE_FILE = str(IPTV_OUTPUT_DIR / "IPTV-EPG-Catalog.xml")
OUTPUT_FORMAT = "ts"  # use "m3u8" if you prefer

DOWNLOAD_LIVE_STREAMS = True
# Built dynamically by build_live_streams_url() so SERVER/USERNAME/PASSWORD are validated first.
LIVE_STREAMS_URL = ""

DOWNLOAD_EPG = True
EPG_URL = "https://epgshare01.online/epgshare01/epg_ripper_UK1.xml.gz"
EPG_GZ_FILE = str(IPTV_CACHE_DIR / "epg_ripper_UK1.xml.gz")

# EPG mapping and channel availability are separate decisions. A genuine
# playable channel may be listed and enabled even when no reliable XMLTV match
# exists; in that case it is clearly marked "No EPG" rather than guessed.
REQUIRE_EPG_MATCH = False

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
        "provider_epg_ids": ["tntsports1.uk", "tntsport1.uk"],
        "aliases": ["tnt sports 1", "tnt sport 1"],
        "epg_aliases": ["tnt sports 1"],
        "reject": ["box office", "ultimate", "2", "3", "4", "5", "6", "7", "8", "9", "10"],
        "enabled_default": True,
    },
    {
        "key": "tnt_sports_2",
        "name": "TNT Sports 2",
        "group": "Sports",
        "provider_epg_ids": ["tntsports2.uk", "tntsport2.uk"],
        "aliases": ["tnt sports 2", "tnt sport 2"],
        "epg_aliases": ["tnt sports 2"],
        "reject": ["box office", "ultimate", "1", "3", "4", "5", "6", "7", "8", "9", "10"],
        "enabled_default": True,
    },
    {
        "key": "tnt_sports_3",
        "name": "TNT Sports 3",
        "group": "Sports",
        "provider_epg_ids": ["tntsports3.uk", "tntsport3.uk"],
        "aliases": ["tnt sports 3", "tnt sport 3"],
        "epg_aliases": ["tnt sports 3"],
        "reject": ["box office", "ultimate", "1", "2", "4", "5", "6", "7", "8", "9", "10"],
        "enabled_default": True,
    },
    {
        "key": "tnt_sports_4",
        "name": "TNT Sports 4",
        "group": "Sports",
        "provider_epg_ids": ["tntsports4.uk", "tntsport4.uk"],
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






# =========================
# TEXT MATCHING HELPERS
# =========================

def _normalise_text_uncached(value):
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
    if not epg_channels:
        return None, 0, []

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





# =========================
# DOWNLOAD HELPERS
# =========================

def download_file(url, output_path, description="file"):
    import time
    import requests

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = Path(str(output_path) + ".part")

    try:
        if temp_path.exists():
            temp_path.unlink()
    except Exception as error:
        raise GeneratorError(
            f"Could not prepare the temporary file for {description}.\n"
            f"{type(error).__name__}: {error}"
        ) from error

    headers = {
        "User-Agent": "Mozilla/5.0 (Linux; Android 11; Fire TV) AppleWebKit/537.36 Chrome/120 Safari/537.36",
        "Accept": "*/*",
        "Accept-Encoding": "gzip, deflate",
        "Connection": "close"
    }

    last_error = None

    for attempt in range(1, 4):
        try:
            print(
                f"Downloading {description}, attempt {attempt}/3: "
                f"{redact_url(url)}"
            )

            with requests.get(
                url,
                headers=headers,
                stream=True,
                allow_redirects=True,
                timeout=(15, 120)
            ) as response:
                print(
                    f"{description} response: "
                    f"status={response.status_code}, "
                    f"content-type={response.headers.get('Content-Type', '')}, "
                    f"url={redact_url(response.url)}"
                )

                response.raise_for_status()

                with temp_path.open("wb") as output:
                    for chunk in response.iter_content(chunk_size=131072):
                        if chunk:
                            output.write(chunk)

            if not temp_path.exists() or temp_path.stat().st_size == 0:
                raise GeneratorError(
                    f"Could not download {description}. "
                    f"The server returned an empty file."
                )

            temp_path.replace(output_path)

            print(
                f"Downloaded {description} successfully: "
                f"{output_path.stat().st_size} bytes"
            )

            return

        except Exception as error:
            last_error = error
            safe_error = redact_url(str(error))

            print(
                f"Download attempt {attempt}/3 failed for "
                f"{description}: {type(error).__name__}: {safe_error}"
            )

            try:
                if temp_path.exists():
                    temp_path.unlink()
            except Exception:
                pass

            if attempt < 3:
                time.sleep(attempt * 2)

    safe_error = redact_url(str(last_error))

    raise GeneratorError(
        f"Could not download {description} after 3 attempts. "
        f"Check the URL and connection.\n"
        f"{type(last_error).__name__}: {safe_error}"
    ) from last_error

def maybe_download_live_streams():
    if DOWNLOAD_LIVE_STREAMS:
        validate_login_config()
        print("Downloading live_streams.json...")
        download_file(build_live_streams_url(), INPUT_JSON, description="live streams JSON")




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




# =========================
# OUTPUT WRITERS
# =========================









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
        message = redact_url(str(error).strip())

    except Exception as error:
        message = redact_url(
            f"Unexpected IPTV generator error: "
            f"{type(error).__name__}: {error}"
        )

        try:
            import traceback
            traceback_text = traceback.format_exc()

            if xbmc:
                xbmc.log(
                    "[FLAM IPTV Generator] Unexpected traceback:\n%s"
                    % traceback_text,
                    xbmc.LOGERROR
                )
            else:
                print(traceback_text)
        except Exception:
            pass

    try:
        if xbmc:
            xbmc.log(
                "[FLAM IPTV Generator] %s" % message,
                xbmc.LOGERROR
            )
        else:
            print(
                "[FLAM IPTV Generator] %s" % message
            )
    except Exception:
        pass

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
    """Run the generator directly and return cleanly on success."""
    try:
        result = run_generator()
    except GeneratorError as error:
        message = redact_url(str(error).strip())
    except Exception as error:
        message = redact_url(
            f"Unexpected IPTV generator error: "
            f"{type(error).__name__}: {error}"
        )
        try:
            import traceback
            print(traceback.format_exc())
        except Exception:
            pass
    else:
        return result

    print("")
    print("Could not generate IPTV files.")
    print(message)
    try:
        write_failure_report(message)
        print(f"Failure report written to: {REPORT_FILE}")
    except Exception:
        pass
    return 1


# ============================================================================
# CATALOGUE STORAGE, SELECTION REBUILDS AND PLAYBACK
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




def _load_catalog_raw():
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


_VOLATILE_EVENT_PREFIXES = (
    "live |", "next |", "end |", "ended |", "no event streaming now",
)


def _is_volatile_dynamic_event(item):
    """Identify temporary event-bank rows that are not stable TV channels."""
    provider_epg = clean(get_provider_epg(item)).lower()
    if provider_epg:
        return False

    raw_name = clean(get_stream_name(item))
    lower_name = raw_name.lower()
    normalised = " %s " % normalise_text(raw_name)
    event_terms = (
        " ppv ", " espn plus ppv ", " nfhs ppv ", " soccer ppv ",
        " dazn ppv ", " event replay ", " full event replay ",
        " 8k exclusive ",
    )
    if lower_name.startswith(_VOLATILE_EVENT_PREFIXES) and any(term in normalised for term in event_terms):
        return True
    if any(term in normalised for term in (
        " espn plus ppv ", " nfhs ppv ", " soccer ppv ", " dazn ppv ",
    )):
        return True
    return False


def _extra_match_score(extra, item):
    if is_adult(item) or not get_stream_id(item) or _is_volatile_dynamic_event(item):
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




# ============================================================================
# PROVIDER CHANNEL DISCOVERY
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






def _mark_core_channel(channel):
    if channel:
        channel['catalog_source'] = 'core_mapping'
        channel['default_enabled_reason'] = 'core uk mapping' if channel.get('enabled') else 'core disabled by default'
    return channel






def _clean_streams_for_catalog(streams):
    return [item for item in streams if _is_usable_live_stream(item)]




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


def _fast_epg_for_auto_fuzzy(provider_epg, display_name, variants, epg_fast_index):
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






# repeat the expensive live/VOD filter after auto discovery.



# ============================================================================
# MULTI-REGION EPG, GROUPED EXTRAS AND BOOT-SAFE REFRESH
# ============================================================================

EPG_US_URL = "https://epgshare01.online/epgshare01/epg_ripper_US2.xml.gz"
EPG_US_GZ_FILE = str(IPTV_CACHE_DIR / "epg_ripper_US2.xml.gz")

# Fallback only: if the US-only source is missing/unreadable, use the all-sources
# file internally and still write one small filtered IPTV-EPG.xml for IPTV Simple.
EPG_ALL_URL = "https://epgshare01.online/epgshare01/epg_ripper_ALL_SOURCES1.xml.gz"
EPG_ALL_GZ_FILE = str(IPTV_CACHE_DIR / "epg_ripper_ALL_SOURCES1.xml.gz")
USE_ALL_SOURCES_US_FALLBACK = False

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


def _is_us_epg_id(epg_id):
    return bool(re.search(r"\.us\d*$", clean(epg_id).lower()))


def _strip_us_epg_suffix(epg_id):
    return re.sub(r"\.us\d*$", "", clean(epg_id), flags=re.I)


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
    """Support either a simple channel list or the multi-region EPG dictionary."""
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
    if not _is_us_epg_id(epg):
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
        base = _strip_us_epg_suffix(provider_epg)
        name = re.sub(r"[^a-zA-Z0-9]+", " ", base).strip()
    return _title_keep_acronyms(name)


def _auto_us_key(provider_epg, fallback_name=""):
    base = _strip_us_epg_suffix(provider_epg).lower() or fallback_name.lower()
    base = normalise_text(base)
    base = re.sub(r"[^a-z0-9]+", "_", base).strip("_")
    if not base:
        base = re.sub(r"[^a-z0-9]+", "_", compact_text(fallback_name)).strip("_")
    return "us_%s" % base[:80]


def _pseudo_wanted_for_us(provider_epg, display_name, variants):
    aliases = [display_name, provider_epg, _strip_us_epg_suffix(provider_epg)]
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
        if _is_us_epg_id(epg_id) and epg_id not in provider_ids:
            provider_ids.append(epg_id)

    aliases = [extra.get("name", "")] + extra.get("aliases", []) + hint.get("aliases", [])
    for variant in variants[:10]:
        if variant.get("name"):
            aliases.append(_safe_kodi_display_text(_strip_quality_words(variant.get("name"))))
        if variant.get("provider_epg"):
            aliases.append(variant.get("provider_epg"))
            aliases.append(_strip_us_epg_suffix(variant.get("provider_epg")))

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


def _catalog_epg_enabled_copy(catalog):
    rebuilt = copy.deepcopy(catalog)
    for channel in rebuilt.get("channels", []):
        channel["enabled"] = bool(clean(channel.get("xmltv_id")))
    return rebuilt


def _write_catalog_epg_cache_to_path(catalog, epg_data, path):
    return _write_epg_to_path(_catalog_epg_enabled_copy(catalog), epg_data, path)


def _filter_catalog_epg_cache_to_path(catalog, cache_path, output_path):
    selected_ids = {
        clean(channel.get("xmltv_id"))
        for channel in catalog.get("channels", [])
        if channel.get("enabled") and clean(channel.get("xmltv_id"))
    }
    root = ET.parse(str(cache_path)).getroot()
    output_root = ET.Element(root.tag, dict(root.attrib))
    present_ids = set()
    channel_count = 0
    programme_count = 0

    for child in list(root):
        if child.tag == "channel":
            channel_id = clean(child.get("id"))
            if channel_id in selected_ids:
                output_root.append(child)
                present_ids.add(channel_id)
                channel_count += 1
        elif child.tag == "programme" and clean(child.get("channel")) in selected_ids:
            output_root.append(child)
            programme_count += 1

    missing = selected_ids - present_ids
    if missing:
        raise GeneratorError(
            "The fast EPG cache is missing %s selected channel(s): %s" % (
                len(missing), ", ".join(sorted(missing)[:8])
            )
        )

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temp = Path(str(output_path) + ".tmp")
    ET.ElementTree(output_root).write(str(temp), encoding="utf-8", xml_declaration=True)
    temp.replace(output_path)
    return "%s channels / %s programmes" % (channel_count, programme_count)


def rebuild_from_catalog(reload_pvr=True, catalog_override=None):
    """Rebuild channel choices using the compact all-catalogue EPG cache."""
    timings, mark = _new_timing_tracker()
    catalog = copy.deepcopy(catalog_override) if catalog_override is not None else load_catalog()
    mark("Load existing catalogue")

    stage_dir = Path(IPTV_OUTPUT_DIR) / (".selection_stage_%s_%s" % (os.getpid(), int(time.time() * 1000)))
    staged_catalog = _stage_path(stage_dir, "IPTV-Catalog.json")
    staged_epg = _stage_path(stage_dir, "IPTV-EPG.xml")
    staged_m3u = _stage_path(stage_dir, "IPTV.m3u")
    staged_report = _stage_path(stage_dir, "IPTV-Report.txt")
    staged_cache = _stage_path(stage_dir, "IPTV-EPG-Catalog.xml")
    cache_final = Path(CATALOG_EPG_CACHE_FILE)
    cache_was_rebuilt = False

    try:
        if cache_final.exists():
            try:
                filtered_epg_stats = _filter_catalog_epg_cache_to_path(catalog, cache_final, staged_epg)
                mark("Load/filter catalogue EPG cache")
            except Exception:
                epg_data = _load_epg_sources(require_uk=True)
                mark("Load cached EPG sources")
                filtered_epg_stats = _write_epg_to_path(catalog, epg_data, staged_epg)
                _write_catalog_epg_cache_to_path(catalog, epg_data, staged_cache)
                cache_was_rebuilt = True
                mark("Rebuild catalogue EPG cache")
        else:
            epg_data = _load_epg_sources(require_uk=True)
            mark("Load cached EPG sources")
            filtered_epg_stats = _write_epg_to_path(catalog, epg_data, staged_epg)
            _write_catalog_epg_cache_to_path(catalog, epg_data, staged_cache)
            cache_was_rebuilt = True
            mark("Build catalogue EPG cache")

        _write_m3u_to_path(catalog, staged_m3u, Path(OUTPUT_EPG_FILE))
        mark("Stage M3U")

        report_catalog = copy.deepcopy(catalog)
        report_catalog["timings"] = timings
        report_catalog["optimisation_version"] = HYBRID_OPTIMISATION_VERSION
        _write_catalog_path(staged_catalog, catalog)
        _write_report_to_path(report_catalog, filtered_epg_stats, staged_report)
        mark("Stage catalogue/report")

        streams = None
        try:
            if Path(INPUT_JSON).exists():
                streams = load_and_validate_live_streams(Path(INPUT_JSON))
        except Exception:
            streams = None
        _validate_staged_generation(catalog, streams, staged_catalog, staged_m3u, staged_epg)
        mark("Validate staged output")

        stage_map = {
            Path(OUTPUT_EPG_FILE): staged_epg,
            Path(OUTPUT_FILE): staged_m3u,
            Path(CATALOG_FILE): staged_catalog,
            Path(REPORT_FILE): staged_report,
        }
        if cache_was_rebuilt:
            stage_map[cache_final] = staged_cache
        _commit_staged_files(stage_map)
        mark("Commit channel selection")

        iptv_simple_settings = update_iptv_simple_paths()
        mark("Update IPTV Simple paths")
        pvr_reload = reload_pvr_manager() if reload_pvr else {"success": False, "message": "PVR reload skipped."}
        mark("Reload PVR" if reload_pvr else "Skip PVR reload")

        report_catalog["timings"] = timings
        _atomic_final_report(report_catalog, filtered_epg_stats)

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
            "timings": timings,
            "epg_cache": "rebuilt" if cache_was_rebuilt else "reused",
        }
    finally:
        shutil.rmtree(str(stage_dir), ignore_errors=True)

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
    cache_temp = Path(str(CATALOG_EPG_CACHE_FILE) + ".tmp")
    _write_catalog_epg_cache_to_path(catalog, epg_data, cache_temp)
    cache_temp.replace(Path(CATALOG_EPG_CACHE_FILE))
    mark("Refresh catalogue EPG cache")
    catalog["timings"] = timings
    catalog["optimisation_version"] = HYBRID_OPTIMISATION_VERSION
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



# ============================================================================
# USER-FACING STREAM LABEL CLEANUP
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


# ============================================================================
# REFERENCE/DELTA GENERATION AND ATOMIC OUTPUT
#
# Conservative acceleration layer:
#   * bundled/local references contain no credentials or direct stream URLs
#   * current account data remains authoritative
#   * exact inventory -> fast rebuild from verified reference
#   * small safe changes -> incremental rebuild
#   * uncertain/new channel identities or EPG definition changes -> full matcher
#   * output is staged, validated, and committed atomically per file
# ============================================================================


try:
    from modules import iptv_reference_cache as _iptv_ref
except Exception:
    try:
        import iptv_reference_cache as _iptv_ref
    except Exception:
        _iptv_ref = None

HYBRID_OPTIMISATION_VERSION = "safe-code-optimisations-v1+display-label-cleanup-v1+hybrid-reference-v2"
IPTV_BUNDLED_REFERENCE_FILE = Path(__file__).resolve().parents[2] / "data" / "iptv_reference.json"

# Full matching remains the authoritative fallback when reference reuse is unsafe.

# Cache repeated normalisation.  The original generator called these helpers
# millions of times for the same stream/EPG strings on low-power devices.


@lru_cache(maxsize=65536)
def _normalise_text_cached(value):
    return _normalise_text_uncached(value)


def normalise_text(value):
    return _normalise_text_cached(clean(value))


@lru_cache(maxsize=65536)
def _compact_text_cached(value):
    return re.sub(r"[^a-z0-9]+", "", normalise_text(value))


def compact_text(value):
    return _compact_text_cached(clean(value))


def _safe_load_catalog_file(path):
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8", errors="replace"))
        if isinstance(data, dict) and isinstance(data.get("channels"), list):
            return data
    except Exception:
        pass
    return None


def _hybrid_enabled_states(previous_catalog):
    """Return current choices plus tombstoned choices for temporarily missing channels."""
    if not isinstance(previous_catalog, dict):
        return {}
    states = {
        clean(key): bool(value)
        for key, value in (previous_catalog.get("selection_history") or {}).items()
        if clean(key)
    }
    for item in previous_catalog.get("channels", []):
        key = clean(item.get("key"))
        if key:
            states[key] = bool(item.get("enabled"))
    return states


def _attach_selection_history(catalog, previous_states=None):
    history = {
        clean(key): bool(value)
        for key, value in (previous_states or {}).items()
        if clean(key)
    }
    for channel in catalog.get("channels", []):
        key = clean(channel.get("key"))
        if key:
            history[key] = bool(channel.get("enabled"))
    catalog["selection_history"] = history
    return catalog


def _hybrid_reference_variant(item, channel, method):
    wanted = {
        "name": channel.get("name", ""),
        "group": channel.get("section", ""),
    }
    variant = _stream_to_variant(item, wanted, method=method, match_score="reference")
    if _iptv_ref is not None:
        variant["source_fingerprint"] = _iptv_ref.stream_fingerprint(item)
    return variant




def _merge_duplicate_catalog_channels_by_epg(catalog):
    """Merge only objectively identical catalogue rows.

    A merge requires the same XMLTV id plus either the same normalised display
    name or at least one identical provider stream id.  This safely folds the
    core/auto TNT aliases and curated/auto NFL/MLB duplicates without merging
    ambiguous mappings such as National Geographic vs NatGeo Wild.
    """
    source_rank = {
        "core_mapping": 1,
        "curated_extra": 2,
        "auto_uk_epg": 3,
        "auto_us_epg": 4,
        "pattern_extra": 5,
    }
    merged = []
    merged_count = 0

    for channel in catalog.get("channels", []):
        xmltv_id = clean(channel.get("xmltv_id"))
        name_key = compact_text(_strip_quality_words(channel.get("name", "")))
        stream_ids = {clean(item.get("stream_id")) for item in channel.get("streams", []) if clean(item.get("stream_id"))}
        target_index = None

        if xmltv_id:
            for index, existing in enumerate(merged):
                if clean(existing.get("xmltv_id")) != xmltv_id:
                    continue
                existing_name = compact_text(_strip_quality_words(existing.get("name", "")))
                existing_ids = {clean(item.get("stream_id")) for item in existing.get("streams", []) if clean(item.get("stream_id"))}
                if (name_key and existing_name == name_key) or (stream_ids and existing_ids and stream_ids & existing_ids):
                    target_index = index
                    break

        if target_index is None:
            merged.append(copy.deepcopy(channel))
            continue

        existing = merged[target_index]
        preferred_new = source_rank.get(channel.get("catalog_source"), 50) < source_rank.get(existing.get("catalog_source"), 50)
        preferred = copy.deepcopy(channel if preferred_new else existing)
        other = existing if preferred_new else channel
        preferred["enabled"] = bool(existing.get("enabled") or channel.get("enabled"))
        preferred["streams"] = _unique_sorted_variants(
            list(existing.get("streams", [])) + list(channel.get("streams", []))
        )
        preferred["stream_count"] = len(preferred["streams"])
        if not preferred.get("logo"):
            preferred["logo"] = other.get("logo", "")
        preferred.setdefault("merged_channel_keys", [])
        for key in [existing.get("key"), channel.get("key")]:
            if key and key != preferred.get("key") and key not in preferred["merged_channel_keys"]:
                preferred["merged_channel_keys"].append(key)
        merged[target_index] = preferred
        merged_count += 1

    catalog["channels"] = merged
    stats = dict(catalog.get("stats") or {})
    stats["safe_duplicate_channels_merged"] = merged_count
    stats["core_channels"] = len([c for c in merged if c.get("catalog_source") == "core_mapping"])
    stats["auto_uk_channels"] = len([c for c in merged if c.get("catalog_source") == "auto_uk_epg"])
    stats["auto_us_channels"] = len([c for c in merged if c.get("catalog_source") == "auto_us_epg"])
    stats["curated_extra_channels"] = len([c for c in merged if c.get("catalog_source") == "curated_extra"])
    stats["pattern_extra_channels"] = len([c for c in merged if c.get("catalog_source") == "pattern_extra"])
    catalog["stats"] = stats
    return catalog


def _hybrid_reference_candidates(previous_catalog, diagnostics=None):
    candidates = []
    if _iptv_ref is None:
        return candidates

    if previous_catalog:
        local_meta = previous_catalog.get("reference_meta") or {}
        try:
            local_schema = int(local_meta.get("schema_version") or 0)
        except (TypeError, ValueError, OverflowError):
            local_schema = 0
        if (
            local_schema == _iptv_ref.REFERENCE_SCHEMA_VERSION
            and clean(local_meta.get("logic_version")) == _iptv_ref.REFERENCE_LOGIC_VERSION
        ):
            local_catalog = copy.deepcopy(previous_catalog)
            local_catalog["channels"] = [
                channel for channel in local_catalog.get("channels", [])
                if channel.get("catalog_source") != "dynamic_special"
            ]
            candidates.append({
                "schema_version": _iptv_ref.REFERENCE_SCHEMA_VERSION,
                "logic_version": _iptv_ref.REFERENCE_LOGIC_VERSION,
                "catalog": local_catalog,
                "source_name": "local previous catalogue",
            })
        elif isinstance(diagnostics, list):
            diagnostics.append("local previous catalogue uses an older reference schema or logic version")

    bundled = _iptv_ref.load_reference_file(
        IPTV_BUNDLED_REFERENCE_FILE,
        "bundled reference",
        diagnostics=diagnostics,
    )
    if bundled:
        candidates.append(bundled)
    return candidates



def _migrate_previous_reference_candidate(previous_catalog, stable_streams, epg_data, raw_count):
    """Re-fingerprint a verified older local catalogue for the current cache schema.

    This preserves locally discovered channels across a cache-schema upgrade.
    Dynamic Special Events are excluded because they are always rebuilt live.
    Migration is accepted only when every retained stable variant can be matched
    safely to the current provider response.
    """
    if _iptv_ref is None or not isinstance(previous_catalog, dict):
        return None, ""

    local_meta = previous_catalog.get("reference_meta") or {}
    try:
        local_schema = int(local_meta.get("schema_version") or 0)
    except (TypeError, ValueError, OverflowError):
        local_schema = 0
    if (
        local_schema == _iptv_ref.REFERENCE_SCHEMA_VERSION
        and clean(local_meta.get("logic_version")) == _iptv_ref.REFERENCE_LOGIC_VERSION
    ):
        return None, ""

    old_manifest = Counter((local_meta.get("inventory") or {}).get("manifest") or [])
    if not old_manifest:
        return None, "older local catalogue has no inventory manifest to migrate"

    # Preserve the old manifest boundary under the new fingerprint algorithm.
    # Streams that were not represented by the old manifest stay outside the
    # migrated reference so try_build_from_reference returns them to the normal
    # partial matcher instead of incorrectly calling the migration exact.
    remaining = Counter(old_manifest)
    compatible_streams = []
    for item in stable_streams:
        legacy_fp = _iptv_ref.legacy_v3_stream_fingerprint(item)
        if legacy_fp and remaining.get(legacy_fp, 0) > 0:
            compatible_streams.append(item)
            remaining[legacy_fp] -= 1

    if not compatible_streams:
        return None, "older local inventory could not be mapped to the current fingerprint format"

    migrated = copy.deepcopy(previous_catalog)
    migrated["channels"] = [
        channel for channel in migrated.get("channels", [])
        if channel.get("catalog_source") != "dynamic_special"
    ]
    stable_view = {"channels": migrated.get("channels", [])}
    missing = _iptv_ref.attach_variant_fingerprints(stable_view, stable_streams)
    if missing:
        return None, "older local catalogue could not be safely re-fingerprinted (%s variants unmatched)" % len(missing)

    migrated["reference_meta"] = _iptv_ref.make_reference_meta(
        compatible_streams,
        epg_data,
        raw_count=raw_count,
        server=normalised_server() if clean(SERVER) else "",
        username=USERNAME,
    )
    migrated_fingerprints = set(migrated["reference_meta"]["inventory"].get("manifest") or [])
    for channel in migrated.get("channels", []):
        channel["streams"] = [
            variant for variant in channel.get("streams", [])
            if clean(variant.get("source_fingerprint")) in migrated_fingerprints
        ]
        channel["stream_count"] = len(channel["streams"])
    migrated["reference_meta"]["dynamic_streams_excluded"] = 0
    migrated["reference_meta"]["migration_unresolved_streams"] = max(
        0, len(stable_streams) - len(compatible_streams)
    )
    migrated["server"] = normalised_server() if clean(SERVER) else ""
    migrated["output_format"] = OUTPUT_FORMAT
    return {
        "schema_version": _iptv_ref.REFERENCE_SCHEMA_VERSION,
        "logic_version": _iptv_ref.REFERENCE_LOGIC_VERSION,
        "catalog": migrated,
        "source_name": "migrated local previous catalogue",
    }, ""






def _stage_path(directory, name):
    directory.mkdir(parents=True, exist_ok=True)
    return directory / name


def _write_catalog_path(path, catalog):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(catalog, indent=2, sort_keys=False), encoding="utf-8")


def _write_epg_to_path(catalog, epg_data, path):
    global OUTPUT_EPG_FILE
    original = OUTPUT_EPG_FILE
    try:
        OUTPUT_EPG_FILE = str(path)
        return _write_filtered_epg_for_catalog(catalog, epg_data)
    finally:
        OUTPUT_EPG_FILE = original


def _write_m3u_to_path(catalog, path, final_epg_path):
    global OUTPUT_FILE, OUTPUT_EPG_FILE
    original_output = OUTPUT_FILE
    original_epg = OUTPUT_EPG_FILE
    try:
        OUTPUT_FILE = str(path)
        OUTPUT_EPG_FILE = str(final_epg_path)
        write_m3u_from_catalog(catalog)
    finally:
        OUTPUT_FILE = original_output
        OUTPUT_EPG_FILE = original_epg


def _write_report_to_path(catalog, filtered_epg_stats, path):
    global REPORT_FILE
    original = REPORT_FILE
    try:
        REPORT_FILE = str(path)
        write_report_from_catalog(catalog, filtered_epg_stats)
    finally:
        REPORT_FILE = original


def _validate_staged_generation(catalog, streams, catalog_path, m3u_path, epg_path):
    errors = []
    current_ids = None if streams is None else {str(get_stream_id(item)) for item in streams if get_stream_id(item)}
    keys = set()
    enabled = []
    for channel in catalog.get("channels", []):
        key = clean(channel.get("key"))
        if not key:
            errors.append("channel without key")
            continue
        if key in keys:
            errors.append("duplicate channel key: %s" % key)
        keys.add(key)
        variants = channel.get("streams") or []
        if not variants:
            errors.append("channel without streams: %s" % key)
        for variant in variants:
            stream_id = clean(variant.get("stream_id"))
            if not stream_id or (current_ids is not None and stream_id not in current_ids):
                errors.append("stale/missing stream id %s in %s" % (stream_id, key))
        if channel.get("enabled") and variants:
            enabled.append(channel)

    try:
        staged_catalog = json.loads(Path(catalog_path).read_text(encoding="utf-8"))
        if len(staged_catalog.get("channels", [])) != len(catalog.get("channels", [])):
            errors.append("staged catalogue channel count mismatch")
    except Exception as error:
        errors.append("staged catalogue is unreadable: %s" % error)

    try:
        m3u_text = Path(m3u_path).read_text(encoding="utf-8", errors="replace")
        extinf_count = sum(1 for line in m3u_text.splitlines() if line.startswith("#EXTINF:"))
        plugin_count = sum(1 for line in m3u_text.splitlines() if line.startswith("plugin://"))
        if extinf_count != len(enabled) or plugin_count != len(enabled):
            errors.append("M3U count mismatch: expected %s, got %s/%s" % (len(enabled), extinf_count, plugin_count))
    except Exception as error:
        errors.append("staged M3U is unreadable: %s" % error)

    epg_ids = set()
    try:
        epg_root = ET.parse(str(epg_path)).getroot()
        epg_ids = {clean(node.get("id")) for node in epg_root.findall("channel") if clean(node.get("id"))}
    except Exception as error:
        errors.append("staged EPG is unreadable: %s" % error)

    for channel in enabled:
        xmltv_id = clean(channel.get("xmltv_id"))
        if xmltv_id and xmltv_id not in epg_ids:
            errors.append("enabled channel missing from EPG: %s (%s)" % (channel.get("key"), xmltv_id))

    if not catalog.get("channels"):
        errors.append("catalogue contains no channels")
    if errors:
        raise GeneratorError(
            "Generated files failed validation; the existing working setup was not replaced.\n"
            + "\n".join(errors[:20])
        )
    return {
        "catalog_channels": len(catalog.get("channels", [])),
        "enabled_channels": len(enabled),
        "epg_channels": len(epg_ids),
    }


def _commit_staged_files(stage_map):
    backup_dir = Path(IPTV_OUTPUT_DIR) / ".generation_backup"
    if backup_dir.exists():
        shutil.rmtree(str(backup_dir), ignore_errors=True)
    backup_dir.mkdir(parents=True, exist_ok=True)
    backups = {}
    try:
        for final_path in stage_map:
            final_path = Path(final_path)
            if final_path.exists():
                backup = backup_dir / final_path.name
                shutil.copy2(str(final_path), str(backup))
                backups[final_path] = backup

        for final_path, staged_path in stage_map.items():
            final_path = Path(final_path)
            staged_path = Path(staged_path)
            final_path.parent.mkdir(parents=True, exist_ok=True)
            staged_path.replace(final_path)
    except BaseException:
        for final_path in stage_map:
            final_path = Path(final_path)
            backup = backups.get(final_path)
            try:
                if backup and backup.exists():
                    shutil.copy2(str(backup), str(final_path))
                elif final_path.exists() and final_path not in backups:
                    final_path.unlink()
            except Exception:
                pass
        raise
    finally:
        shutil.rmtree(str(backup_dir), ignore_errors=True)


def _atomic_final_catalog(catalog):
    final = Path(CATALOG_FILE)
    temp = Path(str(final) + ".tmp")
    _write_catalog_path(temp, catalog)
    temp.replace(final)


def _atomic_final_report(catalog, filtered_epg_stats):
    final = Path(REPORT_FILE)
    temp = Path(str(final) + ".tmp")
    _write_report_to_path(catalog, filtered_epg_stats, temp)
    temp.replace(final)




# ============================================================================
# CATEGORY DISCOVERY, NO-EPG CHANNELS AND SPECIAL EVENTS
#
# Final reliability/UX layer:
#   * provider categories are downloaded and cached when available
#   * genuine channels may exist and be enabled without XMLTV data
#   * canonical EPG IDs recover obvious matches such as mtv.uk -> MTV.HD.uk
#   * blank-EPG UK/category channels are still exposed in Manage Channels
#   * active PPV/event-bank rows are rebuilt live under Special Events
#   * dynamic event title changes are excluded from stable reference identity
#   * reference reuse performs a partial match for only unresolved stable rows
# ============================================================================

LIVE_CATEGORIES_FILE = str(IPTV_CACHE_DIR / "live_categories.json")
CURRENT_LIVE_CATEGORY_MAP = {}
CATEGORY_DISCOVERY_SOURCE = "none"
HYBRID_OPTIMISATION_VERSION = (
    "safe-code-optimisations-v1+display-label-cleanup-v1+"
    "delta-reference-v4+no-epg-channels-v1+special-events-v1"
)

# A deliberately small alias table for branding changes that canonical ID
# normalisation cannot infer by itself. Keys/values are XMLTV/provider IDs.
CANONICAL_EPG_ID_ALIASES = {
    "alibi.uk": "U.and.alibi.HD.uk",
    "uktvplayalibi.uk": "U.and.alibi.HD.uk",
}

TRUSTED_NO_EPG_CATEGORY_TERMS = (
    "united kingdom", "uk entertainment", "uk music", "music hd", "music 4k",
    "uk kids", "uk documentary", "uk news", "uk sports", "uk movies",
    "entertainment hd", "documentary hd", "kids hd", "sports hd", "movies hd",
)

DYNAMIC_EVENT_FAMILY_PATTERNS = (
    ("ESPN+", r"(?:\(|\b)(?:US\s*)?ESPN\+\s*0*(\d{1,4})(?:\)|\b)"),
    ("FloSports", r"\bFLSP\s*0*(\d{1,4})\b"),
    ("BTN+", r"\bBTN\+\s*0*(\d{1,4})\b"),
    ("Peacock", r"\bPEACOCK\s*0*(\d{1,4})\b"),
    ("MiLB", r"\bMILB\s*0*(\d{1,4})\b"),
    ("Stan Sport", r"\bSTAN(?:\s+SPORTS?)?\s*0*(\d{1,4})\b"),
    ("MAX PPV", r"\bMAX\s+PPV\s*0*(\d{1,4})\b"),
    ("Apple TV F1 PPV", r"\bAPPLE\s+TV\s+F1\s+PPV\s*0*(\d{1,4})\b"),
    ("Soccer PPV", r"\bSOCCER\s+PPV\s*0*(\d{1,4})\b"),
    ("NFHS PPV", r"\bNFHS\s+PPV\s*0*(\d{1,4})\b"),
    ("DAZN PPV", r"\bDAZN\s+PPV\s*0*(\d{1,4})\b"),
    ("PPV", r"\bPPV(?:\s+EVENT)?\s*0*(\d{1,4})\b"),
)


def build_live_categories_url():
    server = normalised_server()
    query = urllib.parse.urlencode({
        "username": USERNAME,
        "password": PASSWORD,
        "action": "get_live_categories",
    })
    return "%s/player_api.php?%s" % (server, query)


def maybe_download_live_categories():
    """Refresh provider category names without making generation depend on them."""
    if not DOWNLOAD_LIVE_STREAMS:
        return {"downloaded": False, "source": "offline"}
    try:
        validate_login_config()
        download_file(build_live_categories_url(), LIVE_CATEGORIES_FILE, description="live categories JSON")
        return {"downloaded": True, "source": "provider"}
    except Exception as error:
        # Categories improve discovery, but a temporary category-endpoint failure
        # must never prevent a working stream/EPG generation.
        try:
            if xbmc:
                xbmc.log("[FLAM IPTV Generator] Category refresh warning: %s" % redact_url(str(error)), xbmc.LOGWARNING)
        except Exception:
            pass
        return {
            "downloaded": False,
            "source": "cached" if Path(LIVE_CATEGORIES_FILE).exists() else "name inference",
            "warning": redact_url(str(error)),
        }


def _load_live_category_map(path=LIVE_CATEGORIES_FILE):
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8", errors="replace"))
    except Exception:
        return {}
    if not isinstance(payload, list):
        return {}
    result = {}
    for item in payload:
        if not isinstance(item, dict):
            continue
        category_id = clean(item.get("category_id") or item.get("id"))
        category_name = clean(item.get("category_name") or item.get("name"))
        if category_id and category_name:
            result[category_id] = category_name
    return result


def _header_category_label(value):
    text = clean(value)
    if not text or "#" not in text:
        return ""
    text = re.sub(r"#+", " ", text)
    text = _safe_kodi_display_text(text)
    text = re.sub(r"(?i)\b(RAW|HD|FHD|UHD|SD|3840P|2160P)\b", " ", text)
    text = re.sub(r"\s+", " ", text).strip(" -:|")
    return text


def _infer_category_map_from_headers(streams):
    labels = {}
    for item in streams:
        category_id = clean(item.get("category_id"))
        label = _header_category_label(get_stream_name(item))
        if category_id and label:
            labels.setdefault(category_id, set()).add(label)
    # Only trust a header-derived category when the category has one clear label.
    return {key: next(iter(values)) for key, values in labels.items() if len(values) == 1}


def _annotate_stream_categories(streams, provider_map=None):
    global CURRENT_LIVE_CATEGORY_MAP, CATEGORY_DISCOVERY_SOURCE
    provider_map = dict(provider_map or {})
    inferred = _infer_category_map_from_headers(streams)
    merged = dict(inferred)
    merged.update(provider_map)
    CURRENT_LIVE_CATEGORY_MAP = merged
    CATEGORY_DISCOVERY_SOURCE = "provider" if provider_map else ("header inference" if inferred else "none")
    for item in streams:
        if not isinstance(item, dict):
            continue
        category_id = clean(item.get("category_id"))
        category_name = clean(item.get("category_name")) or merged.get(category_id, "")
        if category_name:
            item["_category_name"] = category_name
    return merged


def _clean_category(item):
    return clean(get_field(item, "_category_name", "category_name", "category", "group"))


def _canonical_epg_identity(value):
    text = clean(value).lower()
    text = re.sub(r"\.(uk|us\d*)$", "", text)
    text = unicodedata.normalize("NFKD", text)
    text = text.replace("+", " plus ")
    tokens = re.findall(r"[a-z0-9]+", text)
    ignored = {"hd", "fhd", "uhd", "sd", "raw", "channel", "tv"}
    return "".join(token for token in tokens if token not in ignored)


def _canonical_epg_match(provider_epg, epg_channels):
    provider_epg = clean(provider_epg)
    if not provider_epg:
        return None
    exact = _find_epg_channel_exact([provider_epg], epg_channels)
    if exact:
        return exact

    alias_id = CANONICAL_EPG_ID_ALIASES.get(provider_epg.lower())
    if alias_id:
        exact = _find_epg_channel_exact([alias_id], epg_channels)
        if exact:
            return exact

    wanted = _canonical_epg_identity(provider_epg)
    if not wanted:
        return None
    matches = []
    for channel in epg_channels or []:
        channel_id = clean(channel.get("id"))
        identities = {_canonical_epg_identity(channel_id)}
        identities.update(_canonical_epg_identity(name) for name in channel.get("names", []))
        if wanted in identities:
            matches.append(channel)
    unique = []
    seen = set()
    for channel in matches:
        channel_id = clean(channel.get("id"))
        if channel_id and channel_id not in seen:
            unique.append(channel)
            seen.add(channel_id)
    return unique[0] if len(unique) == 1 else None




def _fast_epg_for_auto(provider_epg, display_name, variants, epg_fast_index):
    epg_channels = [entry.get("channel") for entry in epg_fast_index if entry.get("channel")]
    canonical = _canonical_epg_match(provider_epg, epg_channels)
    if canonical:
        return canonical.get("id", ""), canonical.get("names", []), 5000, "canonical_epg_id", [(5000, canonical)]
    return _fast_epg_for_auto_fuzzy(provider_epg, display_name, variants, epg_fast_index)


def _enabled_default_for_channel(key, section, xmltv_id, is_core=False):
    wanted = CHANNEL_BY_KEY.get(key) or {}
    if wanted.get("enabled_default") is False:
        return False
    if section in {"Kids", "Non-UK Extras", "US Extras"}:
        return False
    if section == "Special Events":
        return True
    # EPG availability is deliberately not used as an enable/disable decision.
    return True


def _channel_default_enabled(group_name, xmltv_id):
    if group_name in {"Kids", "Non-UK Extras", "US Extras"}:
        return False
    return True


def _mark_auto_channel(channel):
    if channel:
        channel["catalog_source"] = "auto_uk_epg" if channel.get("xmltv_id") else "auto_uk_no_epg"
        channel["default_enabled_reason"] = (
            "trusted UK channel enabled by default" if channel.get("enabled") else "disabled by channel classification/user choice"
        )
    return channel


def _build_auto_uk_groups(streams, epg_channels, previous_states, used_provider_epgs):
    """Auto-discover provider *.uk groups even when XMLTV is unavailable."""
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
    review = []
    for provider_epg, items in sorted(buckets.items()):
        variants = []
        pseudo = {"group": "Other UK Channels"}
        for item in items:
            variant = _stream_to_variant(item, pseudo, method="auto_provider_epg_uk", match_score="auto")
            variant["priority_score"] += 80
            variants.append(variant)
        variants = _unique_sorted_variants(variants)
        if not variants:
            continue

        best = _best_variant_for_name(variants)
        best_item = next((item for item in items if str(get_stream_id(item)) == str(best.get("stream_id"))), items[0])
        display_name = _auto_display_name_from_item(best_item)
        xmltv_id, xmltv_names, epg_score, epg_status, alternatives = _fast_epg_for_auto(
            provider_epg, display_name, variants, epg_fast_index
        )
        section = _infer_auto_uk_section(display_name, best_item)
        key = _auto_key_from_provider_epg(provider_epg, display_name)
        default_enabled = _enabled_default_for_channel(key, section, xmltv_id, is_core=False)
        enabled = previous_states.get(key, default_enabled) if key in previous_states else default_enabled
        status = epg_status if xmltv_id else ("epg_ambiguous" if epg_status == "ambiguous" else "epg_not_available")
        channel = _make_channel_record(
            key, display_name, section, variants,
            xmltv_id=xmltv_id, xmltv_names=xmltv_names, epg_score=epg_score,
            enabled=enabled, status=status, previous_states=previous_states,
        )
        channel["catalog_source"] = "auto_uk_epg" if xmltv_id else "auto_uk_no_epg"
        channel["provider_epg_id"] = provider_epg
        if not xmltv_id:
            channel["epg_alternatives"] = alternatives
            review.append({
                "name": display_name,
                "reason": "Included without EPG; no confident EPGShare match for %s" % provider_epg,
                "drop_status": status,
                "epg_alternatives": alternatives,
            })
        channels.append(channel)
        used_provider_epgs.add(provider_epg)
    return channels, review


def _is_header_or_separator(item):
    name = clean(get_stream_name(item))
    if not name:
        return True
    if name.count("#") >= 3:
        return True
    stripped = re.sub(r"[\s#_\-=|:]+", "", name)
    return len(stripped) < 3


def _event_status(item):
    name = clean(get_stream_name(item)).upper().strip()
    if re.match(r"^(ENDED?|END)\s*\|", name):
        return "ended"
    if re.match(r"^LIVE\s*\|", name):
        return "live"
    if re.match(r"^(NEXT|UPCOMING)\s*\|", name):
        return "upcoming"
    normalised = normalise_text(name)
    if any(term in normalised for term in ("no event streaming", "no event scheduled", "offline", "placeholder", "test stream")):
        return "inactive"
    return "active"


def _dynamic_event_family_slot(item):
    raw = clean(get_stream_name(item))
    for family, pattern in DYNAMIC_EVENT_FAMILY_PATTERNS:
        match = re.search(pattern, raw, re.I)
        if match:
            return family, clean(match.group(1)).lstrip("0") or "0"
    category = _clean_category(item)
    category_norm = normalise_text(category)
    if "ppv" in category_norm or "event" in category_norm:
        return _title_keep_acronyms(category), clean(get_stream_id(item))
    return "", ""


def _is_dynamic_event_candidate(item):
    if not _is_usable_live_stream(item) or get_provider_epg(item):
        return False
    if _is_header_or_separator(item):
        return False
    raw = clean(get_stream_name(item))
    normalised = normalise_text("%s %s" % (raw, _clean_category(item)))
    family, _slot = _dynamic_event_family_slot(item)
    if family:
        return True
    if re.match(r"^(LIVE|NEXT|UPCOMING|ENDED?|END)\s*\|", raw, re.I) and any(
        term in normalised for term in ("event", "ppv", "8k exclusive", "espn plus", "flosports", "btn plus")
    ):
        return True
    return any(term in normalised for term in (
        "pay per view", "live event", "main event", "fight night", "box office event",
    ))


def _is_active_dynamic_event(item):
    if not _is_dynamic_event_candidate(item):
        return False
    status = _event_status(item)
    if status in {"ended", "inactive"}:
        return False
    name_norm = normalise_text(get_stream_name(item))
    if any(term in name_norm for term in ("replay", "full event replay", "backup")):
        return False
    return True


def _clean_event_title(item):
    raw = _safe_kodi_display_text(get_stream_name(item))
    status = _event_status(item)
    family, slot = _dynamic_event_family_slot(item)
    parts = [clean(part) for part in raw.split("|")]
    meaningful = []
    for index, part in enumerate(parts):
        if not part:
            continue
        norm = normalise_text(part)
        if index == 0 and norm in {"live", "next", "upcoming", "ended", "end"}:
            continue
        if family and re.search(r"(?:ESPN\+|FLSP|BTN\+|PEACOCK|MILB|STAN|MAX\s+PPV|PPV)\s*0*%s\b" % re.escape(slot or "0"), part, re.I):
            continue
        if re.search(r"(?i)\b(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun)\b.*\bUTC\b", part):
            continue
        if re.search(r"(?i)\b8K\s+EXCLUSIVE\b", part):
            continue
        if re.match(r"(?i)^(UK|US|USA|CA|AU|NZ|VIP|NOW)\s*:", part):
            # Usually the final technical slot label.
            if "PPV" in part.upper() or family:
                continue
        if norm in {"hd", "fhd", "uhd", "4k", "raw", "hevc", "event"}:
            continue
        cleaned = re.sub(r"(?i)\s*\([^)]*\b(?:UK|US)\b[^)]*\)\s*$", "", part).strip()
        cleaned = re.sub(r"(?i)\b(?:FHD|HD|UHD|4K|RAW|HEVC|H265|50FPS|60FPS)\b", " ", cleaned)
        cleaned = re.sub(r"\s+", " ", cleaned).strip(" -:|")
        if cleaned:
            meaningful.append(cleaned)

    if not meaningful:
        text = _clean_variant_base_for_display(raw)
        text = re.sub(r"(?i)\b(?:PPV|PAY\s+PER\s+VIEW|LIVE\s+EVENT)\b", " ", text)
        text = re.sub(r"\s+", " ", text).strip(" -:|")
        meaningful = [text] if text else []
    if not meaningful:
        return ""
    # Keep enough detail to distinguish rounds/teams, without exposing slot noise.
    title = " · ".join(meaningful[:5])
    title = _title_keep_acronyms(title)
    if status == "live":
        return "LIVE: %s" % title
    if status == "upcoming":
        return "Upcoming: %s" % title
    return title






def _trusted_no_epg_category(item):
    category = normalise_text(_clean_category(item))
    if any(term in category for term in TRUSTED_NO_EPG_CATEGORY_TERMS):
        return True
    # Header-derived labels such as "MUSIC" are safe only for UK-prefixed rows.
    name = clean(get_stream_name(item)).upper()
    if any(term in category for term in ("music", "entertainment", "documentary", "kids", "news", "sports", "cinema", "movies")):
        return name.startswith(("UK:", "NOW:", "VIP:"))
    return False


def _stable_channel_name_from_item(item):
    name = _clean_variant_base_for_display(get_stream_name(item))
    name = re.sub(r"(?i)\b(?:SERVER|SOURCE|FEED)\s*\d+\b", " ", name)
    name = re.sub(r"\s+", " ", name).strip(" -:|")
    return name


def _is_stable_no_epg_candidate(item):
    if not _is_usable_live_stream(item) or get_provider_epg(item):
        return False
    if _is_header_or_separator(item) or _is_dynamic_event_candidate(item):
        return False
    if _event_status(item) in {"ended", "inactive"}:
        return False
    return _trusted_no_epg_category(item)


def _add_no_epg_stable_channels(catalog, stable_streams, previous_states):
    channels = catalog.setdefault("channels", [])
    used_ids = {
        clean(variant.get("stream_id"))
        for channel in channels
        for variant in channel.get("streams", [])
        if clean(variant.get("stream_id"))
    }
    name_index = {}
    for channel in channels:
        identity = compact_text(_strip_quality_words(channel.get("name", "")))
        if identity:
            name_index.setdefault(identity, []).append(channel)

    remaining = {}
    attached = 0
    for item in stable_streams:
        stream_id = clean(get_stream_id(item))
        if not stream_id or stream_id in used_ids or not _is_stable_no_epg_candidate(item):
            continue
        display = _stable_channel_name_from_item(item)
        identity = compact_text(_strip_quality_words(display))
        if not identity:
            continue
        targets = name_index.get(identity, [])
        if len(targets) == 1:
            target = targets[0]
            target.setdefault("streams", []).append(
                _stream_to_variant(item, {"name": target.get("name", ""), "group": target.get("section", "")}, method="no_epg_name_attach", match_score="exact_name")
            )
            target["streams"] = _unique_sorted_variants(target["streams"])
            target["stream_count"] = len(target["streams"])
            if not target.get("logo"):
                target["logo"] = get_logo(item)
            used_ids.add(stream_id)
            attached += 1
            continue
        remaining.setdefault(identity, {"name": display, "items": []})["items"].append(item)

    created = 0
    for identity, payload in remaining.items():
        variants = [
            _stream_to_variant(item, {"name": payload["name"], "group": "Other UK Channels"}, method="auto_no_epg_category", match_score="category")
            for item in payload["items"]
        ]
        variants = _unique_sorted_variants(variants)
        if not variants:
            continue
        section = _infer_auto_uk_section(payload["name"], payload["items"][0])
        key = "uk_noepg_%s" % re.sub(r"[^a-z0-9]+", "_", identity.lower()).strip("_")[:70]
        enabled = previous_states.get(key, _enabled_default_for_channel(key, section, "", is_core=False))
        channel = _make_channel_record(
            key, payload["name"], section, variants,
            xmltv_id="", xmltv_names=[], epg_score="none",
            enabled=enabled, status="epg_not_available", previous_states=previous_states,
        )
        channel["catalog_source"] = "auto_no_epg"
        channel["default_enabled_reason"] = "trusted UK channel enabled; EPG unavailable"
        channels.append(channel)
        created += 1

    stats = dict(catalog.get("stats") or {})
    stats["no_epg_variants_attached"] = attached
    stats["no_epg_channels_created"] = created
    catalog["stats"] = stats
    return catalog


def _stable_reference_streams(usable_streams):
    return [item for item in usable_streams if not _is_dynamic_event_candidate(item)]


def _is_potential_catalog_stream(item):
    if _is_dynamic_event_candidate(item):
        return False
    provider_epg = get_provider_epg(item).lower()
    if provider_epg in CORE_PROVIDER_EPG_IDS or provider_epg.endswith(".uk"):
        return True
    if _is_us_epg_id(provider_epg):
        return _is_useful_us_candidate(item)
    if _is_stable_no_epg_candidate(item):
        return True
    search = " ".join([get_stream_name(item), provider_epg, _clean_category(item)])
    search_compact = compact_text(search)
    if "mutv" in search_compact or "manchesterunited" in search_compact:
        return True
    for extra in EXTRA_CHANNELS:
        aliases = [extra.get("name", "")] + extra.get("aliases", [])
        if any(compact_text(alias) and compact_text(alias) in search_compact for alias in aliases):
            if _extra_match_score(extra, item) >= MIN_STREAM_MATCH_SCORE:
                return True
    search_norm = normalise_text(search)
    for pattern_extra in EXTRA_PATTERNS:
        try:
            if re.search(pattern_extra.get("pattern", ""), search_norm, re.I):
                return True
        except Exception:
            continue
    return False


def _sort_catalog_channels(catalog):
    group_order = {
        "Special Events": 0,
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
    catalog["channels"] = sorted(
        catalog.get("channels", []),
        key=lambda item: (group_order.get(item.get("section"), 60), item.get("name", "").lower()),
    )
    return catalog


def _merge_partial_catalog(base_catalog, partial_catalog, previous_states):
    by_key = {clean(channel.get("key")): channel for channel in base_catalog.get("channels", []) if clean(channel.get("key"))}
    for incoming in partial_catalog.get("channels", []):
        key = clean(incoming.get("key"))
        if not key:
            continue
        existing = by_key.get(key)
        if existing is None:
            by_key[key] = copy.deepcopy(incoming)
            continue
        existing["streams"] = _unique_sorted_variants(list(existing.get("streams", [])) + list(incoming.get("streams", [])))
        existing["stream_count"] = len(existing["streams"])
        if incoming.get("xmltv_id") and not existing.get("xmltv_id"):
            for field in ("xmltv_id", "xmltv_names", "epg_match_score", "epg_status", "epg_region"):
                existing[field] = copy.deepcopy(incoming.get(field))
        if not existing.get("logo"):
            existing["logo"] = incoming.get("logo", "")
        if key in previous_states:
            existing["enabled"] = bool(previous_states[key])
    base_catalog["channels"] = list(by_key.values())
    base_catalog.setdefault("dropped", []).extend(partial_catalog.get("dropped", []))
    return base_catalog




def _refresh_catalog_stats(catalog, raw_streams, usable_streams):
    channels = catalog.get("channels", [])
    stats = dict(catalog.get("stats") or {})
    stats.update({
        "raw_streams": len(raw_streams),
        "usable_live_streams": len(usable_streams),
        "core_channels": len([c for c in channels if c.get("catalog_source") == "core_mapping"]),
        "auto_uk_channels": len([c for c in channels if c.get("catalog_source") == "auto_uk_epg"]),
        "auto_uk_no_epg_channels": len([c for c in channels if c.get("catalog_source") in {"auto_uk_no_epg", "auto_no_epg"}]),
        "auto_us_channels": len([c for c in channels if c.get("catalog_source") == "auto_us_epg"]),
        "curated_extra_channels": len([c for c in channels if c.get("catalog_source") == "curated_extra"]),
        "pattern_extra_channels": len([c for c in channels if c.get("catalog_source") == "pattern_extra"]),
        "dynamic_special_channels": len([c for c in channels if c.get("catalog_source") == "dynamic_special"]),
        "channels_without_epg": len([c for c in channels if not c.get("xmltv_id")]),
        "vod_or_non_live_dropped": max(0, len(raw_streams) - len(usable_streams)),
    })
    catalog["stats"] = stats
    return catalog


def _attach_hybrid_reference_meta(catalog, streams, usable_streams, epg_data):
    if _iptv_ref is None:
        return []
    stable_streams = _stable_reference_streams(usable_streams)
    stable_view = {
        "channels": [channel for channel in catalog.get("channels", []) if channel.get("catalog_source") != "dynamic_special"]
    }
    missing = _iptv_ref.attach_variant_fingerprints(stable_view, stable_streams)
    catalog["reference_meta"] = _iptv_ref.make_reference_meta(
        stable_streams,
        epg_data,
        raw_count=len(streams),
        server=normalised_server() if clean(SERVER) else "",
        username=USERNAME,
    )
    catalog["reference_meta"]["dynamic_streams_excluded"] = len(usable_streams) - len(stable_streams)
    return missing


def _build_channel_catalog_delta(streams, epg_data):
    """Delta-reference catalogue with live Special Events and no-EPG channels."""
    previous_catalog = _safe_load_catalog_file(CATALOG_FILE)
    previous_states = _hybrid_enabled_states(previous_catalog)
    usable_streams = _clean_streams_for_catalog(streams)
    stable_streams = _stable_reference_streams(usable_streams)
    dynamic_channels = _build_dynamic_special_events(usable_streams, previous_states)

    fallback_reasons = []
    if _iptv_ref is not None:
        account_fp = _iptv_ref.account_fingerprint(normalised_server() if clean(SERVER) else "", USERNAME)
        incomplete, incomplete_reason = _iptv_ref.suspicious_incomplete(previous_catalog, len(stable_streams), account_fp)
        if incomplete:
            raise GeneratorError("%s Existing working IPTV files were kept unchanged. Try again later." % incomplete_reason)

        epg_signatures = _iptv_ref.build_epg_signatures(epg_data)
        reference_candidates = []
        migrated_candidate, migration_reason = _migrate_previous_reference_candidate(
            previous_catalog,
            stable_streams,
            epg_data,
            len(streams),
        )
        if migrated_candidate:
            reference_candidates.append(migrated_candidate)
        elif migration_reason:
            fallback_reasons.append(migration_reason)
        reference_candidates.extend(
            _hybrid_reference_candidates(previous_catalog, diagnostics=fallback_reasons)
        )

        for payload in reference_candidates:
            result = _iptv_ref.try_build_from_reference(
                payload=payload,
                usable_streams=stable_streams,
                current_epg_meta=epg_signatures,
                previous_enabled_states=previous_states,
                raw_count=len(streams),
                server=normalised_server() if clean(SERVER) else "",
                output_format=OUTPUT_FORMAT,
                is_potential_catalog_stream=_is_potential_catalog_stream,
                variant_factory=_hybrid_reference_variant,
                unique_sort_variants=_unique_sorted_variants,
                allow_partial=True,
                max_partial_items=max(300, int(len(stable_streams) * 0.12)),
            )
            if not result.get("success"):
                fallback_reasons.append("%s: %s" % (payload.get("source_name", "reference"), result.get("reason", "not reusable")))
                continue
            if not isinstance(result.get("catalog"), dict):
                fallback_reasons.append("%s: reference helper returned no catalogue" % payload.get("source_name", "reference"))
                continue

            catalog = result["catalog"]
            # Dynamic rows in an old local catalogue are never reused; they are
            # always rebuilt from the current panel response below.
            catalog["channels"] = [c for c in catalog.get("channels", []) if c.get("catalog_source") != "dynamic_special"]
            unresolved = result.get("unresolved_items") or []
            requires_partial = bool(result.get("requires_partial_merge") or unresolved)
            if requires_partial and not unresolved:
                fallback_reasons.append("%s: partial merge was requested without input records" % payload.get("source_name", "reference"))
                continue
            if unresolved:
                partial = _build_stable_catalog_subset(unresolved, epg_data, previous_states)
                catalog = _merge_partial_catalog(catalog, partial, previous_states)
                catalog["reference_build"]["partial_matched_channels"] = len(partial.get("channels", []))
                catalog["reference_build"]["partial_input_streams"] = len(unresolved)
            catalog = _add_no_epg_stable_channels(catalog, stable_streams, previous_states)
            catalog["channels"].extend(dynamic_channels)
            catalog = _safe_merge_duplicate_catalog_channels(catalog)
            catalog = _sort_catalog_channels(catalog)
            catalog["version"] = 6
            catalog["mode"] = "grouped_plugin_resolver_delta_reference_special_events"
            catalog["optimisation_version"] = HYBRID_OPTIMISATION_VERSION
            catalog["category_source"] = CATEGORY_DISCOVERY_SOURCE
            catalog = _refresh_catalog_stats(catalog, streams, usable_streams)
            catalog = _attach_selection_history(catalog, previous_states)
            missing = _attach_hybrid_reference_meta(catalog, streams, usable_streams, epg_data)
            if not missing:
                return catalog
            fallback_reasons.append("reference fingerprint refresh was incomplete")
            break
    else:
        fallback_reasons.append("reference helper unavailable")

    # Full fallback still excludes volatile event banks from permanent matching;
    # Special Events are rebuilt independently from the fresh provider list.
    catalog = _build_stable_catalog_subset(stable_streams, epg_data, previous_states)
    catalog["channels"].extend(dynamic_channels)
    catalog = _safe_merge_duplicate_catalog_channels(catalog)
    catalog = _sort_catalog_channels(catalog)
    catalog["version"] = 6
    catalog["mode"] = "grouped_plugin_resolver_delta_reference_special_events"
    catalog["optimisation_version"] = HYBRID_OPTIMISATION_VERSION
    catalog["category_source"] = CATEGORY_DISCOVERY_SOURCE
    catalog["reference_build"] = {
        "mode": "full",
        "source": "full stable-channel matcher",
        "fallback_reasons": fallback_reasons[-8:],
        "dynamic_events_built_live": len(dynamic_channels),
    }
    catalog = _refresh_catalog_stats(catalog, streams, usable_streams)
    catalog = _attach_selection_history(catalog, previous_states)
    missing = _attach_hybrid_reference_meta(catalog, streams, usable_streams, epg_data)
    if missing:
        catalog.setdefault("warnings", []).append(
            "%s selected stable variants could not be fingerprinted; the next run may use full matching." % len(missing)
        )
    return catalog


def run_generator(reload_pvr=True):
    """Run staged generation with non-fatal provider-category discovery."""
    timings, mark = _new_timing_tracker()
    stage_dir = Path(IPTV_OUTPUT_DIR) / (".generation_stage_%s_%s" % (os.getpid(), int(time.time() * 1000)))
    final_catalog = Path(CATALOG_FILE)
    final_m3u = Path(OUTPUT_FILE)
    final_epg = Path(OUTPUT_EPG_FILE)
    final_report = Path(REPORT_FILE)

    try:
        maybe_download_live_streams()
        category_result = maybe_download_live_categories()
        mark("Download provider streams/categories")

        input_path = Path(INPUT_JSON)
        if not input_path.exists():
            raise GeneratorError("Cannot find %s. Download failed." % INPUT_JSON)
        streams = load_and_validate_live_streams(input_path)
        provider_categories = _load_live_category_map()
        _annotate_stream_categories(streams, provider_categories)
        mark("Load/validate streams and categories")

        _download_epg_sources()
        mark("Download EPG sources")
        epg_data = _load_epg_sources(require_uk=True)
        mark("Load EPG XML sources")

        catalog = build_channel_catalog(streams, epg_data)
        mark("Build/delta-match channel catalogue")
        catalog["optimisation_version"] = HYBRID_OPTIMISATION_VERSION
        catalog["timings"] = timings
        if category_result.get("warning"):
            catalog.setdefault("warnings", []).append("Provider categories: %s" % category_result.get("warning"))

        staged_catalog = _stage_path(stage_dir, "IPTV-Catalog.json")
        staged_epg = _stage_path(stage_dir, "IPTV-EPG.xml")
        staged_catalog_epg = _stage_path(stage_dir, "IPTV-EPG-Catalog.xml")
        staged_m3u = _stage_path(stage_dir, "IPTV.m3u")
        staged_report = _stage_path(stage_dir, "IPTV-Report.txt")

        _write_catalog_path(staged_catalog, catalog)
        mark("Stage catalogue")
        filtered_epg_stats = _write_epg_to_path(catalog, epg_data, staged_epg)
        mark("Stage filtered EPG")
        _write_catalog_epg_cache_to_path(catalog, epg_data, staged_catalog_epg)
        mark("Stage catalogue EPG cache")
        _write_m3u_to_path(catalog, staged_m3u, final_epg)
        mark("Stage M3U")
        catalog["timings"] = timings
        _write_report_to_path(catalog, filtered_epg_stats, staged_report)
        mark("Stage report")

        validation = _validate_staged_generation(catalog, streams, staged_catalog, staged_m3u, staged_epg)
        mark("Validate staged output")
        _commit_staged_files({
            final_epg: staged_epg,
            Path(CATALOG_EPG_CACHE_FILE): staged_catalog_epg,
            final_m3u: staged_m3u,
            final_catalog: staged_catalog,
            final_report: staged_report,
        })
        mark("Commit generated files")

        iptv_simple_settings = update_iptv_simple_paths()
        mark("Update IPTV Simple paths")
        pvr_reload = reload_pvr_manager() if reload_pvr else {"success": False, "message": "PVR reload skipped."}
        mark("Reload PVR" if reload_pvr else "Skip PVR reload")

        catalog["timings"] = timings
        _atomic_final_catalog(catalog)
        _atomic_final_report(catalog, filtered_epg_stats)
        enabled = _enabled_channels(catalog)
        reference_build = catalog.get("reference_build") or {}
        return {
            "success": True,
            "playlist": str(final_m3u),
            "epg": str(final_epg),
            "report": str(final_report),
            "catalog": str(final_catalog),
            "channels": len(enabled),
            "catalog_channels": len(catalog.get("channels", [])),
            "disabled": len(catalog.get("channels", [])) - len(enabled),
            "stream_variants": sum(len(item.get("streams", [])) for item in catalog.get("channels", [])),
            "dropped": len(catalog.get("dropped", [])),
            "filtered_epg": filtered_epg_stats,
            "iptv_simple_settings": iptv_simple_settings,
            "pvr_reload": pvr_reload,
            "timings": timings,
            "build_mode": reference_build.get("mode", "full"),
            "reference_source": reference_build.get("source", "full matcher"),
            "validation": validation,
        }
    finally:
        shutil.rmtree(str(stage_dir), ignore_errors=True)


# --- Special-event exposure filter ------------------------------------------
# Event-bank rows are still excluded from stable reference identity, but only
# explicit PPV/Strong/event products are exposed in Manage Channels. Routine
# ESPN+/FloSports/BTN+/Peacock schedule banks no longer create thousands of rows.

SPECIAL_EXPOSURE_TERMS = (
    " ppv ", " pay per view ", " 8k exclusive ", " live event ",
    " box office event ", " fight night ", " main card ",
)


def _has_meaningful_event_title(item):
    raw = clean(get_stream_name(item))
    norm = normalise_text(raw)
    if not raw or "2098" in raw:
        return False
    if _event_status(item) in {"ended", "inactive"}:
        return False
    # Empty slot labels such as ':NCAAB 01' or ':Paramount+ 03'.
    stripped = re.sub(r"(?i)[:\-\s]*(?:PPV\s+EVENT|NCAAB|PARAMOUNT\+|MILB|PEACOCK|ESPN\+|FLSP|BTN\+|STAN)\s*0*\d+[:\-\s]*", "", raw)
    stripped = re.sub(r"[\s:|\-]+", "", stripped)
    if len(stripped) < 5:
        return False
    return not any(term in norm for term in ("no event streaming", "no event scheduled", "placeholder", "test stream"))


def _is_exposed_special_event(item):
    if not _is_active_dynamic_event(item) or not _has_meaningful_event_title(item):
        return False
    raw = clean(get_stream_name(item))
    norm = " %s " % normalise_text("%s %s" % (raw, _clean_category(item)))
    family, _slot = _dynamic_event_family_slot(item)
    if any(term in norm for term in SPECIAL_EXPOSURE_TERMS):
        return True
    if family in {"MAX PPV", "Apple TV F1 PPV", "Soccer PPV", "NFHS PPV", "DAZN PPV", "PPV"}:
        return True
    # Provider categories explicitly named PPV are eligible, while ordinary
    # ESPN+/FloSports/Peacock schedule banks stay hidden from Manage Channels.
    return "ppv" in normalise_text(_clean_category(item))


def _special_group_name(item):
    family, _slot = _dynamic_event_family_slot(item)
    category = _title_keep_acronyms(_clean_category(item))
    if family == "MAX PPV":
        return "Strong 8K / MAX PPV"
    if family and family != "PPV":
        return "%s Events" % family
    if category and "ppv" in normalise_text(category):
        return category
    return "PPV Events"


def _unique_special_variants(variants, limit=150):
    unique = {}
    for variant in variants:
        stream_id = clean(variant.get("stream_id"))
        if not stream_id:
            continue
        old = unique.get(stream_id)
        if old is None or int(variant.get("priority_score", 0)) > int(old.get("priority_score", 0)):
            unique[stream_id] = variant
    result = list(unique.values())
    result.sort(key=lambda item: (
        0 if clean(item.get("event_status")) == "live" else 1,
        clean(item.get("event_title")).lower(),
    ))
    return result[:limit]


def _build_dynamic_special_events(streams, previous_states):
    groups = {}
    excluded_overflow = 0
    for item in streams:
        if not _is_exposed_special_event(item):
            continue
        event_title = _clean_event_title(item)
        if not event_title or event_title.startswith("(2098-"):
            continue
        group_name = _special_group_name(item)
        key_base = compact_text(group_name) or "ppv_events"
        key = "special_group_%s" % key_base[:60]
        groups.setdefault(key, {"name": group_name, "items": []})["items"].append((item, event_title))

    channels = []
    for key, payload in groups.items():
        variants = []
        for item, event_title in payload["items"]:
            variant = _stream_to_variant(
                item,
                {"name": payload["name"], "group": "Special Events"},
                method="dynamic_special_event",
                match_score="live",
            )
            family, slot = _dynamic_event_family_slot(item)
            variant.update({
                "display_name": event_title,
                "event_title": event_title,
                "event_status": _event_status(item),
                "event_family": family,
                "event_slot": slot,
                "dynamic_event": True,
            })
            variants.append(variant)
        original_count = len(variants)
        variants = _unique_special_variants(variants)
        excluded_overflow += max(0, original_count - len(variants))
        if not variants:
            continue
        enabled = previous_states.get(key, True) if key in previous_states else True
        channel = _make_channel_record(
            key, payload["name"], "Special Events", variants,
            xmltv_id="", xmltv_names=[], epg_score="none",
            enabled=enabled, status="dynamic_no_epg", previous_states=previous_states,
        )
        channel.update({
            "catalog_source": "dynamic_special",
            "dynamic_event": True,
            "default_enabled_reason": "active special-event group enabled by default",
            "event_count": len(variants),
        })
        channels.append(channel)
    channels.sort(key=lambda channel: channel.get("name", "").lower())
    return channels


# --- Duplicate consolidation for no-EPG discoveries -------------------------


def _safe_merge_duplicate_catalog_channels(catalog):
    catalog = _merge_duplicate_catalog_channels_by_epg(catalog)
    source_rank = {
        "core_mapping": 1,
        "auto_uk_epg": 2,
        "auto_uk_no_epg": 3,
        "auto_no_epg": 4,
        "curated_extra": 5,
        "auto_us_epg": 6,
        "pattern_extra": 7,
        "dynamic_special": 8,
    }
    merged = []
    name_merged = 0
    for channel in catalog.get("channels", []):
        name_key = compact_text(_strip_quality_words(channel.get("name", "")))
        if not name_key or len(name_key) < 3 or channel.get("catalog_source") == "dynamic_special":
            merged.append(copy.deepcopy(channel))
            continue

        target_index = None
        incoming_ids = {clean(v.get("stream_id")) for v in channel.get("streams", []) if clean(v.get("stream_id"))}
        for index, existing in enumerate(merged):
            if existing.get("catalog_source") == "dynamic_special":
                continue
            existing_key = compact_text(_strip_quality_words(existing.get("name", "")))
            if existing_key != name_key:
                continue
            existing_ids = {clean(v.get("stream_id")) for v in existing.get("streams", []) if clean(v.get("stream_id"))}
            same_broad_region = (
                existing.get("section") not in {"US Extras", "Non-UK Extras"}
                and channel.get("section") not in {"US Extras", "Non-UK Extras"}
            )
            if same_broad_region or (incoming_ids & existing_ids):
                target_index = index
                break

        if target_index is None:
            merged.append(copy.deepcopy(channel))
            continue

        existing = merged[target_index]
        # Prefer a row with XMLTV, then the more authoritative catalogue source.
        existing_preference = (0 if existing.get("xmltv_id") else 1, source_rank.get(existing.get("catalog_source"), 50))
        incoming_preference = (0 if channel.get("xmltv_id") else 1, source_rank.get(channel.get("catalog_source"), 50))
        preferred = copy.deepcopy(channel if incoming_preference < existing_preference else existing)
        other = existing if incoming_preference < existing_preference else channel
        preferred["enabled"] = bool(existing.get("enabled") or channel.get("enabled"))
        preferred["streams"] = _unique_sorted_variants(list(existing.get("streams", [])) + list(channel.get("streams", [])))
        preferred["stream_count"] = len(preferred["streams"])
        if not preferred.get("logo"):
            preferred["logo"] = other.get("logo", "")
        preferred.setdefault("merged_channel_keys", [])
        for key in [existing.get("key"), channel.get("key")] + list(existing.get("merged_channel_keys", [])) + list(channel.get("merged_channel_keys", [])):
            if key and key != preferred.get("key") and key not in preferred["merged_channel_keys"]:
                preferred["merged_channel_keys"].append(key)
        merged[target_index] = preferred
        name_merged += 1

    catalog["channels"] = merged
    stats = dict(catalog.get("stats") or {})
    stats["safe_same_name_channels_merged"] = name_merged
    catalog["stats"] = stats
    return catalog


# --- Targeted partial matcher ------------------------------------------------
def _build_stable_catalog_subset(streams, epg_data, previous_states):
    """Match only the supplied changed stable rows without broad core fuzzing."""
    usable = _clean_streams_for_catalog(streams)
    exact_buckets = build_exact_provider_buckets(usable)
    channels = []
    dropped = []
    used_provider_epgs = set()
    uk_epg_channels = _epg_channels_from_data(epg_data, "uk")

    # Core channels are considered only when the changed row still carries one
    # of that core channel's exact provider EPG IDs. This avoids a lone unknown
    # stream being fuzzily assigned to an unrelated core channel.
    for wanted in WANTED_CHANNELS:
        exact_options = []
        for epg_id in wanted.get("provider_epg_ids", []):
            exact_options.extend(exact_buckets.get(clean(epg_id).lower(), []))
        if not exact_options:
            continue
        wanted["epg_region"] = "uk"
        channel, drop = _build_wanted_group(wanted, exact_options, build_exact_provider_buckets(exact_options), uk_epg_channels, previous_states)
        if channel:
            channel = _mark_core_channel(channel)
            channel["epg_region"] = _region_from_epg_id(channel.get("xmltv_id")) if channel.get("xmltv_id") else "uk"
            channels.append(channel)
            used_provider_epgs.update(clean(epg).lower() for epg in wanted.get("provider_epg_ids", []))
        elif drop:
            dropped.append(drop)

    auto_channels, auto_dropped = _build_auto_uk_groups(usable, uk_epg_channels, previous_states, used_provider_epgs)
    channels.extend(_mark_auto_channel(item) for item in auto_channels)
    dropped.extend(auto_dropped)

    us_channels, us_dropped = _build_auto_us_extra_groups(usable, epg_data, previous_states, used_provider_epgs)
    channels.extend(us_channels)
    dropped.extend(us_dropped)

    channels.extend(_build_extra_groups_with_epg(EXTRA_CHANNELS, usable, epg_data, previous_states))
    channels.extend(_build_pattern_extra_groups(EXTRA_PATTERNS, usable, previous_states))

    catalog = {
        "version": 6,
        "mode": "delta_subset",
        "server": normalised_server() if clean(SERVER) else "",
        "output_format": OUTPUT_FORMAT,
        "channels": channels,
        "dropped": dropped,
        "stats": {"raw_streams": len(streams), "usable_live_streams": len(usable)},
    }
    catalog = _add_no_epg_stable_channels(catalog, usable, previous_states)
    catalog = _safe_merge_duplicate_catalog_channels(catalog)
    return _sort_catalog_channels(catalog)



# ============================================================================
# CHANNEL SELECTION POLICY AND VIRTUAL MENU GROUPS
# Focused UK Common defaults, simplified browse groups and hidden movies.
# ============================================================================

IPTV_SELECTION_POLICY_VERSION = 4

# UK Common intentionally combines the established popular UK sports set with
# only the most frequently used terrestrial/news channels.  Other BBC, ITV and
# Channel 4/5 family services remain available under Other UK Channels.
POPULAR_UK_SPORTS_KEYS = frozenset(
    item.get("key") for item in WANTED_CHANNELS
    if item.get("key") and item.get("enabled_default") is True
    and clean(item.get("group")) == "Sports"
)
UK_COMMON_BROADCASTER_KEYS = frozenset({
    "bbc_one", "bbc_two", "uk_bbcnews",
    "itv1", "itv2", "itv3",
    "channel_4", "channel_5",
})
CORE_COMMON_UK_KEYS = frozenset(
    set(POPULAR_UK_SPORTS_KEYS) | set(UK_COMMON_BROADCASTER_KEYS)
)
# Compatibility aliases for external code/tests that imported older names.
COMMON_UK_KEYS = CORE_COMMON_UK_KEYS

UK_COMMON_XMLTV_IDS = frozenset({
    "bbconelonhduk", "bbctwohduk", "bbcnewshduk",
    "itv1hduk", "itv2hduk", "itv3hduk",
    "channel4hduk", "channel5hduk", "channel5uk",
})
UK_COMMON_PROVIDER_EPG_IDS = frozenset({
    "bbc1uk", "bbc2uk", "bbcnewsuk",
    "itv1uk", "itv2uk", "itv3uk",
    "channel4uk", "channel5uk",
})
UK_COMMON_EXACT_NAMES = frozenset({
    "bbc 1", "bbc one", "bbc 2", "bbc two", "bbc news",
    "itv1", "itv 1", "itv2", "itv 2", "itv3", "itv 3",
    "channel 4", "channel 5",
})

# A compact popular North-American sports group.  These remain disabled by
# default; the group exists only to make them easy to find and select.
USA_COMMON_KEYS = frozenset({
    "dazn_1", "uk_dazn1", "dazn_2", "dazn_3", "dazn_4",
    "us_espn", "espn_2", "us_espnnews",
    "us_foxsports1", "us_foxsports2", "us_cbssportsnetwork",
    "us_accnetwork", "us_secnetwork", "us_golfchannel",
    "us_tennischannel", "nba_tv", "nfl_network", "nfl_redzone",
    "nhl_network", "mlb_network", "wwe_network",
})

MENU_SECTION_LABELS = {
    "BBC": "Other UK Channels",
    "ITV": "Other UK Channels",
    "Channel 4 & 5": "Other UK Channels",
    "US Extras": "Other USA Channels",
    "USA Channels": "Other USA Channels",
    "Non-UK Extras": "Other USA Channels",
    "Other Extras": "Other USA Channels",
}

# These are intentionally unavailable in Manage Channels and can never be
# written to the active M3U.  Entertainment channels that happened to be
# classified under the old Movies section are reclassified instead of hidden.
NON_MOVIE_LEGACY_MOVIES_KEYS = frozenset({
    "uk_skycomedy", "uk_skycrime", "uk_skymax", "uk_noepg_skyone",
})

MOVIE_CHANNEL_PATTERNS = (
    r"\bsky\s+cinema\b",
    r"\bfilm\s*4\b",
    r"\btalking\s+pictures\b",
    r"\bmovie(?:s)?\b",
    r"\bcinema\b",
    r"\btcm\b",
    r"\bsky\s+action\b",
)


def _identity_token(value):
    return re.sub(r"[^a-z0-9]+", "", clean(value).lower())


def _is_movie_channel(channel):
    if not isinstance(channel, dict):
        return False
    values = [
        clean(channel.get("key")), clean(channel.get("name")),
        clean(channel.get("xmltv_id")),
    ]
    for stream in (channel.get("streams") or [])[:20]:
        values.extend((
            clean(stream.get("name")), clean(stream.get("display_name")),
            clean(stream.get("provider_epg")),
        ))
    key = clean(channel.get("key"))
    section = clean(channel.get("section"))
    if section == "Movies" and key not in NON_MOVIE_LEGACY_MOVIES_KEYS:
        return True
    text = normalise_text(" ".join(value for value in values if value))
    return any(re.search(pattern, text, flags=re.I) for pattern in MOVIE_CHANNEL_PATTERNS)


def _normalise_catalogue_section(channel):
    """Undo broad legacy sections and keep menu grouping independent."""
    section = clean(channel.get("section")) or "Other UK Channels"
    if section == "Movies" and not _is_movie_channel(channel):
        section = "Other UK Channels"
        channel["section"] = section
    return section


def _is_uk_common_broadcaster(channel):
    key = clean(channel.get("key"))
    if key in UK_COMMON_BROADCASTER_KEYS:
        return True

    if _identity_token(channel.get("xmltv_id")) in UK_COMMON_XMLTV_IDS:
        return True

    for stream in (channel.get("streams") or [])[:40]:
        if _identity_token(stream.get("provider_epg")) in UK_COMMON_PROVIDER_EPG_IDS:
            return True

    name = normalise_text(clean(channel.get("name"))).lower()
    name = re.sub(r"\b(?:hd|fhd|uhd|4k|8k|raw|hevc)\b", " ", name)
    name = re.sub(r"\s+", " ", name).strip()
    return name in UK_COMMON_EXACT_NAMES


def _is_common_uk_channel(channel):
    if not isinstance(channel, dict) or _is_movie_channel(channel):
        return False
    if channel.get("dynamic_event") or clean(channel.get("section")) == "Special Events":
        return False
    key = clean(channel.get("key"))
    return key in POPULAR_UK_SPORTS_KEYS or _is_uk_common_broadcaster(channel)


def _is_uk_broadcaster_family(channel):
    if not isinstance(channel, dict) or _is_movie_channel(channel):
        return False
    section = clean(channel.get("section"))
    if section in ("BBC", "ITV", "Channel 4 & 5"):
        return True
    key = clean(channel.get("key")).lower()
    if re.match(r"^(?:bbc|uk_bbc|itv|uk_itv|citv|uk_citv)", key):
        return True
    if key in {
        "channel_4", "channel_5", "uk_4seven", "uk_e4", "uk_more4",
        "uk_5star", "uk_5usa", "uk_5select", "uk_5action",
    }:
        return True
    name = normalise_text(clean(channel.get("name"))).lower()
    return bool(re.match(
        r"^(?:bbc\b|itv\b|citv\b|channel\s+[45]\b|e4\b|more4\b|4seven\b|5star\b|5usa\b|5select\b|5action\b)",
        name,
    ))


def _menu_groups_for_channel(channel):
    if _is_movie_channel(channel):
        return []
    key = clean(channel.get("key"))
    section = _normalise_catalogue_section(channel)
    section_group = MENU_SECTION_LABELS.get(section, section)
    common_us = key in USA_COMMON_KEYS
    groups = []
    if _is_common_uk_channel(channel):
        groups.append("UK Common")
    if common_us:
        groups.append("USA Common")

    # Other USA Channels is deliberately exclusive of USA Common.  This keeps
    # DAZN 2 and other popular US rows from appearing in two adjacent US groups.
    if not (common_us and section_group == "Other USA Channels"):
        groups.append(section_group)
    if _is_uk_broadcaster_family(channel) and "Other UK Channels" not in groups:
        groups.append("Other UK Channels")

    output = []
    for group in groups:
        if group and group not in ("Movies", "Other Extras", "USA Channels") and group not in output:
            output.append(group)
    return output


def _catalog_selection_states(catalog):
    if not isinstance(catalog, dict):
        return {}
    try:
        policy_version = int(catalog.get("selection_policy_version") or 0)
    except (TypeError, ValueError, OverflowError):
        policy_version = 0
    if policy_version != IPTV_SELECTION_POLICY_VERSION:
        return {}
    states = {
        clean(key): bool(value)
        for key, value in (catalog.get("selection_history") or {}).items()
        if clean(key)
    }
    for channel in catalog.get("channels", []):
        key = clean(channel.get("key"))
        if key and not _is_movie_channel(channel):
            states[key] = bool(channel.get("enabled"))
    return states


def _decorate_catalog_v9(catalog, previous_catalog=None):
    """Apply the focused UK Common defaults and simplified virtual groups.

    Policy v4 migrates older catalogues once.  Only popular UK sports plus BBC
    One, BBC Two, BBC News, ITV1-3, Channel 4 and Channel 5 start selected.
    Other broadcaster-family channels remain selectable under Other UK
    Channels.  Movie channels remain forced off and hidden.  Once a v4
    selection is saved, explicit user choices are preserved across generation.
    """
    if not isinstance(catalog, dict):
        return catalog

    previous_states = _catalog_selection_states(previous_catalog)
    try:
        previous_policy = int((previous_catalog or {}).get("selection_policy_version") or 0)
    except (TypeError, ValueError, OverflowError):
        previous_policy = 0
    migrated = bool(previous_catalog) and previous_policy != IPTV_SELECTION_POLICY_VERSION

    group_counts = {}
    group_enabled_counts = {}
    selectable_count = 0
    movie_hidden_count = 0
    common_count = 0

    for channel in catalog.get("channels", []):
        key = clean(channel.get("key"))
        movie_hidden = _is_movie_channel(channel)
        channel["movie_channel"] = bool(movie_hidden)
        channel["user_selectable"] = not movie_hidden

        if movie_hidden:
            movie_hidden_count += 1
            channel["enabled"] = False
            channel["menu_groups"] = []
            channel["common_uk"] = False
            channel["common_us"] = False
            channel["default_enabled_reason"] = "movie channel excluded from Live TV selection"
            channel["selection_excluded_reason"] = "movie_channel"
            continue

        selectable_count += 1
        groups = _menu_groups_for_channel(channel)
        common_uk = _is_common_uk_channel(channel)
        channel["menu_groups"] = groups
        channel["common_uk"] = common_uk
        channel["common_us"] = key in USA_COMMON_KEYS
        channel.pop("selection_excluded_reason", None)
        if common_uk:
            common_count += 1

        if key in previous_states:
            enabled = previous_states[key]
        else:
            enabled = common_uk
        channel["enabled"] = bool(enabled)
        if common_uk:
            channel["default_enabled_reason"] = "UK Common channel"
        elif channel.get("xmltv_id"):
            channel["default_enabled_reason"] = "available; disabled until selected"
        else:
            channel["default_enabled_reason"] = "available without EPG; disabled until selected"

        for group in groups:
            group_counts[group] = group_counts.get(group, 0) + 1
            if channel["enabled"]:
                group_enabled_counts[group] = group_enabled_counts.get(group, 0) + 1

    catalog["selection_policy_version"] = IPTV_SELECTION_POLICY_VERSION
    catalog["menu_group_version"] = 3
    catalog["menu_groups"] = [
        {
            "name": name,
            "channel_count": group_counts.get(name, 0),
            "enabled_count": group_enabled_counts.get(name, 0),
        }
        for name in (
            "UK Common", "USA Common", "Sports", "News",
            "Documentary", "Kids", "Music", "Other UK Channels",
            "Special Events", "Other USA Channels",
        )
        if group_counts.get(name, 0)
    ]
    stats = dict(catalog.get("stats") or {})
    stats["menu_groups"] = len(catalog.get("menu_groups", []))
    stats["default_common_channels"] = common_count
    stats["selectable_channels"] = selectable_count
    stats["movie_channels_hidden"] = movie_hidden_count
    stats["enabled_channels"] = len([
        channel for channel in catalog.get("channels", []) if channel.get("enabled")
    ])
    catalog["stats"] = stats
    if migrated:
        catalog["selection_policy_migration"] = (
            "older defaults reset once to focused UK Common; broadcaster groups "
            "moved to Other UK Channels; US groups simplified"
        )
    catalog = _attach_selection_history(catalog, previous_states)
    return catalog


# grouping remains outside the reference matcher, so changing browse groups or
# Common membership does not require rebuilding the bundled reference.


def build_channel_catalog(streams, epg_data):
    previous_catalog = _safe_load_catalog_file(CATALOG_FILE)
    catalog = _build_channel_catalog_delta(streams, epg_data)
    catalog = _decorate_catalog_v9(catalog, previous_catalog=previous_catalog)
    catalog["version"] = 9
    catalog["mode"] = "grouped_plugin_resolver_delta_reference_special_events_grouped_ui_v9"
    return catalog




def load_catalog():
    catalog = _load_catalog_raw()
    # The manager can apply the policy immediately to an older catalogue before
    # Generate / Refresh is run.  It is persisted on Save or the next success.
    return _decorate_catalog_v9(catalog, previous_catalog=catalog)


def update_catalog_enabled_states(enabled_keys):
    catalog = load_catalog()
    enabled_keys = set(clean(item) for item in enabled_keys if clean(item))
    for item in catalog.get("channels", []):
        item["enabled"] = (
            bool(item.get("user_selectable", True))
            and not _is_movie_channel(item)
            and clean(item.get("key")) in enabled_keys
        )
    catalog["selection_policy_version"] = IPTV_SELECTION_POLICY_VERSION
    catalog["selection_policy_migration"] = "user selection saved"
    catalog = _attach_selection_history(catalog, _hybrid_enabled_states(catalog))
    # Rebuild and validate the catalogue, M3U and EPG together before saving.
    return rebuild_from_catalog(reload_pvr=True, catalog_override=catalog)

if __name__ == "__main__":
    sys.exit(main() or 0)
