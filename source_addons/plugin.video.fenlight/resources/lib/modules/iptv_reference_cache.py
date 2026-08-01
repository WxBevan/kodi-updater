# -*- coding: utf-8 -*-
"""Conservative reference-cache support for the FLAM IPTV generator.

The cache never stores credentials or final stream URLs.  It stores only a
manifest of provider stream identities and a previously verified channel
catalogue.  Exact or narrowly compatible changes can be reused; uncertain
changes are deliberately rejected so the caller can run the full generator.
"""

from __future__ import absolute_import

import copy
import hashlib
import json
import re
import unicodedata
from pathlib import Path

REFERENCE_SCHEMA_VERSION = 1
REFERENCE_LOGIC_VERSION = "flam-iptv-hybrid-reference-v2"


def _clean(value):
    if value is None:
        return ""
    return str(value).strip()


def _normalise(value):
    text = unicodedata.normalize("NFKC", _clean(value)).lower()
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _name_words(value):
    text = unicodedata.normalize("NFKD", _normalise(value))
    text = re.sub(r"[^a-z0-9]+", " ", text)
    ignored = {
        "hd", "fhd", "uhd", "sd", "raw", "hevc", "h265", "4k", "2160p",
        "1080p", "720p", "50fps", "60fps", "uk", "us", "usa", "vip", "now",
        "live", "tv", "channel",
    }
    return [word for word in text.split() if word and word not in ignored]


def _category_identity(item):
    values = []
    category_ids = item.get("category_ids")
    if isinstance(category_ids, (list, tuple, set)):
        values.extend(_clean(value) for value in category_ids if _clean(value))
    for key in ("category_id", "category_name", "category", "group"):
        value = _clean(item.get(key))
        if value:
            values.append(value)
    return "|".join(sorted(set(values)))


def stream_identity_text(item):
    """Stable provider identity used for change detection.

    Logos and timestamps are intentionally excluded.  A logo refresh should not
    force a full rematch; rebuilt variants still copy the current live logo.
    """
    parts = [
        _clean(item.get("stream_id") or item.get("id")),
        _normalise(item.get("name") or item.get("stream_name")),
        _normalise(item.get("epg_channel_id") or item.get("provider_epg") or item.get("tvg_id")),
        _category_identity(item),
        _normalise(item.get("stream_type") or item.get("type")),
        "1" if bool(item.get("is_adult")) else "0",
    ]
    return "\x1f".join(parts)


def stream_fingerprint(item):
    return hashlib.sha256(stream_identity_text(item).encode("utf-8", "replace")).hexdigest()[:32]


def build_inventory_meta(usable_streams):
    fingerprints = sorted(stream_fingerprint(item) for item in usable_streams)
    digest = hashlib.sha256("\n".join(fingerprints).encode("ascii")).hexdigest()
    return {
        "algorithm": "sha256-trunc128-stream-identity-v1",
        "usable_count": len(usable_streams),
        "signature": digest,
        "manifest": fingerprints,
    }


def _epg_channel_record(channel):
    channel_id = _normalise(channel.get("id"))
    names = sorted(_normalise(name) for name in channel.get("names", []) if _normalise(name))
    return channel_id + "\x1f" + "\x1e".join(names)


def build_epg_signatures(epg_data):
    signatures = {}
    if not isinstance(epg_data, dict):
        epg_data = {"uk": {"channels": epg_data or []}}
    for region, entry in epg_data.items():
        if not isinstance(entry, dict):
            continue
        channels = entry.get("channels") or []
        records = sorted(_epg_channel_record(channel) for channel in channels if _clean(channel.get("id")))
        signatures[region] = {
            "channel_count": len(records),
            "signature": hashlib.sha256("\n".join(records).encode("utf-8", "replace")).hexdigest(),
        }
    return signatures


