# -*- coding: utf-8 -*-
"""Conservative reference-cache support for the FLAM IPTV generator.

The cache never stores credentials or final stream URLs. It stores a manifest
of provider stream identities and a previously verified channel catalogue.
Exact or narrowly compatible changes can be reused; uncertain changes are
returned to the caller for FLAM's partial matcher, or rejected so the full
matcher can run.

Public API compatibility is intentionally retained for iptv_generator.py:

* stream_fingerprint
* build_inventory_meta
* build_epg_signatures
* account_fingerprint
* make_reference_meta
* attach_variant_fingerprints
* reference_payload_from_catalog
* load_reference_file
* try_build_from_reference
* suspicious_incomplete
"""

from __future__ import absolute_import

import copy
import hashlib
import json
import re
import unicodedata
from collections import Counter
from functools import lru_cache
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

REFERENCE_SCHEMA_VERSION = 3
REFERENCE_LOGIC_VERSION = "flam-iptv-delta-reference-v4"

ITEM_FINGERPRINT_ALGORITHM = "sha256-trunc128-stream-identity-v2"
INVENTORY_SIGNATURE_ALGORITHM = "sha256-sorted-item-fingerprints-with-duplicates-v2"
EPG_SIGNATURE_SCOPE = "channel-definitions-only"

DEFAULT_MINIMUM_INVENTORY_OVERLAP = 0.50
DEFAULT_MAX_PARTIAL_ITEMS = 1200
DEFAULT_INCOMPLETE_PREVIOUS_MINIMUM = 200
DEFAULT_INCOMPLETE_RATIO = 0.40
DEFAULT_INCOMPLETE_FLOOR = 50

_WHITESPACE_RE = re.compile(r"\s+")
_NON_ALNUM_RE = re.compile(r"[^a-z0-9]+")
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]+")

# These are cosmetic/source words for channel-name compatibility only. Region,
# numeric and identity words such as UK, US, TV and Channel remain meaningful.
_COSMETIC_NAME_WORDS = frozenset({
    "hd", "fhd", "uhd", "sd", "raw", "hevc", "h265", "h264", "4k",
    "2160p", "1080p", "720p", "576p", "480p", "50fps", "60fps",
    "25fps", "30fps", "vip", "now", "live",
})
_LEADING_SOURCE_WORDS = frozenset({"uk", "us", "usa", "vip", "now", "live"})


def _clean(value):
    if value is None:
        return ""
    return str(value).strip()


def _safe_int(value, default=0, minimum=None, maximum=None):
    try:
        result = int(value)
    except (TypeError, ValueError, OverflowError):
        result = int(default)
    if minimum is not None and result < minimum:
        result = minimum
    if maximum is not None and result > maximum:
        result = maximum
    return result


def _safe_float(value, default=0.0, minimum=None, maximum=None):
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError):
        result = float(default)
    if minimum is not None and result < minimum:
        result = minimum
    if maximum is not None and result > maximum:
        result = maximum
    return result


def _as_bool(value):
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    return _normalise(value) in {"1", "true", "yes", "on", "enabled"}


@lru_cache(maxsize=32768)
def _normalise_cached(text):
    text = unicodedata.normalize("NFKC", text).lower()
    return _WHITESPACE_RE.sub(" ", text).strip()


def _normalise(value):
    return _normalise_cached(_clean(value))


@lru_cache(maxsize=32768)
def _name_words_cached(text):
    decomposed = unicodedata.normalize("NFKD", _normalise_cached(text))
    words = [word for word in _NON_ALNUM_RE.sub(" ", decomposed).split() if word]

    # Provider names frequently begin with source labels such as "UK:" or
    # "USA:". Treat only a leading source label as cosmetic. Region terms that
    # occur in the actual channel name remain identity-bearing.
    while len(words) > 1 and words[0] in _LEADING_SOURCE_WORDS:
        words.pop(0)

    return tuple(word for word in words if word not in _COSMETIC_NAME_WORDS)


def _name_words(value):
    return list(_name_words_cached(_clean(value)))


def _normalised_category_values(item, key):
    value = item.get(key)
    if isinstance(value, (list, tuple, set)):
        return sorted({_normalise(entry) for entry in value if _normalise(entry)})
    value = _normalise(value)
    return [value] if value else []


