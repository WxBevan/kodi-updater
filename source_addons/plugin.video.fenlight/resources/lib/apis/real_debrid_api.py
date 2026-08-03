# -*- coding: utf-8 -*-
"""Real-Debrid API wrapper for Fen Light / FLAM.

Important 2026 compatibility notes
----------------------------------
Real-Debrid's public documentation no longer exposes
/torrents/instantAvailability/{hash}; the endpoint can return
{"error": "disabled_endpoint", "error_code": 37}.

This wrapper therefore does not rely on instantAvailability for playback.
It resolves a selected torrent through the supported workflow:

    addMagnet -> torrents/info -> selectFiles -> torrents/info -> unrestrict/link

It also handles existing cloud torrents and the documented
"torrent_already_active" response (error_code 33).
"""

import base64
import re
import time
import unicodedata
from threading import Thread
try:
	from urllib.parse import parse_qs, urlsplit
except ImportError:  # pragma: no cover - Python 2 compatibility is not expected on Kodi 21
	from urlparse import parse_qs, urlsplit

import requests

try:
	import xbmc
except ImportError:
	xbmc = None

from caches.main_cache import cache_object
from caches.settings_cache import get_setting, set_setting
from modules.utils import copy2clip, make_tinyurl, make_qrcode
from modules.source_utils import supported_video_extensions, seas_ep_filter, extras
from modules.kodi_utils import sleep, ok_dialog, progress_dialog, notification


OFFICIAL_BASE_URL = 'https://api.real-debrid.com/rest/1.0/'
OFFICIAL_AUTH_URL = 'https://api.real-debrid.com/oauth/v2/'
OPEN_SOURCE_CLIENT_ID = 'X245A4XAIBGVM'
REQUEST_TIMEOUT = (10, 30)
MAX_TORRENT_LIST = 5000
API_REVISION = '2026.08.03-v3'

ERROR_NAMES = {
	-1: 'internal_error',
	1: 'missing_parameter',
	2: 'bad_parameter_value',
	3: 'unknown_method',
	4: 'method_not_allowed',
	5: 'slow_down',
	6: 'resource_unreachable',
	7: 'resource_not_found',
	8: 'bad_token',
	9: 'permission_denied',
	10: 'two_factor_needed',
	11: 'two_factor_pending',
	12: 'invalid_login',
	13: 'invalid_password',
	14: 'account_locked',
	15: 'account_not_activated',
	16: 'unsupported_hoster',
	17: 'hoster_in_maintenance',
	18: 'hoster_limit_reached',
	19: 'hoster_temporarily_unavailable',
	20: 'hoster_unavailable_for_free_user',
	21: 'too_many_active_downloads',
	22: 'ip_not_allowed',
	23: 'traffic_exhausted',
	24: 'file_unavailable',
	25: 'service_unavailable',
	26: 'upload_too_big',
	27: 'upload_error',
	28: 'file_not_allowed',
	29: 'torrent_too_big',
	30: 'torrent_file_invalid',
	31: 'action_already_done',
	32: 'image_resolution_error',
	33: 'torrent_already_active',
	34: 'too_many_requests',
	35: 'infringing_file',
	36: 'fair_usage_limit',
	37: 'disabled_endpoint',
}