def account_fingerprint(server, username):
    server = _normalise(server).rstrip("/")
    username = _normalise(username)
    if not server or not username:
        return ""
    return hashlib.sha256((server + "\x1f" + username).encode("utf-8", "replace")).hexdigest()[:24]


def make_reference_meta(usable_streams, epg_data, raw_count, server, username):
    return {
        "schema_version": REFERENCE_SCHEMA_VERSION,
        "logic_version": REFERENCE_LOGIC_VERSION,
        "inventory": build_inventory_meta(usable_streams),
        "epg": build_epg_signatures(epg_data),
        "raw_count": int(raw_count),
        "account_fingerprint": account_fingerprint(server, username),
    }


def attach_variant_fingerprints(catalog, usable_streams):
    by_id = {}
    by_triplet = {}
    for item in usable_streams:
        stream_id = _clean(item.get("stream_id") or item.get("id"))
        fp = stream_fingerprint(item)
        by_id.setdefault(stream_id, []).append((fp, item))
        triplet = (
            stream_id,
            _normalise(item.get("name") or item.get("stream_name")),
            _normalise(item.get("epg_channel_id") or item.get("provider_epg") or item.get("tvg_id")),
        )
        by_triplet.setdefault(triplet, []).append((fp, item))

    missing = []
    for channel in catalog.get("channels", []):
        for variant in channel.get("streams", []):
            triplet = (
                _clean(variant.get("stream_id")),
                _normalise(variant.get("name")),
                _normalise(variant.get("provider_epg")),
            )
            matches = by_triplet.get(triplet) or by_id.get(triplet[0]) or []
            if len(matches) == 1:
                variant["source_fingerprint"] = matches[0][0]
            elif matches:
                # Prefer matching provider EPG/name when the provider duplicated an ID.
                selected = None
                for fp, item in matches:
                    if (
                        _normalise(item.get("name")) == triplet[1]
                        and _normalise(item.get("epg_channel_id")) == triplet[2]
                    ):
                        selected = fp
                        break
                if selected:
                    variant["source_fingerprint"] = selected
                else:
                    missing.append((channel.get("key"), triplet[0]))
            else:
                missing.append((channel.get("key"), triplet[0]))
    return missing


def reference_payload_from_catalog(catalog):
    return {
        "schema_version": REFERENCE_SCHEMA_VERSION,
        "logic_version": REFERENCE_LOGIC_VERSION,
        "catalog": copy.deepcopy(catalog),
    }


def load_reference_file(path, source_name):
    path = Path(path)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except Exception:
        return None

    if isinstance(data, dict) and isinstance(data.get("catalog"), dict):
        payload = data
    elif isinstance(data, dict) and isinstance(data.get("channels"), list):
        payload = reference_payload_from_catalog(data)
    else:
        return None

    catalog = payload.get("catalog") or {}
    meta = catalog.get("reference_meta") or payload.get("reference_meta") or {}
    if int(meta.get("schema_version") or payload.get("schema_version") or 0) != REFERENCE_SCHEMA_VERSION:
        return None
    if _clean(meta.get("logic_version") or payload.get("logic_version")) != REFERENCE_LOGIC_VERSION:
        return None
    payload["source_name"] = source_name
    return payload


def _epg_meta_matches(reference_meta, current_epg_meta):
    reference_epg = reference_meta.get("epg") or {}
    for region in ("uk", "us"):
        current = current_epg_meta.get(region)
        reference = reference_epg.get(region)
        if current is None and reference is None:
            continue
        if not current or not reference:
            return False
        if _clean(current.get("signature")) != _clean(reference.get("signature")):
            return False
    return True


def _channel_name_compatible(item_name, channel, reference_names):
    item_words = set(_name_words(item_name))
    candidates = [_clean(channel.get("name"))] + list(reference_names)
    best = 0.0
    for candidate in candidates:
        words = set(_name_words(candidate))
        if not words or not item_words:
            continue
        overlap = len(words & item_words)
        score = overlap / float(max(1, min(len(words), len(item_words))))
        if score > best:
            best = score
        compact_item = "".join(item_words)
        compact_candidate = "".join(words)
        if compact_candidate and (compact_candidate in compact_item or compact_item in compact_candidate):
            best = max(best, 0.9)
    return best >= 0.66