def _category_identity(item):
    """Return labelled, normalised category identity fields.

    Labels prevent field-position collisions. Category names remain included
    because FLAM uses them for discovery and grouping; a real category rename is
    therefore deliberately treated as a small delta rather than silently reused.
    """
    parts = []
    for value in _normalised_category_values(item, "category_ids"):
        parts.append("category_ids=" + value)
    for key in ("category_id", "category_name", "category", "group"):
        for value in _normalised_category_values(item, key):
            parts.append(key + "=" + value)
    return "|".join(sorted(set(parts)))


def _stream_id(item):
    return _clean(item.get("stream_id") or item.get("id")) if isinstance(item, dict) else ""


def _stream_name(item):
    return _normalise(item.get("name") or item.get("stream_name")) if isinstance(item, dict) else ""


def _provider_epg(item):
    if not isinstance(item, dict):
        return ""
    return _normalise(item.get("epg_channel_id") or item.get("provider_epg") or item.get("tvg_id"))


def stream_identity_text(item):
    """Stable provider identity used for change detection.

    Logos and timestamps are intentionally excluded. A logo refresh should not
    force rematching; rebuilt variants still copy the current live logo.
    """
    if not isinstance(item, dict):
        return ""
    parts = [
        _stream_id(item),
        _stream_name(item),
        _provider_epg(item),
        _category_identity(item),
        _normalise(item.get("stream_type") or item.get("type")),
        "1" if _as_bool(item.get("is_adult")) else "0",
    ]
    return "\x1f".join(parts)


def stream_fingerprint(item):
    return hashlib.sha256(stream_identity_text(item).encode("utf-8", "replace")).hexdigest()[:32]


def legacy_v3_stream_fingerprint(item):
    """Reproduce the v3 fingerprint only for one-time local-cache migration.

    The old adult conversion bug is intentionally reproduced here so an older
    manifest can be compared accurately; all newly written references use v4.
    """
    if not isinstance(item, dict):
        return ""
    values = []
    category_ids = item.get("category_ids")
    if isinstance(category_ids, (list, tuple, set)):
        values.extend(_clean(value) for value in category_ids if _clean(value))
    for key in ("category_id", "category_name", "category", "group"):
        value = _clean(item.get(key))
        if value:
            values.append(value)
    old_category = "|".join(sorted(set(values)))
    parts = [
        _stream_id(item),
        _stream_name(item),
        _provider_epg(item),
        old_category,
        _normalise(item.get("stream_type") or item.get("type")),
        "1" if bool(item.get("is_adult")) else "0",
    ]
    return hashlib.sha256("".join(parts).encode("utf-8", "replace")).hexdigest()[:32]


def _prepare_stream_records(usable_streams):
    records = []
    invalid_count = 0
    for item in usable_streams or []:
        if not isinstance(item, dict) or not _stream_id(item):
            invalid_count += 1
            continue
        records.append((stream_fingerprint(item), item))
    return records, invalid_count


def _inventory_signature(fingerprints):
    return hashlib.sha256("\n".join(sorted(fingerprints)).encode("ascii")).hexdigest()


def build_inventory_meta(usable_streams):
    records, invalid_count = _prepare_stream_records(usable_streams)
    fingerprints = sorted(fp for fp, _item in records)  # duplicates deliberately preserved
    return {
        "item_fingerprint_algorithm": ITEM_FINGERPRINT_ALGORITHM,
        "inventory_signature_algorithm": INVENTORY_SIGNATURE_ALGORITHM,
        # Compatibility field retained for older diagnostics/readers.
        "algorithm": ITEM_FINGERPRINT_ALGORITHM,
        "input_count": len(usable_streams or []),
        "usable_count": len(records),
        "invalid_count": invalid_count,
        "duplicate_count": max(0, len(fingerprints) - len(set(fingerprints))),
        "signature": _inventory_signature(fingerprints),
        "manifest": fingerprints,
    }


def _epg_channel_record(channel):
    channel_id = _normalise(channel.get("id")) if isinstance(channel, dict) else ""
    names = []
    if isinstance(channel, dict):
        for name in channel.get("names", []) or []:
            normalised = _normalise(name)
            if normalised:
                names.append(normalised)
    return channel_id + "\x1f" + "\x1e".join(sorted(set(names)))