class RealDebridAPI:
	def __init__(self):
		self.client_ID = get_setting('fenlight.rd.client_id', 'empty_setting')
		if self.client_ID in ('empty_setting', ''):
			self.client_ID = OPEN_SOURCE_CLIENT_ID

		# The current public documentation specifies api.real-debrid.com for both
		# REST and OAuth. Do not silently route authenticated calls to another host.
		self.base_url = OFFICIAL_BASE_URL
		self.auth_url = OFFICIAL_AUTH_URL

		self.token = get_setting('fenlight.rd.token', 'empty_setting')
		self.secret = get_setting('fenlight.rd.secret', 'empty_setting')
		self.refresh = get_setting('fenlight.rd.refresh', 'empty_setting')
		self.device_code = ''
		self.refresh_retries = 0
		self.break_auth_loop = False
		self.last_error = None
		self._instant_availability_warning_logged = False
		self.session = requests.Session()
		self.session.headers.update({
			'Accept': 'application/json',
			'User-Agent': 'FenLight-RealDebrid/3.0'
		})
		self._log('Loaded API revision=%s file=%s' % (API_REVISION, __file__))

	def _log(self, message, error=False):
		line = '[RealDebridAPI] %s' % message
		try:
			if xbmc is not None:
				xbmc.log(line, xbmc.LOGERROR if error else xbmc.LOGINFO)
			else:
				print(line)
		except Exception:
			pass

	def _set_error(self, message, error_code=None, http_status=None):
		name = ERROR_NAMES.get(error_code, 'unknown_error') if error_code is not None else 'unknown_error'
		parts = [message]
		if error_code is not None:
			parts.append('error_code=%s(%s)' % (error_code, name))
		if http_status is not None:
			parts.append('http_status=%s' % http_status)
		self.last_error = ', '.join(parts)
		self._log(self.last_error, error=True)
		return self.last_error

	# -------------------------------------------------------------------------
	# OAuth
	# -------------------------------------------------------------------------
	def auth(self):
		self.secret = ''
		self.client_ID = OPEN_SOURCE_CLIENT_ID
		try:
			response = self.session.get(
				self.auth_url + 'device/code',
				params={'client_id': self.client_ID, 'new_credentials': 'yes'},
				timeout=REQUEST_TIMEOUT
			)
			response.raise_for_status()
			payload = response.json()
		except Exception as error:
			self._set_error('Device authorization failed: %s' % error)
			ok_dialog(text=self.last_error)
			return False

		user_code = payload.get('user_code')
		device_code = payload.get('device_code')
		verification_url = payload.get('direct_verification_url') or payload.get('verification_url')
		if not user_code or not device_code or not verification_url:
			self._set_error('Real-Debrid returned incomplete device authorization data')
			ok_dialog(text=self.last_error)
			return False

		qr_code = make_qrcode(verification_url) or ''
		short_url = make_tinyurl(verification_url)
		copy2clip(verification_url)
		if short_url:
			insert = 'OR visit this URL: [B]%s[/B][CR]OR Enter this Code: [B]%s[/B]' % (short_url, user_code)
		else:
			insert = 'OR Enter this Code: [B]%s[/B]' % user_code
		content = 'Please Scan the QR Code%s[CR]' % insert
		dialog = progress_dialog('Real Debrid Authorize', qr_code)
		dialog.update(content, 0)

		expires_in = int(payload.get('expires_in') or 1800)
		interval = max(1, int(payload.get('interval') or 5))
		started = time.time()

		while not dialog.iscanceled() and time.time() - started < expires_in and not self.secret:
			sleep(interval * 1000)
			try:
				response = self.session.get(
					self.auth_url + 'device/credentials',
					params={'client_id': self.client_ID, 'code': device_code},
					timeout=REQUEST_TIMEOUT
				)
				credentials = response.json()
			except Exception:
				continue

			if response.status_code >= 400 or credentials.get('error'):
				elapsed = time.time() - started
				dialog.update(content, min(100, int(100 * elapsed / float(expires_in))))
				continue

			self.client_ID = credentials.get('client_id') or ''
			self.secret = credentials.get('client_secret') or ''
			if self.client_ID and self.secret:
				set_setting('rd.client_id', self.client_ID)
				set_setting('rd.secret', self.secret)

		try:
			dialog.close()
		except Exception:
			pass

		if not self.secret:
			return False

		try:
			response = self.session.post(
				self.auth_url + 'token',
				data={
					'client_id': self.client_ID,
					'client_secret': self.secret,
					'code': device_code,
					'grant_type': 'http://oauth.net/grant_type/device/1.0'
				},
				timeout=REQUEST_TIMEOUT
			)
			data = response.json()
			if response.status_code >= 400 or data.get('error'):
				raise RuntimeError(data.get('error') or 'HTTP %s' % response.status_code)
			self.token = data['access_token']
			self.refresh = data['refresh_token']
			set_setting('rd.token', self.token)
			set_setting('rd.refresh', self.refresh)
			account = self.account_info() or {}
			username = account.get('username', '') if isinstance(account, dict) else ''
			set_setting('rd.account_id', username)
			set_setting('rd.enabled', 'true')
			ok_dialog(text='Success')
			return True
		except Exception as error:
			self._set_error('Token authorization failed: %s' % error)
			ok_dialog(text=self.last_error)
			return False

	def refresh_token(self):
		if self.refresh in ('empty_setting', '') or self.secret in ('empty_setting', ''):
			return False
		try:
			response = self.session.post(
				self.auth_url + 'token',
				data={
					'client_id': self.client_ID,
					'client_secret': self.secret,
					'code': self.refresh,
					'grant_type': 'http://oauth.net/grant_type/device/1.0'
				},
				timeout=REQUEST_TIMEOUT
			)
			payload = response.json()
			if response.status_code >= 400 or payload.get('error'):
				self._set_error(
					'Refresh token failed: %s' % (payload.get('error') or 'HTTP %s' % response.status_code),
					payload.get('error_code'), response.status_code
				)
				return False
			self.token = payload['access_token']
			self.refresh = payload['refresh_token']
			set_setting('rd.token', self.token)
			set_setting('rd.refresh', self.refresh)
			return True
		except Exception as error:
			self._set_error('Refresh token network failure: %s' % error)
			return False

	def revoke(self):
		set_setting('rd.client_id', 'empty_setting')
		set_setting('rd.secret', 'empty_setting')
		set_setting('rd.refresh', 'empty_setting')
		set_setting('rd.token', 'empty_setting')
		set_setting('rd.account_id', 'empty_setting')
		set_setting('rd.enabled', 'false')
		notification('Real Debrid Authorization Reset', 3000)

	# -------------------------------------------------------------------------
	# Public API methods
	# -------------------------------------------------------------------------
	def account_info(self):
		return self._get('user')

	def check_cache(self, hashes):
		"""Compatibility method.

		The former instantAvailability endpoint is no longer in the public method
		list and currently returns error_code 37 on affected accounts. There is no
		documented replacement that can bulk-check arbitrary hashes without first
		adding them. Returning an empty mapping is safer than falsely claiming every
		source is cached.
		"""
		if not self._instant_availability_warning_logged:
			self._instant_availability_warning_logged = True
			self._log(
				'instantAvailability is disabled/unsupported by the current public API; '
				'cache state must be determined when a selected magnet is resolved.',
				error=True
			)
		return {}

	def check_hash(self, hash_string):
		return self.check_cache([hash_string])

	def check_single_magnet(self, hash_string):
		# A torrent already present and downloaded in the user's cloud is known usable.
		torrent = self.find_torrent_by_hash(hash_string)
		return bool(torrent and torrent.get('status') == 'downloaded' and torrent.get('links'))

	def torrents_activeCount(self):
		return self._get('torrents/activeCount')

	def user_cloud(self):
		return cache_object(self._get, 'rd_user_cloud', 'torrents?limit=500', False, 0.03)

	def user_cloud_check(self):
		return self._get('torrents?limit=500')

	def downloads(self):
		return cache_object(self._get, 'rd_downloads', 'downloads?limit=500', False, 0.03)

	def user_cloud_info(self, file_id):
		return cache_object(self._get, 'rd_user_cloud_info_%s' % file_id, 'torrents/info/%s' % file_id, False, 0.03)

	def user_cloud_info_check(self, file_id):
		return self._get('torrents/info/%s' % file_id)

	def torrent_info(self, file_id):
		return self._get('torrents/info/%s' % file_id)

	def unrestrict_link_details(self, link):
		response = self._post('unrestrict/link', {'link': link})
		if isinstance(response, list):
			# The endpoint may return multiple generated links for some providers.
			response = response[0] if response else None
		if not isinstance(response, dict) or response.get('error'):
			return None
		self._log('unrestrict/link success filename=%s streamable=%s' % (
			response.get('filename'), response.get('streamable')
		))
		return response

	def unrestrict_link(self, link):
		response = self.unrestrict_link_details(link)
		return response.get('download') if response else None

	def add_magnet(self, magnet):
		magnet = unicodedata.normalize('NFC', magnet or '')
		response = self._post('torrents/addMagnet', {'magnet': magnet})
		if isinstance(response, dict) and response.get('id'):
			self._log('addMagnet success torrent_id=%s' % response.get('id'))
		return response

	def add_torrent_select(self, torrent_id, file_ids):
		self.clear_cache(clear_hashes=False)
		if isinstance(file_ids, (list, tuple, set)):
			file_ids = ','.join(str(value) for value in file_ids)
		response = self._post('torrents/selectFiles/%s' % torrent_id, {'files': str(file_ids)})
		if not self._is_api_error(response):
			self._log('selectFiles success torrent_id=%s files=%s' % (torrent_id, file_ids))
		return response

	def delete_torrent(self, folder_id):
		if not folder_id or self.token in ('empty_setting', ''):
			return None
		return self._delete('torrents/delete/%s' % folder_id)

	def delete_download(self, download_id):
		if not download_id or self.token in ('empty_setting', ''):
			return None
		return self._delete('downloads/delete/%s' % download_id)

	def find_torrent_by_hash(self, info_hash):
		target = self._normalize_info_hash(info_hash)
		if not target:
			return None
		result = self._get('torrents?limit=%s' % MAX_TORRENT_LIST)
		if not isinstance(result, list):
			return None
		for torrent in result:
			if str(torrent.get('hash') or '').lower() == target:
				return torrent
		return None

	def create_transfer(self, magnet_url):
		"""Add a magnet to cloud and select its video files.

		Returns the legacy strings expected by Fen Light: 'success' or 'failed'.
		"""
		torrent_id = None
		created_here = False
		try:
			info_hash = self._extract_info_hash(magnet_url)
			existing = self.find_torrent_by_hash(info_hash) if info_hash else None
			if existing:
				torrent_id = existing.get('id')
			else:
				added = self.add_magnet(magnet_url)
				if not isinstance(added, dict):
					self._set_error('addMagnet returned no JSON object')
					return 'failed'
				if added.get('error_code') == 33:
					existing = self.find_torrent_by_hash(info_hash)
					torrent_id = existing.get('id') if existing else None
				elif added.get('error') or not added.get('id'):
					self._set_error(
						'addMagnet failed: %s' % (added.get('error') or 'missing torrent id'),
						added.get('error_code'), added.get('http_status')
					)
					return 'failed'
				else:
					torrent_id = added['id']
					created_here = True

			if not torrent_id:
				self._set_error('Could not locate the Real-Debrid torrent after addMagnet')
				return 'failed'

			info = self._wait_for_file_list(torrent_id)
			if not info:
				return 'failed'
			status = str(info.get('status') or '').lower()
			if status == 'waiting_files_selection':
				file_ids = self._video_file_ids(info)
				if not file_ids:
					self._set_error('Torrent contains no supported video files')
					if created_here:
						self.delete_torrent(torrent_id)
					return 'failed'
				selection = self.add_torrent_select(torrent_id, file_ids)
				if self._is_api_error(selection):
					if created_here:
						self.delete_torrent(torrent_id)
					return 'failed'

			self._log('Added torrent to cloud: torrent_id=%s status=%s' % (torrent_id, status or 'unknown'))
			return 'success'
		except Exception as error:
			self._set_error('create_transfer exception: %s' % error)
			if torrent_id and created_here:
				self.delete_torrent(torrent_id)
			return 'failed'

	def resolve_magnet(self, magnet_url, info_hash, store_to_cloud, title, season, episode):
		"""Resolve a selected torrent through documented Real-Debrid methods."""
		torrent_id = None
		created_here = False
		try:
			normalized_hash = self._normalize_info_hash(info_hash) or self._extract_info_hash(magnet_url)
			torrent = self.find_torrent_by_hash(normalized_hash) if normalized_hash else None

			if torrent:
				torrent_id = torrent.get('id')
				self._log('Using existing cloud torrent: torrent_id=%s status=%s' % (torrent_id, torrent.get('status')))
			else:
				added = self.add_magnet(magnet_url)
				if not isinstance(added, dict):
					self._set_error('addMagnet returned no JSON object')
					return None

				if added.get('error_code') == 33:
					torrent = self.find_torrent_by_hash(normalized_hash)
					torrent_id = torrent.get('id') if torrent else None
				elif added.get('error') or not added.get('id'):
					self._set_error(
						'addMagnet failed: %s' % (added.get('error') or 'missing torrent id'),
						added.get('error_code'), added.get('http_status')
					)
					return None
				else:
					torrent_id = added['id']
					created_here = True

			if not torrent_id:
				self._set_error('Could not locate torrent after addMagnet/error 33')
				return None

			info = self._prepare_torrent_for_playback(
				torrent_id=torrent_id,
				title=title,
				season=season,
				episode=episode,
				max_wait_seconds=35
			)
			if not info:
				if created_here and not store_to_cloud:
					self.delete_torrent(torrent_id)
				return None

			selected_files = [item for item in info.get('files', []) if item.get('selected') == 1]
			links = info.get('links') or []
			if not selected_files or not links:
				self._set_error('Downloaded torrent has no selected files or host links')
				if created_here and not store_to_cloud:
					self.delete_torrent(torrent_id)
				return None

			entry = self._pick_selected_entry(selected_files, links, title, season, episode)
			if not entry:
				self._set_error('Could not match a playable file in the selected torrent')
				if created_here and not store_to_cloud:
					self.delete_torrent(torrent_id)
				return None

			details = self.unrestrict_link_details(entry['link'])
			if not details:
				if created_here and not store_to_cloud:
					self.delete_torrent(torrent_id)
				return None

			download = details.get('download')
			filename = details.get('filename') or entry['file'].get('path', '')
			mime_type = str(details.get('mimeType') or '').lower()
			streamable = details.get('streamable')

			# Do not require the generated URL itself to end in .mkv/.mp4. Some valid
			# Real-Debrid download URLs are opaque or carry the filename separately.
			if not download:
				self._set_error('unrestrict/link returned no download URL')
				return None
			if str(filename).lower().endswith('.rar'):
				self._set_error('Resolved item is a RAR archive, not a video')
				return None
			if mime_type and not (mime_type.startswith('video/') or mime_type == 'application/octet-stream'):
				self._log('Resolved MIME type is %s; allowing Kodi to probe the stream.' % mime_type)
			if streamable == 0:
				self._log('Real-Debrid marked the file streamable=0; returning direct download URL for Kodi probing.')

			if created_here and not store_to_cloud:
				Thread(target=self.delete_torrent, args=(torrent_id,)).start()
			return download
		except Exception as error:
			self._set_error('resolve_magnet exception: %s' % error)
			if torrent_id and created_here and not store_to_cloud:
				self.delete_torrent(torrent_id)
			return None

	def display_magnet_pack(self, magnet_url, info_hash):
		torrent_id = None
		created_here = False
		try:
			normalized_hash = self._normalize_info_hash(info_hash) or self._extract_info_hash(magnet_url)
			torrent = self.find_torrent_by_hash(normalized_hash) if normalized_hash else None
			if torrent:
				torrent_id = torrent.get('id')
			else:
				added = self.add_magnet(magnet_url)
				if not isinstance(added, dict):
					return None
				if added.get('error_code') == 33:
					torrent = self.find_torrent_by_hash(normalized_hash)
					torrent_id = torrent.get('id') if torrent else None
				elif added.get('error') or not added.get('id'):
					return None
				else:
					torrent_id = added['id']
					created_here = True
			if not torrent_id:
				return None

			info = self._wait_for_file_list(torrent_id)
			if not info:
				return None
			if str(info.get('status') or '').lower() == 'waiting_files_selection':
				file_ids = self._video_file_ids(info)
				if not file_ids:
					return None
				selection = self.add_torrent_select(torrent_id, file_ids)
				if self._is_api_error(selection):
					return None
				info = self._wait_for_downloaded(torrent_id, max_wait_seconds=35)
			if not info:
				return None

			files = [item for item in info.get('files', []) if item.get('selected') == 1]
			links = info.get('links') or []
			items = []
			for index, item in enumerate(files):
				if index >= len(links):
					break
				items.append({
					'link': links[index],
					'filename': item.get('path', '').lstrip('/'),
					'size': item.get('bytes', 0)
				})
			return items
		except Exception as error:
			self._set_error('display_magnet_pack exception: %s' % error)
			return None
		finally:
			if torrent_id and created_here:
				self.delete_torrent(torrent_id)

	# -------------------------------------------------------------------------
	# File selection and torrent state helpers
	# -------------------------------------------------------------------------
	def _prepare_torrent_for_playback(self, torrent_id, title, season, episode, max_wait_seconds=35):
		deadline = time.monotonic() + max_wait_seconds
		selection_done = False
		last_status = None
		last_progress = None
		failure_states = {'magnet_error', 'error', 'virus', 'dead'}

		while time.monotonic() < deadline:
			info = self.torrent_info(torrent_id)
			if not isinstance(info, dict):
				self._set_error('torrents/info returned no JSON object')
				return None
			if info.get('error'):
				self._set_error(
					'torrents/info failed: %s' % info.get('error'),
					info.get('error_code'), info.get('http_status')
				)
				return None

			status = str(info.get('status') or '').lower()
			progress = info.get('progress')
			if status != last_status or progress != last_progress:
				self._log('torrent_id=%s status=%s progress=%s links=%s' % (
					torrent_id, status or 'unknown', progress, len(info.get('links') or [])
				))
				last_status, last_progress = status, progress

			if status in failure_states:
				self._set_error('Torrent entered failure state: %s' % status)
				return None

			if status == 'waiting_files_selection' and not selection_done:
				file_ids = self._target_file_ids(info, title, season, episode)
				if not file_ids:
					self._set_error('No matching supported video file found for selection')
					return None
				selection = self.add_torrent_select(torrent_id, file_ids)
				if self._is_api_error(selection):
					return None
				selection_done = True
				sleep(250)
				continue

			if status == 'downloaded' and info.get('links'):
				return info

			# A source that was falsely presented as cached will enter queued or
			# downloading. Continue briefly so very fast cached conversions can finish,
			# but do not block Kodi indefinitely.
			sleep(750)

		self._set_error(
			'Timed out after %ss waiting for a playable torrent (last_status=%s, progress=%s)'
			% (max_wait_seconds, last_status or 'unknown', last_progress)
		)
		return None

	def _wait_for_file_list(self, torrent_id, max_wait_seconds=20):
		deadline = time.monotonic() + max_wait_seconds
		failure_states = {'magnet_error', 'error', 'virus', 'dead'}
		while time.monotonic() < deadline:
			info = self.torrent_info(torrent_id)
			if not isinstance(info, dict) or info.get('error'):
				return None
			status = str(info.get('status') or '').lower()
			if info.get('files') or status == 'downloaded':
				return info
			if status in failure_states:
				self._set_error('Torrent entered failure state: %s' % status)
				return None
			sleep(500)
		self._set_error('Timed out waiting for torrent file metadata')
		return None

	def _wait_for_downloaded(self, torrent_id, max_wait_seconds=35):
		deadline = time.monotonic() + max_wait_seconds
		failure_states = {'magnet_error', 'error', 'virus', 'dead'}
		while time.monotonic() < deadline:
			info = self.torrent_info(torrent_id)
			if not isinstance(info, dict) or info.get('error'):
				return None
			status = str(info.get('status') or '').lower()
			if status == 'downloaded' and info.get('links'):
				return info
			if status in failure_states:
				return None
			sleep(750)
		return None

	def _video_file_ids(self, info):
		exts = self._extensions()
		return [str(item.get('id')) for item in info.get('files', [])
				if item.get('id') is not None and str(item.get('path') or '').lower().endswith(exts)]

	def _target_file_ids(self, info, title, season, episode):
		files = [item for item in info.get('files', []) if self._is_video_file(item)]
		if not files:
			return []
		if season not in (None, '', 0, '0'):
			matches = [item for item in files if seas_ep_filter(season, episode, item.get('path', ''))]
			matches = [item for item in matches if not self._is_extra_file(item.get('path', ''), title, season, episode)]
			if not matches:
				# Single-file episode torrents often omit SxxExx from the filename.
				if len(files) == 1:
					return [str(files[0]['id'])]
				return []
			matches.sort(key=lambda item: item.get('bytes', 0), reverse=True)
			return [str(matches[0]['id'])]

		candidates = [item for item in files if not self._is_extra_file(item.get('path', ''), title, season, episode)]
		if not candidates:
			candidates = files
		candidates.sort(key=lambda item: item.get('bytes', 0), reverse=True)
		return [str(candidates[0]['id'])]

	def _pick_selected_entry(self, selected_files, links, title, season, episode):
		entries = []
		for index, item in enumerate(selected_files):
			if index >= len(links):
				break
			if self._is_video_file(item):
				entries.append({'file': item, 'link': links[index]})
		if not entries:
			return None

		if season not in (None, '', 0, '0'):
			matches = [entry for entry in entries if seas_ep_filter(season, episode, entry['file'].get('path', ''))]
			matches = [entry for entry in matches if not self._is_extra_file(entry['file'].get('path', ''), title, season, episode)]
			if matches:
				matches.sort(key=lambda entry: entry['file'].get('bytes', 0), reverse=True)
				return matches[0]
			if len(entries) == 1:
				return entries[0]
			return None

		candidates = [entry for entry in entries if not self._is_extra_file(entry['file'].get('path', ''), title, season, episode)]
		if not candidates:
			candidates = entries
		candidates.sort(key=lambda entry: entry['file'].get('bytes', 0), reverse=True)
		return candidates[0]

	def _is_video_file(self, item):
		return str(item.get('path') or '').lower().endswith(self._extensions())

	def _extensions(self):
		return tuple(str(value).lower() for value in supported_video_extensions())

	def _is_extra_file(self, path, title, season, episode):
		path_lc = str(path or '').lower()
		base = path_lc.rsplit('/', 1)[-1]
		common = ('sample', 'trailer', 'featurette', 'behind.the.scenes', 'behind the scenes', 'extras')
		if any(value in base for value in common):
			return True
		try:
			if season not in (None, '', 0, '0'):
				compare = seas_ep_filter(season, episode, path, split=True)
			else:
				compare = base
			clean_title = re.sub(r'[^a-z0-9]+', '.', str(title or '').lower()).strip('.')
			if clean_title:
				compare = re.sub(re.escape(clean_title), '', str(compare).lower())
			return any(value in compare for value in extras())
		except Exception:
			return False

	def _m2ts_check(self, folder_details):
		# Kept for caller compatibility. M2TS is a valid video container and must
		# not be blanket-rejected solely because it is high-bitrate/Blu-ray media.
		return False

	def video_only(self, storage_variant, extensions):
		values = storage_variant.values()
		return not any(not item['filename'].lower().endswith(tuple(extensions)) for item in values)

	def name_check(self, storage_variant, season, episode, seas_ep_filter_func):
		return any(seas_ep_filter_func(season, episode, item['filename']) for item in storage_variant.values())

	def sort_cache_list(self, unsorted_list):
		return [item[0] for item in sorted(unsorted_list, key=lambda value: value[1], reverse=True)]

	# -------------------------------------------------------------------------
	# Hash helpers
	# -------------------------------------------------------------------------
	def _extract_info_hash(self, magnet):
		try:
			query = parse_qs(urlsplit(magnet).query)
			for xt in query.get('xt', []):
				match = re.search(r'urn:btih:([A-Za-z0-9]+)', xt, re.I)
				if match:
					return self._normalize_info_hash(match.group(1))
		except Exception:
			pass
		match = re.search(r'btih:([A-Za-z0-9]+)', magnet or '', re.I)
		return self._normalize_info_hash(match.group(1)) if match else None

	def _normalize_info_hash(self, value):
		value = str(value or '').strip().lower()
		if re.fullmatch(r'[0-9a-f]{40}', value):
			return value
		if re.fullmatch(r'[a-z2-7]{32}', value):
			try:
				return base64.b32decode(value.upper()).hex()
			except Exception:
				return None
		return value if value else None

	# -------------------------------------------------------------------------
	# HTTP
	# -------------------------------------------------------------------------
	def _is_api_error(self, payload):
		if not isinstance(payload, dict):
			return False
		code = payload.get('error_code')
		# selectFiles action_already_done is a successful terminal response.
		return bool(payload.get('error') and code != 31)

	def _request(self, method, endpoint, data=None, params=None, retry_auth=True):
		if self.token in ('empty_setting', ''):
			self._set_error('Request attempted without an access token')
			return None

		url = self.base_url + endpoint.lstrip('/')
		headers = {'Authorization': 'Bearer %s' % self.token}
		try:
			response = self.session.request(
				method,
				url,
				headers=headers,
				data=data,
				params=params,
				timeout=REQUEST_TIMEOUT
			)
		except Exception as error:
			self._set_error('%s %s network failure: %s' % (method, endpoint, error))
			return {'error': str(error), 'error_code': None, 'http_status': 0}

		if response.status_code == 204 or not response.content:
			return {}

		try:
			payload = response.json()
		except Exception:
			payload = {
				'error': response.text or 'HTTP %s' % response.status_code,
				'error_code': None
			}

		if isinstance(payload, dict):
			error_code = payload.get('error_code')
		else:
			error_code = None

		if retry_auth and (response.status_code == 401 or error_code == 8):
			if self.refresh_token():
				return self._request(method, endpoint, data=data, params=params, retry_auth=False)

		# selectFiles documents 202 as "Action already done". It may have an empty
		# body or error_code 31; both are successful for this operation.
		if endpoint.startswith('torrents/selectFiles/') and (
			response.status_code == 202 or error_code == 31
		):
			return {}

		if response.status_code >= 400 or (isinstance(payload, dict) and payload.get('error')):
			if not isinstance(payload, dict):
				payload = {'error': 'HTTP %s' % response.status_code, 'error_code': None}
			payload['http_status'] = response.status_code
			self._set_error(
				'%s %s failed: %s' % (method, endpoint, payload.get('error') or 'HTTP error'),
				payload.get('error_code'), response.status_code
			)
		return payload

	def _get(self, endpoint):
		return self._request('GET', endpoint)

	def _post(self, endpoint, post_data):
		return self._request('POST', endpoint, data=post_data)

	def _delete(self, endpoint):
		return self._request('DELETE', endpoint)

	# -------------------------------------------------------------------------
	# Local cache invalidation
	# -------------------------------------------------------------------------
	def clear_cache(self, clear_hashes=True):
		try:
			from caches.debrid_cache import debrid_cache
			from caches.base_cache import connect_database
			dbcon = connect_database('maincache_db')
			user_cloud_success = False
			try:
				try:
					cache = dbcon.execute(
						"""SELECT data FROM maincache WHERE id LIKE ?""",
						('rd_user_cloud_info_%',)
					).fetchall()
					user_cloud_info_caches = [eval(item[0])['id'] for item in cache]
				except Exception:
					user_cloud_success = True
				if not user_cloud_success:
					dbcon.execute("""DELETE FROM maincache WHERE id=?""", ('rd_user_cloud',))
					for item_id in user_cloud_info_caches:
						dbcon.execute(
							"""DELETE FROM maincache WHERE id=?""",
							('rd_user_cloud_info_%s' % item_id,)
						)
					user_cloud_success = True
			except Exception:
				user_cloud_success = False

			try:
				dbcon.execute("""DELETE FROM maincache WHERE id=?""", ('rd_downloads',))
				download_links_success = True
			except Exception:
				download_links_success = False

			if clear_hashes:
				try:
					debrid_cache.clear_debrid_results('rd')
					hash_cache_status_success = True
				except Exception:
					hash_cache_status_success = False
			else:
				hash_cache_status_success = True
		except Exception:
			return False
		return False not in (user_cloud_success, download_links_success, hash_cache_status_success)


RealDebrid = RealDebridAPI()