def _current_variant_fields(item):
    return {
        "name": _clean(item.get("name") or item.get("stream_name")),
        "stream_id": _clean(item.get("stream_id") or item.get("id")),
        "provider_epg": _clean(item.get("epg_channel_id") or item.get("provider_epg") or item.get("tvg_id")).lower(),
        "logo": _clean(item.get("stream_icon") or item.get("logo") or item.get("icon")),
        "category": _clean(item.get("category_name") or item.get("category") or item.get("group")),
        "source_fingerprint": stream_fingerprint(item),
    }


def _refresh_reference_variant(reference_variant, current_item, output_format):
    variant = copy.deepcopy(reference_variant)
    variant.update(_current_variant_fields(current_item))
    variant["output_format"] = output_format
    return variant


def _reference_catalog(payload):
    catalog = payload.get("catalog") or {}
    if not isinstance(catalog.get("channels"), list):
        return None
    return catalog


def try_build_from_reference(
    payload,
    usable_streams,
    current_epg_meta,
    previous_enabled_states,
    raw_count,
    server,
    output_format,
    is_potential_catalog_stream,
    variant_factory,
    unique_sort_variants,
):
    """Return a conservative reused catalogue or a reason to run full matching."""
    catalog = _reference_catalog(payload)
    if catalog is None:
        return {"success": False, "reason": "invalid reference catalogue"}
    reference_meta = catalog.get("reference_meta") or payload.get("reference_meta") or {}
    inventory = reference_meta.get("inventory") or {}
    reference_manifest = set(inventory.get("manifest") or [])
    if not reference_manifest:
        return {"success": False, "reason": "reference has no inventory manifest"}
    if not _epg_meta_matches(reference_meta, current_epg_meta):
        return {"success": False, "reason": "EPG channel definitions changed"}

    current_by_fp = {}
    current_manifest = set()
    for item in usable_streams:
        fp = stream_fingerprint(item)
        current_manifest.add(fp)
        current_by_fp.setdefault(fp, []).append(item)

    exact_inventory = _clean(inventory.get("signature")) == _clean(
        hashlib.sha256("\n".join(sorted(current_manifest)).encode("ascii")).hexdigest()
    )
    overlap = len(reference_manifest & current_manifest) / float(max(1, len(reference_manifest)))
    if not exact_inventory and overlap < 0.50:
        return {"success": False, "reason": "provider inventory differs too much", "overlap": overlap}

    reference_provider_channels = {}
    channel_reference_names = {}
    reference_variant_fps = set()
    for channel in catalog.get("channels", []):
        names = []
        for variant in channel.get("streams", []):
            fp = _clean(variant.get("source_fingerprint"))
            if fp:
                reference_variant_fps.add(fp)
            if variant.get("name"):
                names.append(variant.get("name"))
            provider_epg = _normalise(variant.get("provider_epg"))
            if provider_epg:
                reference_provider_channels.setdefault(provider_epg, set()).add(_clean(channel.get("key")))
        channel_reference_names[_clean(channel.get("key"))] = names

    rebuilt = copy.deepcopy(catalog)
    rebuilt_channels = []
    assigned_current_fps = set()
    channels_by_key = {}

    # First retain every exactly matching referenced variant.
    for channel in rebuilt.get("channels", []):
        key = _clean(channel.get("key"))
        retained = []
        for variant in channel.get("streams", []):
            fp = _clean(variant.get("source_fingerprint"))
            matches = current_by_fp.get(fp) if fp else None
            if not matches:
                continue
            current_item = matches[0]
            retained.append(_refresh_reference_variant(variant, current_item, output_format))
            assigned_current_fps.add(fp)
        channel["streams"] = retained
        channel["stream_count"] = len(retained)
        channels_by_key[key] = channel

    # Then consider only newly changed/added live records.  They may be attached
    # incrementally when one provider EPG maps to one reference channel and the
    # channel name is still compatible.  Anything else relevant forces full mode.
    unsafe = []
    added_safe = 0
    for item in usable_streams:
        fp = stream_fingerprint(item)
        if fp in reference_manifest or fp in assigned_current_fps:
            continue
        provider_epg = _normalise(item.get("epg_channel_id") or item.get("provider_epg") or item.get("tvg_id"))
        keys = reference_provider_channels.get(provider_epg) or set()
        if len(keys) == 1:
            key = next(iter(keys))
            channel = channels_by_key.get(key)
            if channel is not None and _channel_name_compatible(
                item.get("name") or item.get("stream_name"),
                channel,
                channel_reference_names.get(key, []),
            ):
                channel.setdefault("streams", []).append(variant_factory(item, channel, "reference_incremental"))
                assigned_current_fps.add(fp)
                added_safe += 1
                continue

        try:
            relevant = bool(is_potential_catalog_stream(item))
        except Exception:
            relevant = True
        if relevant:
            unsafe.append({
                "stream_id": _clean(item.get("stream_id") or item.get("id")),
                "name": _clean(item.get("name") or item.get("stream_name")),
                "provider_epg": provider_epg,
            })
            if len(unsafe) >= 20:
                break

    if unsafe:
        return {
            "success": False,
            "reason": "new or changed relevant streams require full matching",
            "uncertain": unsafe,
            "overlap": overlap,
        }

    # Removed variants/channels disappear because the current account inventory
    # is authoritative.  Never leave a stale stream in the final catalogue.
    for channel in rebuilt.get("channels", []):
        variants = unique_sort_variants(channel.get("streams", []))
        if not variants:
            continue
        channel["streams"] = variants
        channel["stream_count"] = len(variants)
        if channel.get("key") in previous_enabled_states:
            channel["enabled"] = bool(previous_enabled_states[channel.get("key")])
        if variants and variants[0].get("logo"):
            channel["logo"] = variants[0].get("logo")
        rebuilt_channels.append(channel)

    if not rebuilt_channels:
        return {"success": False, "reason": "reference produced no live channels"}

    rebuilt["channels"] = rebuilt_channels
    rebuilt["server"] = server
    rebuilt["output_format"] = output_format
    rebuilt["stats"] = dict(rebuilt.get("stats") or {})
    rebuilt["stats"]["raw_streams"] = int(raw_count)
    rebuilt["stats"]["usable_live_streams"] = len(usable_streams)
    rebuilt["stats"]["reference_removed_variants"] = len(reference_variant_fps - current_manifest)
    rebuilt["stats"]["reference_added_variants"] = added_safe
    rebuilt["reference_build"] = {
        "mode": "reference_exact" if exact_inventory else "reference_incremental",
        "source": payload.get("source_name", "reference"),
        "inventory_overlap": round(overlap, 6),
    }
    return {"success": True, "catalog": rebuilt, "mode": rebuilt["reference_build"]["mode"]}


def suspicious_incomplete(previous_catalog, current_usable_count, account_fp):
    if not isinstance(previous_catalog, dict):
        return False, ""
    previous_meta = previous_catalog.get("reference_meta") or {}
    previous_fp = _clean(previous_meta.get("account_fingerprint"))
    previous_count = int((previous_meta.get("inventory") or {}).get("usable_count") or 0)
    if not previous_fp or not account_fp or previous_fp != account_fp or previous_count < 200:
        return False, ""
    if current_usable_count < max(50, int(previous_count * 0.40)):
        return True, "Provider returned only %s usable streams; the last successful run had %s." % (
            current_usable_count, previous_count,
        )
    return False, ""