def build_epg_signatures(epg_data):
    """Build signatures for XMLTV channel definitions, not programme content."""
    signatures = {}
    if not isinstance(epg_data, dict):
        epg_data = {"uk": {"channels": epg_data or []}}
    for region, entry in epg_data.items():
        if not isinstance(entry, dict):
            continue
        channels = entry.get("channels") or []
        records = sorted(
            _epg_channel_record(channel)
            for channel in channels
            if isinstance(channel, dict) and _clean(channel.get("id"))
        )
        signatures[_clean(region)] = {
            "scope": EPG_SIGNATURE_SCOPE,
            "channel_count": len(records),
            "signature": hashlib.sha256("\n".join(records).encode("utf-8", "replace")).hexdigest(),
        }
    return signatures


def _canonical_server(server):
    raw = _clean(server).rstrip("/")
    if not raw:
        return ""
    try:
        parsed = urlsplit(raw)
        if not parsed.scheme or not parsed.hostname:
            return _normalise(raw).rstrip("/")
        scheme = parsed.scheme.lower()
        host = parsed.hostname.lower()
        try:
            port = parsed.port
        except ValueError:
            return _normalise(raw).rstrip("/")
        default_port = (scheme == "http" and port == 80) or (scheme == "https" and port == 443)
        if ":" in host and not host.startswith("["):
            host = "[" + host + "]"
        netloc = host if not port or default_port else "%s:%s" % (host, port)
        # Preserve path case because some panels can use a case-sensitive path.
        path = (parsed.path or "").rstrip("/")
        return urlunsplit((scheme, netloc, path, parsed.query, ""))
    except Exception:
        return _normalise(raw).rstrip("/")


def account_fingerprint(server, username):
    """Return a non-secret account label, not proof of subscription identity."""
    server = _canonical_server(server)
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
        "raw_count": _safe_int(raw_count, default=len(usable_streams or []), minimum=0),
        "account_fingerprint": account_fingerprint(server, username),
    }


def _names_compatible(left, right):
    left_words = set(_name_words(left))
    right_words = set(_name_words(right))
    if not left_words or not right_words:
        return False
    left_numbers = {word for word in left_words if word.isdigit()}
    right_numbers = {word for word in right_words if word.isdigit()}
    if left_numbers != right_numbers:
        return False
    overlap = len(left_words & right_words)
    precision = overlap / float(len(left_words))
    recall = overlap / float(len(right_words))
    jaccard = overlap / float(len(left_words | right_words))
    return min(precision, recall) >= 0.75 and jaccard >= 0.60


def attach_variant_fingerprints(catalog, usable_streams):
    """Attach current fingerprints to variants after a verified full/partial build.

    Exact stream-id/name/EPG matches are preferred. A unique stream-id fallback
    is accepted only when the name or provider EPG remains compatible, preventing
    a reused provider ID from silently inheriting the previous channel identity.
    """
    records, _invalid_count = _prepare_stream_records(usable_streams)
    by_id = {}
    by_triplet = {}
    for fp, item in records:
        stream_id = _stream_id(item)
        by_id.setdefault(stream_id, []).append((fp, item))
        triplet = (stream_id, _stream_name(item), _provider_epg(item))
        by_triplet.setdefault(triplet, []).append((fp, item))

    missing = []
    for channel in (catalog or {}).get("channels", []) or []:
        if not isinstance(channel, dict):
            continue
        for variant in channel.get("streams", []) or []:
            if not isinstance(variant, dict):
                missing.append((_clean(channel.get("key")), ""))
                continue
            triplet = (
                _clean(variant.get("stream_id")),
                _normalise(variant.get("name")),
                _normalise(variant.get("provider_epg")),
            )
            exact = by_triplet.get(triplet) or []
            exact_fps = sorted(set(fp for fp, _item in exact))
            if len(exact_fps) == 1:
                variant["source_fingerprint"] = exact_fps[0]
                continue

            id_matches = by_id.get(triplet[0]) or []
            distinct = {}
            for fp, item in id_matches:
                distinct.setdefault(fp, item)
            if len(distinct) == 1:
                fp, item = next(iter(distinct.items()))
                epg_compatible = bool(triplet[2] and _provider_epg(item) == triplet[2])
                name_compatible = _names_compatible(triplet[1], _stream_name(item))
                if epg_compatible or name_compatible:
                    variant["source_fingerprint"] = fp
                    continue
            missing.append((_clean(channel.get("key")), triplet[0]))
    return missing


