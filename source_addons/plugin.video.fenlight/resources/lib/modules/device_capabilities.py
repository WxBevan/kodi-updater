# -*- coding: utf-8 -*-
import re

from caches.settings_cache import get_setting, set_setting
from modules import kodi_utils


def _normalise_hdr_types(value):
	if not value:
		return set()

	value = value.lower()

	# Kodi normally returns readable canonical names, but
	# normalising makes this resilient to separators.
	compact = re.sub(r'[^a-z0-9+]', '', value)

	supported = set()

	if 'dolbyvision' in compact:
		supported.add('dolbyvision')

	if 'hdr10plus' in compact or 'hdr10+' in value:
		supported.add('hdr10plus')

	if 'hdr10' in compact:
		supported.add('hdr10')

	if 'hlg' in compact:
		supported.add('hlg')

	return supported


def _safe_int(value):
	try:
		return int(value)
	except (TypeError, ValueError):
		return 0


def _quality_from_current_output(width, height):
	long_edge = max(width, height)
	short_edge = min(width, height)

	if long_edge >= 3000 or short_edge >= 2000:
		return '4K'

	if long_edge >= 1600 or short_edge >= 1000:
		return '1080p'

	if long_edge >= 1100 or short_edge >= 700:
		return '720p'

	if width and height:
		return 'SD'

	return 'unknown'


def detect_device_capabilities():
	hdr_raw = kodi_utils.get_infolabel(
		'System.SupportedHDRTypes'
	)

	width = _safe_int(
		kodi_utils.get_infolabel('System.ScreenWidth')
	)

	height = _safe_int(
		kodi_utils.get_infolabel('System.ScreenHeight')
	)

	supported_hdr = _normalise_hdr_types(hdr_raw)
	max_quality = _quality_from_current_output(
		width,
		height
	)

	# An empty HDR response is treated as unknown rather than
	# immediately meaning the display supports no HDR.
	if hdr_raw:
		hdr_setting = ','.join(sorted(supported_hdr)) or 'sdr'
	else:
		hdr_setting = 'unknown'

	set_setting(
		'device.detected_hdr_types',
		hdr_setting
	)

	set_setting(
		'device.detected_max_quality',
		max_quality
	)

	set_setting(
		'device.detected_width',
		str(width)
	)

	set_setting(
		'device.detected_height',
		str(height)
	)

	kodi_utils.logger(
		'Fen Light',
		'Device capabilities: resolution=%sx%s, '
		'quality=%s, HDR raw="%s", parsed=%s'
		% (
			width,
			height,
			max_quality,
			hdr_raw,
			hdr_setting
		)
	)

	return {
		'hdr_types': supported_hdr,
		'hdr_known': bool(hdr_raw),
		'max_quality': max_quality,
		'width': width,
		'height': height
	}


def get_detected_capabilities():
	hdr_value = get_setting(
		'fenlight.device.detected_hdr_types',
		'unknown'
	)

	if hdr_value in ('', 'unknown'):
		hdr_types = set()
		hdr_known = False
	else:
		hdr_types = {
			item
			for item in hdr_value.split(',')
			if item and item != 'sdr'
		}
		hdr_known = True

	return {
		'hdr_types': hdr_types,
		'hdr_known': hdr_known,
		'max_quality': get_setting(
			'fenlight.device.detected_max_quality',
			'unknown'
		)
	}