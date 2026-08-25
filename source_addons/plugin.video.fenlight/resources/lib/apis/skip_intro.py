# -*- coding: utf-8 -*-
from apis import theintrodb_api, introdb_api
from caches.skip_cache import skip_cache
# from modules.kodi_utils import logger

cache_key = 'v2.%s.%s.%s.%s'  # id . season . episode . duration_seconds - the table is the namespace
HIT_HOURS, EMPTY_HOURS = 720, 168  # 30 days for real data, 7 days for blank data
KINDS = ('recap', 'intro', 'outro')  # ordered by where they sit in an episode

def valid_segment(kind, seg, total_time=None):
	try:
		start, end = float(seg['start_sec']), float(seg['end_sec'])
	except (KeyError, TypeError, ValueError): return False
	if start < 0 or end <= start: return False
	duration = end - start
	if kind == 'recap':
		if not 5 <= duration <= 1200: return False
		if total_time and start > total_time * 0.5: return False  # recaps sit at the start
		return True
	if kind == 'outro':
		if not 5 <= duration <= 1800: return False
		if total_time and start < total_time * 0.5: return False  # outros sit in the latter half
		return True
	if not 5 <= duration <= 300: return False
	if total_time and start > total_time * 0.5: return False  # intro not past the midpoint
	return True

def _first_theintrodb_seg(seg_list, kind, total_time):
	if not seg_list: return None
	for s in seg_list:
		if not isinstance(s, dict): continue
		start_ms, end_ms = s.get('start_ms'), s.get('end_ms')
		if kind == 'outro':
			if start_ms in (None, 0): continue
			start = start_ms / 1000.0
			end = float(total_time or 0) if end_ms is None else end_ms / 1000.0
		else:
			start = 0.0 if start_ms is None else start_ms / 1000.0
			if end_ms in (None, 0): continue
			end = end_ms / 1000.0
		return {'start_sec': start, 'end_sec': end}
	return None

def _from_theintrodb(data, total_time):
	return {'intro': _first_theintrodb_seg(data.get('intro'), 'intro', total_time),
			'recap': _first_theintrodb_seg(data.get('recap'), 'recap', total_time),
			'outro': _first_theintrodb_seg(data.get('credits'), 'outro', total_time)}

def _fetch(tmdb_id, imdb_id, season, episode, total_time):
	# TheIntroDB is primary, but fill any missing segment types from IntroDB rather than
	# treating the first provider with any data as a complete result.
	segments = {'intro': None, 'recap': None, 'outro': None}
	errored = False
	if tmdb_id:
		data = theintrodb_api.get_media(tmdb_id, season, episode, int((total_time or 0) * 1000))
		if data is None: errored = True                       # transient failure
		elif data: segments.update(_from_theintrodb(data, total_time))
	if imdb_id and not all(segments.values()):
		data2 = introdb_api.get_segments(imdb_id, season, episode)
		if data2 is None: errored = True
		elif data2:
			for kind in KINDS:
				if not segments[kind] and data2.get(kind): segments[kind] = data2[kind]
	# Do not cache an incomplete result after a transient provider failure; that lets a
	# later playback retry the missing provider instead of preserving the gap for 30 days.
	return segments, not errored

def get_segments(tmdb_id, imdb_id, season, episode, total_time, cache_only=False):
	try: season, episode = int(season), int(episode)
	except (TypeError, ValueError): return None
	key_id = tmdb_id or imdb_id
	if not key_id: return None
	key = cache_key % (key_id, season, episode, int(total_time or 0))
	cached = skip_cache.get(key)
	if cached is not None: return cached
	if cache_only: return None
	segments, cacheable = _fetch(tmdb_id, imdb_id, season, episode, total_time)
	if cacheable:
		skip_cache.set(key, segments, expiration=HIT_HOURS if any(segments.values()) else EMPTY_HOURS)
	return segments

def get_skip_windows(tmdb_id, imdb_id, season, episode, total_time, enabled_kinds, cache_only=False):
	segments = get_segments(tmdb_id, imdb_id, season, episode, total_time, cache_only=cache_only)
	if not segments: return []
	windows = []
	for kind in KINDS:
		if kind not in enabled_kinds: continue
		seg = segments.get(kind)
		if seg and valid_segment(kind, seg, total_time):
			windows.append({'kind': kind, 'start': float(seg['start_sec']), 'end': float(seg['end_sec'])})
	return windows