def reference_payload_from_catalog(catalog):
    return {
        "schema_version": REFERENCE_SCHEMA_VERSION,
        "logic_version": REFERENCE_LOGIC_VERSION,
        "catalog": copy.deepcopy(catalog),
    }


def _safe_source_name(source_name):
    text = _CONTROL_RE.sub(" ", _clean(source_name))
    return _WHITESPACE_RE.sub(" ", text).strip()[:120] or "reference"


def _append_diagnostic(diagnostics, reason):
    if isinstance(diagnostics, list):
        diagnostics.append(_clean(reason)[:500])


def load_reference_file(path, source_name, diagnostics=None):
    """Load a reference or return None, optionally recording a safe reason."""
    path = Path(path)
    if not path.exists():
        _append_diagnostic(diagnostics, "reference file does not exist: %s" % path)
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except Exception as error:
        _append_diagnostic(diagnostics, "reference file could not be read: %s: %s" % (type(error).__name__, error))
        return None

    if isinstance(data, dict) and isinstance(data.get("catalog"), dict):
        payload = data
    elif isinstance(data, dict) and isinstance(data.get("channels"), list):
        payload = reference_payload_from_catalog(data)
    else:
        _append_diagnostic(diagnostics, "reference JSON has no catalogue")
        return None

    catalog = payload.get("catalog") or {}
    meta = catalog.get("reference_meta") or payload.get("reference_meta") or {}
    schema_version = _safe_int(meta.get("schema_version") or payload.get("schema_version"), default=-1)
    if schema_version != REFERENCE_SCHEMA_VERSION:
        _append_diagnostic(diagnostics, "reference schema mismatch: %s" % schema_version)
        return None
    logic_version = _clean(meta.get("logic_version") or payload.get("logic_version"))
    if logic_version != REFERENCE_LOGIC_VERSION:
        _append_diagnostic(diagnostics, "reference logic mismatch: %s" % logic_version)
        return None
    payload["source_name"] = _safe_source_name(source_name)
    return payload


def _epg_meta_matches(reference_meta, current_epg_meta):
    reference_epg = (reference_meta or {}).get("epg") or {}
    current_epg_meta = current_epg_meta or {}
    regions = set(reference_epg) | set(current_epg_meta)
    for region in regions:
        current = current_epg_meta.get(region)
        reference = reference_epg.get(region)
        if not current or not reference:
            return False
        if _clean(current.get("signature")) != _clean(reference.get("signature")):
            return False
    return True


def _channel_name_compatible(item_name, channel, reference_names):
    candidates = [_clean((channel or {}).get("name"))] + list(reference_names or [])
    return any(_names_compatible(item_name, candidate) for candidate in candidates if _clean(candidate))


def _current_variant_fields(item, fingerprint=None):
    fields = {
        "name": _clean(item.get("name") or item.get("stream_name")),
        "stream_id": _stream_id(item),
        "provider_epg": _provider_epg(item),
        "source_fingerprint": fingerprint or stream_fingerprint(item),
    }
    logo = _clean(item.get("stream_icon") or item.get("logo") or item.get("icon"))
    category = _clean(item.get("category_name") or item.get("category") or item.get("group"))
    # Some Xtream live-stream responses contain only category_id; the generator
    # resolves the human-readable category through its separate category map.
    # Do not erase a verified reference/factory value when the raw row is blank.
    if logo:
        fields["logo"] = logo
    if category:
        fields["category"] = category
    return fields


def _refresh_reference_variant(reference_variant, current_item, output_format, fingerprint=None):
    variant = copy.deepcopy(reference_variant)
    variant.update(_current_variant_fields(current_item, fingerprint=fingerprint))
    variant["output_format"] = output_format
    return variant


