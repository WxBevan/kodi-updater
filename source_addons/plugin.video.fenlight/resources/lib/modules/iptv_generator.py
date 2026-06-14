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
    },
    {
        "key": "sky_sports_main_event",
        "name": "Sky Sports Main Event",
        "group": "Sports",
        "provider_epg_ids": ["skysportsmainevent.uk"],
        "aliases": ["sky sports main event", "skysp main ev", "skyspmainev", "main event"],
        "epg_aliases": ["skysp main ev", "skyspmainev", "SkySpMainEvHD"],
        "reject": ["box office", "racing", "news", "mix", "action", "plus", "+1"],
    },
    {
        "key": "sky_sports_premier_league",
        "name": "Sky Sports Premier League",
        "group": "Sports",
        "provider_epg_ids": ["skysportspremiereleague.uk"],
        "aliases": ["sky sports premier league", "sky sports pl", "skysp pl", "skysppl"],
        "epg_aliases": ["skysp pl", "skysppl"],
        "reject": ["box office", "racing", "news", "mix", "action", "plus", "+1"],
    },
    {
        "key": "sky_sports_football",
        "name": "Sky Sports Football",
        "group": "Sports",
        "provider_epg_ids": ["skysportsfootball.uk"],
        "aliases": ["sky sports football", "skysp fball", "skyspfball"],
        "epg_aliases": ["skysp fball", "skyspfball"],
        "reject": ["box office", "racing", "news", "mix", "action", "plus", "+1"],
    },
    {
        "key": "sky_sports_cricket",
        "name": "Sky Sports Cricket",
        "group": "Sports",
        "provider_epg_ids": ["skysportscricket.uk"],
        "aliases": ["sky sports cricket", "skysp cricket", "skyspcricket"],
        "epg_aliases": ["skysp cricket", "skyspcricket"],
        "reject": ["box office", "racing", "news", "mix", "action", "plus", "+1"],
    },
    {
        "key": "sky_sports_golf",
        "name": "Sky Sports Golf",
        "group": "Sports",
        "provider_epg_ids": ["skysportsgolf.uk"],
        "aliases": ["sky sports golf", "skysp golf", "skyspgolf"],
        "epg_aliases": ["skysp golf", "skyspgolf"],
        "reject": ["box office", "racing", "news", "mix", "action", "plus", "+1"],
    },
    {
        "key": "sky_sports_f1",
        "name": "Sky Sports F1",
        "group": "Sports",
        "provider_epg_ids": ["skysportsf1.uk"],
        "aliases": ["sky sports f1", "skysp f1", "skyspf1"],
        "epg_aliases": ["skysp f1", "skyspf1"],
        "reject": ["box office", "racing", "news", "mix", "action", "plus", "+1"],
    },
    {
        "key": "sky_sports_tennis",
        "name": "Sky Sports Tennis",
        "group": "Sports",
        "provider_epg_ids": ["skysportstennis.uk"],
        "aliases": ["sky sports tennis", "skysp tennis"],
        "epg_aliases": ["skysp tennis"],
        "reject": ["box office", "racing", "news", "mix", "action", "plus", "+1"],
    },
    {
        "key": "tnt_sports_1",
        "name": "TNT Sports 1",
        "group": "Sports",
        "provider_epg_ids": ["tntsports1.uk"],
        "aliases": ["tnt sports 1", "tnt sport 1"],
        "epg_aliases": ["tnt sports 1"],
        "reject": ["box office", "ultimate", "2", "3", "4", "5", "6", "7", "8", "9", "10"],
    },
    {
        "key": "tnt_sports_2",
        "name": "TNT Sports 2",
        "group": "Sports",
        "provider_epg_ids": ["tntsports2.uk"],
        "aliases": ["tnt sports 2", "tnt sport 2"],
        "epg_aliases": ["tnt sports 2"],
        "reject": ["box office", "ultimate", "1", "3", "4", "5", "6", "7", "8", "9", "10"],
    },
    {
        "key": "tnt_sports_3",
        "name": "TNT Sports 3",
        "group": "Sports",
        "provider_epg_ids": ["tntsports3.uk"],
        "aliases": ["tnt sports 3", "tnt sport 3"],
        "epg_aliases": ["tnt sports 3"],
        "reject": ["box office", "ultimate", "1", "2", "4", "5", "6", "7", "8", "9", "10"],
    },
    {
        "key": "tnt_sports_4",
        "name": "TNT Sports 4",
        "group": "Sports",
        "provider_epg_ids": ["tntsports4.uk"],
        "aliases": ["tnt sports 4", "tnt sport 4"],
        "epg_aliases": ["tnt sports 4"],
        "reject": ["box office", "ultimate", "1", "2", "3", "5", "6", "7", "8", "9", "10"],
    },
    {
        "key": "mutv",
        "name": "MUTV",
        "group": "Sports",
        "provider_epg_ids": ["mutv.uk"],
        "aliases": ["mutv", "man utd tv", "manchester united tv"],
        "epg_aliases": ["mutv"],
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
    },
    {
        "key": "bbc_two",
        "name": "BBC 2",
        "group": "BBC",
        "provider_epg_ids": ["bbc2.uk"],
        "aliases": ["bbc two", "bbc2", "bbc 2"],
        "epg_aliases": ["bbc two hd"],
        "reject": ["one", "three", "four", "parliament", "cbbc", "cbeebies", "alba"],
    },
    {
        "key": "bbc_parliament",
        "name": "BBC Parliament",
        "group": "BBC",
        "provider_epg_ids": ["bbcparliament.uk"],
        "aliases": ["bbc parliament"],
        "epg_aliases": ["bbc parliament"],
    },
    {
        "key": "bbc_one_wales",
        "name": "BBC One Wales",
        "group": "BBC",
        "provider_epg_ids": ["bbconewales.uk"],
        "aliases": ["bbc one wales", "bbc one wal"],
        "epg_aliases": ["bbc one wales", "bbc one wal"],
        "reject": ["two"],
    },

    # ITV core
    {"key": "itv1", "name": "ITV1", "group": "ITV", "provider_epg_ids": ["itv1.uk"], "aliases": ["itv1", "itv 1"], "epg_aliases": ["itv1 hd"], "reject": ["plus 1", "+1", "itv2", "itv3", "itv4"]},
    {"key": "itv2", "name": "ITV2", "group": "ITV", "provider_epg_ids": ["itv2.uk"], "aliases": ["itv2", "itv 2"], "epg_aliases": ["itv2 hd"], "reject": ["plus 1", "+1", "itv1", "itv3", "itv4"]},
    {"key": "itv3", "name": "ITV3", "group": "ITV", "provider_epg_ids": ["itv3.uk"], "aliases": ["itv3", "itv 3"], "epg_aliases": ["itv3 hd"], "reject": ["plus 1", "+1", "itv1", "itv2", "itv4"]},
    {"key": "itv4", "name": "ITV4", "group": "ITV", "provider_epg_ids": ["itv4.uk"], "aliases": ["itv4", "itv 4"], "epg_aliases": ["itv4 hd"], "reject": ["plus 1", "+1", "itv1", "itv2", "itv3"]},

    # Channel 4 / 5 core
    {"key": "channel_4", "name": "Channel 4", "group": "Channel 4 & 5", "provider_epg_ids": ["channel4.uk"], "aliases": ["channel 4"], "epg_aliases": ["channel 4 hd"], "reject": ["plus 1", "+1", "4seven", "film4", "e4", "more4"]},
    {"key": "channel_5", "name": "Channel 5", "group": "Channel 4 & 5", "provider_epg_ids": ["channel5.uk"], "aliases": ["channel 5"], "epg_aliases": ["channel 5 hd"], "reject": ["plus 1", "+1", "5star", "5usa", "5select", "5action"]},
    {"key": "e4", "name": "E4", "group": "Channel 4 & 5", "provider_epg_ids": ["e4.uk"], "aliases": ["e4"], "epg_aliases": ["e4 hd"], "reject": ["plus 1", "+1", "extra"]},

    # Entertainment core
    {"key": "sky_crime", "name": "Sky Crime", "group": "Entertainment", "provider_epg_ids": ["skycrime.uk"], "aliases": ["sky crime"], "epg_aliases": ["sky crime"], "reject": ["plus 1", "+1"]},
    {"key": "sky_comedy", "name": "Sky Comedy", "group": "Entertainment", "provider_epg_ids": ["skycomedy.uk"], "aliases": ["sky comedy"], "epg_aliases": ["sky comedy"], "reject": ["cinema"]},
    {"key": "comedy_central", "name": "Comedy Central", "group": "Entertainment", "provider_epg_ids": ["comedycentral.uk"], "aliases": ["comedy central", "comedycent", "comedycenthd"], "epg_aliases": ["comedy central", "comedycenthd"], "reject": ["plus 1", "+1", "extra", "xtra"]},

    # News
    {"key": "sky_news", "name": "Sky News", "group": "News", "provider_epg_ids": ["skynews.uk"], "aliases": ["sky news"], "epg_aliases": ["sky news"], "reject": ["arabia", "sports"]},
    {"key": "gb_news", "name": "GB News", "group": "News", "provider_epg_ids": ["gbnews.uk"], "aliases": ["gb news"], "epg_aliases": ["gb news"]},
    {"key": "bbc_news", "name": "BBC News", "group": "News", "provider_epg_ids": ["bbcnews.uk"], "aliases": ["bbc news"], "epg_aliases": ["bbc news"]},
    {"key": "bloomberg", "name": "Bloomberg", "group": "News", "provider_epg_ids": ["bloombergtv.uk"], "aliases": ["bloomberg", "bloomberg tv"], "epg_aliases": ["bloomberg"]},

    # Documentary
    {"key": "discovery", "name": "Discovery Channel", "group": "Documentary", "provider_epg_ids": ["discoverychannel.uk"], "aliases": ["discovery channel", "discovery"], "epg_aliases": ["discovery hd"], "reject": ["history", "science", "turbo", "plus 1", "+1"]},
    {"key": "discovery_history", "name": "Discovery History", "group": "Documentary", "provider_epg_ids": ["discoveryhistory.uk"], "aliases": ["discovery history", "disc history"], "epg_aliases": ["disc history"], "reject": ["plus 1", "+1"]},
    {"key": "discovery_science", "name": "Discovery Science", "group": "Documentary", "provider_epg_ids": ["discoveryscience.uk"], "aliases": ["discovery science", "disc science", "disc sci"], "epg_aliases": ["disc science", "disc sci"], "reject": ["plus 1", "+1"]},
    {"key": "animal_planet", "name": "Animal Planet", "group": "Documentary", "provider_epg_ids": ["animalplanet.uk"], "aliases": ["animal planet", "animal plnt"], "epg_aliases": ["animal planet", "animal plnt"], "reject": ["plus 1", "+1"]},
    {"key": "nat_geo", "name": "National Geographic", "group": "Documentary", "provider_epg_ids": ["natgeo.uk"], "aliases": ["national geographic", "nat geo"], "epg_aliases": ["nat geo hd"], "reject": ["wild", "plus 1", "+1"]},
    {"key": "nat_geo_wild", "name": "Nat Geo Wild", "group": "Documentary", "provider_epg_ids": ["natgeowild.uk"], "aliases": ["nat geo wild", "natgeowild", "national geographic wild"], "epg_aliases": ["nat geo wild", "natgeowild"]},
    {"key": "sky_documentaries", "name": "Sky Documentaries", "group": "Documentary", "provider_epg_ids": ["skydocumentaries.uk"], "aliases": ["sky documentaries"], "epg_aliases": ["sky documentaries"]},
    {"key": "sky_history", "name": "Sky History", "group": "Documentary", "provider_epg_ids": ["skyhistory.uk"], "aliases": ["sky history", "skyhistory"], "epg_aliases": ["sky history", "skyhistory"], "reject": ["history 2", "history2", "plus 1", "+1"]},

    # Music
    {"key": "mtv", "name": "MTV", "group": "Music", "provider_epg_ids": ["mtv.uk"], "aliases": ["mtv"], "epg_aliases": ["mtv hd"], "reject": ["80s", "90s", "hits", "music"]},
    {"key": "now_80s", "name": "NOW 80s", "group": "Music", "provider_epg_ids": ["now80s.uk"], "aliases": ["now 80s"], "epg_aliases": ["now 80s"]},
    {"key": "now_90s", "name": "NOW 90s", "group": "Music", "provider_epg_ids": ["now90s.uk"], "aliases": ["now 90s", "now 90s00s"], "epg_aliases": ["now 90s", "now 90s00s"], "reject": ["80s", "70s"]},

    # Kids
    {"key": "cartoon_network", "name": "Cartoon Network", "group": "Kids", "provider_epg_ids": ["cartoonnetwork.uk"], "aliases": ["cartoon network", "cartoon net", "cartoon netwrk"], "epg_aliases": ["cartoon network", "cartoon net", "cartoon netwrk"], "reject": ["plus 1", "+1", "cartoonito"]},
    {"key": "nickelodeon", "name": "Nickelodeon", "group": "Kids", "provider_epg_ids": ["nickelodeon.uk"], "aliases": ["nickelodeon"], "epg_aliases": ["nickelodeon"], "reject": ["plus 1", "+1", "nick jr", "nicktoons"]},
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


if __name__ == "__main__":
    main()