def _normalise_factory_variant(factory_variant, current_item, output_format, fingerprint):
    if not isinstance(factory_variant, dict):
        raise ValueError("variant_factory did not return a mapping")
    variant = copy.deepcopy(factory_variant)
    variant.update(_current_variant_fields(current_item, fingerprint=fingerprint))
    variant["output_format"] = output_format
    return variant


def _reference_catalog(payload):
    catalog = (payload or {}).get("catalog") or {}
    if not isinstance(catalog, dict) or not isinstance(catalog.get("channels"), list):
        return None
    return catalog


def _validate_reference_catalog(catalog, reference_manifest):
    errors = []
    keys = set()
    manifest_set = set(reference_manifest)
    for channel in catalog.get("channels", []) or []:
        if not isinstance(channel, dict):
            errors.append("non-dictionary channel")
            continue
        key = _clean(channel.get("key"))
        if not key:
            errors.append("blank channel key")
        elif key in keys:
            errors.append("duplicate channel key: %s" % key)
        keys.add(key)

        fingerprints = set()
        streams = channel.get("streams") or []
        if not isinstance(streams, list):
            errors.append("invalid stream list for %s" % key)
            continue
        for variant in streams:
            if not isinstance(variant, dict):
                errors.append("invalid variant in %s" % key)
                continue
            fp = _clean(variant.get("source_fingerprint"))
            stream_id = _clean(variant.get("stream_id"))
            if not stream_id:
                errors.append("blank stream id in %s" % key)
            if not fp:
                errors.append("missing source fingerprint in %s" % key)
            elif fp in fingerprints:
                errors.append("duplicate source fingerprint in %s" % key)
            elif fp not in manifest_set:
                errors.append("variant fingerprint absent from inventory in %s" % key)
            fingerprints.add(fp)
    return errors


def _failure(reason, **extra):
    result = {
        "success": False,
        "complete": False,
        "requires_partial_merge": False,
        "status": "rejected",
        "reason": _clean(reason),
    }
    result.update(extra)
    return result


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
    allow_partial=True,
    max_partial_items=DEFAULT_MAX_PARTIAL_ITEMS,
    minimum_inventory_overlap=DEFAULT_MINIMUM_INVENTORY_OVERLAP,
):
    """Return a reused catalogue and any records that still need partial matching.

    ``success`` remains for backwards compatibility. Callers should also inspect
    ``status`` / ``requires_partial_merge``:

    * reference_exact
    * reference_incremental
    * partial_required
    * rejected
    """
    previous_enabled_states = previous_enabled_states if isinstance(previous_enabled_states, dict) else {}
    max_partial_items = _safe_int(max_partial_items, DEFAULT_MAX_PARTIAL_ITEMS, minimum=0)
    minimum_inventory_overlap = _safe_float(
        minimum_inventory_overlap,
        DEFAULT_MINIMUM_INVENTORY_OVERLAP,
        minimum=0.0,
        maximum=1.0,
    )
    if not callable(is_potential_catalog_stream):
        return _failure("is_potential_catalog_stream callback is not callable")
    if not callable(variant_factory):
        return _failure("variant_factory callback is not callable")
    if not callable(unique_sort_variants):
        return _failure("unique_sort_variants callback is not callable")

    catalog = _reference_catalog(payload)
    if catalog is None:
        return _failure("invalid reference catalogue")
    reference_meta = catalog.get("reference_meta") or (payload or {}).get("reference_meta") or {}
    inventory = reference_meta.get("inventory") or {}
    reference_manifest = list(inventory.get("manifest") or [])
    if not reference_manifest:
        return _failure("reference has no inventory manifest")
    if not _epg_meta_matches(reference_meta, current_epg_meta):
        return _failure("EPG channel definitions changed")

    validation_errors = _validate_reference_catalog(catalog, reference_manifest)
    if validation_errors:
        return _failure(
            "reference catalogue failed integrity validation",
            validation_errors=validation_errors[:20],
        )

    prepared, invalid_current_count = _prepare_stream_records(usable_streams)
    current_fingerprints = [fp for fp, _item in prepared]
    current_counter = Counter(current_fingerprints)
    reference_counter = Counter(reference_manifest)
    current_signature = _inventory_signature(current_fingerprints)
    exact_inventory = (
        _clean(inventory.get("signature")) == current_signature
        and _safe_int(inventory.get("usable_count"), len(reference_manifest), minimum=0) == len(current_fingerprints)
    )
    retained_count = sum(min(count, current_counter.get(fp, 0)) for fp, count in reference_counter.items())
    overlap = retained_count / float(max(1, sum(reference_counter.values())))
    current_novel_count = sum(max(0, count - reference_counter.get(fp, 0)) for fp, count in current_counter.items())
    current_novelty = current_novel_count / float(max(1, len(current_fingerprints)))
    if not exact_inventory and overlap < minimum_inventory_overlap:
        return _failure(
            "provider inventory differs too much",
            overlap=overlap,
            current_novelty=current_novelty,
        )

    current_by_fp = {}
    for fp, item in prepared:
        current_by_fp.setdefault(fp, []).append(item)

    reference_provider_channels = {}
    channel_reference_names = {}
    reference_variant_fps = set()
    for channel in catalog.get("channels", []):
        key = _clean(channel.get("key"))
        names = []
        for variant in channel.get("streams", []):
            fp = _clean(variant.get("source_fingerprint"))
            if fp:
                reference_variant_fps.add(fp)
            if variant.get("name"):
                names.append(variant.get("name"))
            provider_epg = _normalise(variant.get("provider_epg"))
            if provider_epg and key:
                reference_provider_channels.setdefault(provider_epg, set()).add(key)
        channel_reference_names[key] = names

    rebuilt = copy.deepcopy(catalog)
    rebuilt_channels = []
    assigned_incremental_fps = set()
    channels_by_key = {}

    # Duplicate provider rows with the same fingerprint are deduplicated within
    # a channel. The same live stream may intentionally back two distinct
    # catalogue channels (for example a curated alias and an auto-discovered US
    # row), so fingerprints are not consumed globally across channel records.
    for channel in rebuilt.get("channels", []):
        key = _clean(channel.get("key"))
        retained = []
        for variant in channel.get("streams", []):
            fp = _clean(variant.get("source_fingerprint"))
            matches = current_by_fp.get(fp) if fp else None
            if not matches:
                continue
            retained.append(_refresh_reference_variant(variant, matches[0], output_format, fingerprint=fp))
        channel["streams"] = retained
        channel["stream_count"] = len(retained)
        channels_by_key[key] = channel

    unresolved_items = []
    unresolved_preview = []
    added_safe = 0
    irrelevant_new = 0
    duplicate_current_records = max(0, len(current_fingerprints) - len(current_counter))
    seen_new_fps = set()

    for fp, item in prepared:
        if fp in reference_counter or fp in assigned_incremental_fps or fp in seen_new_fps:
            continue
        seen_new_fps.add(fp)
        provider_epg = _provider_epg(item)
        keys = reference_provider_channels.get(provider_epg) or set()
        if len(keys) == 1:
            key = next(iter(keys))
            channel = channels_by_key.get(key)
            if channel is not None and _channel_name_compatible(
                item.get("name") or item.get("stream_name"),
                channel,
                channel_reference_names.get(key, []),
            ):
                try:
                    created = variant_factory(item, channel, "reference_incremental")
                    created = _normalise_factory_variant(created, item, output_format, fp)
                except Exception as error:
                    return _failure(
                        "variant_factory failed during reference reuse: %s: %s" % (type(error).__name__, error),
                        overlap=overlap,
                    )
                channel.setdefault("streams", []).append(created)
                assigned_incremental_fps.add(fp)
                added_safe += 1
                continue

        try:
            relevant = bool(is_potential_catalog_stream(item))
        except Exception as error:
            return _failure(
                "is_potential_catalog_stream failed during reference reuse: %s: %s" % (type(error).__name__, error),
                overlap=overlap,
            )
        if relevant:
            unresolved_items.append(item)
            if len(unresolved_preview) < 20:
                unresolved_preview.append({
                    "stream_id": _stream_id(item),
                    "name": _clean(item.get("name") or item.get("stream_name")),
                    "provider_epg": provider_epg,
                })
        else:
            irrelevant_new += 1

    if unresolved_items and (not allow_partial or len(unresolved_items) > max_partial_items):
        return _failure(
            "too many new or changed relevant streams for a safe partial match",
            uncertain=unresolved_preview,
            uncertain_count=len(unresolved_items),
            overlap=overlap,
            current_novelty=current_novelty,
        )

    for channel in rebuilt.get("channels", []):
        try:
            variants = unique_sort_variants(channel.get("streams", []))
        except Exception as error:
            return _failure(
                "unique_sort_variants failed during reference reuse: %s: %s" % (type(error).__name__, error),
                overlap=overlap,
            )
        if not variants:
            continue
        channel["streams"] = variants
        channel["stream_count"] = len(variants)
        key = _clean(channel.get("key"))
        if key in previous_enabled_states:
            channel["enabled"] = bool(previous_enabled_states[key])
        if variants[0].get("logo"):
            channel["logo"] = variants[0].get("logo")
        rebuilt_channels.append(channel)

    if not rebuilt_channels and not unresolved_items:
        return _failure("reference produced no live channels")

    rebuilt["channels"] = rebuilt_channels
    rebuilt["server"] = server
    rebuilt["output_format"] = output_format
    rebuilt["stats"] = dict(rebuilt.get("stats") or {})
    rebuilt["stats"].update({
        "raw_streams": _safe_int(raw_count, len(usable_streams or []), minimum=0),
        "usable_live_streams": len(prepared),
        "reference_invalid_current_records": invalid_current_count,
        "reference_duplicate_current_records": duplicate_current_records,
        "reference_removed_variants": len(reference_variant_fps - set(current_counter)),
        "reference_added_variants": added_safe,
        "reference_safely_attached_streams": added_safe,
        "reference_unresolved_streams": len(unresolved_items),
        "reference_irrelevant_new_streams": irrelevant_new,
        "reference_current_novelty": round(current_novelty, 6),
    })

    if unresolved_items:
        mode = "reference_partial"
        status = "partial_required"
        complete = False
    elif exact_inventory:
        mode = "reference_exact"
        status = "reference_exact"
        complete = True
    else:
        mode = "reference_incremental"
        status = "reference_incremental"
        complete = True

    rebuilt["reference_build"] = {
        "mode": mode,
        "status": status,
        "source": _safe_source_name((payload or {}).get("source_name", "reference")),
        "inventory_overlap": round(overlap, 6),
        "current_novelty": round(current_novelty, 6),
        "partial_items": len(unresolved_items),
        "requires_partial_merge": bool(unresolved_items),
    }
    return {
        "success": True,
        "complete": complete,
        "requires_partial_merge": bool(unresolved_items),
        "status": status,
        "catalog": rebuilt,
        "mode": mode,
        "unresolved_items": unresolved_items,
        "uncertain": unresolved_preview,
    }


def suspicious_incomplete(
    previous_catalog,
    current_usable_count,
    account_fp,
    previous_minimum=DEFAULT_INCOMPLETE_PREVIOUS_MINIMUM,
    incomplete_ratio=DEFAULT_INCOMPLETE_RATIO,
    current_floor=DEFAULT_INCOMPLETE_FLOOR,
):
    """Detect a likely truncated provider response for the same account label."""
    if not isinstance(previous_catalog, dict):
        return False, ""
    previous_meta = previous_catalog.get("reference_meta") or {}
    previous_fp = _clean(previous_meta.get("account_fingerprint"))
    previous_count = _safe_int((previous_meta.get("inventory") or {}).get("usable_count"), 0, minimum=0)
    current_count = _safe_int(current_usable_count, 0, minimum=0)
    previous_minimum = _safe_int(previous_minimum, DEFAULT_INCOMPLETE_PREVIOUS_MINIMUM, minimum=1)
    current_floor = _safe_int(current_floor, DEFAULT_INCOMPLETE_FLOOR, minimum=0)
    incomplete_ratio = _safe_float(incomplete_ratio, DEFAULT_INCOMPLETE_RATIO, minimum=0.0, maximum=1.0)

    if not previous_fp or not account_fp or previous_fp != _clean(account_fp) or previous_count < previous_minimum:
        return False, ""
    threshold = max(current_floor, int(previous_count * incomplete_ratio))
    if current_count < threshold:
        return True, "Provider returned only %s usable streams; the last successful run had %s." % (
            current_count,
            previous_count,
        )
    return False, ""
