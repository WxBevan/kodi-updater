# -*- coding: utf-8 -*-
import re
import time
import requests
from threading import Thread
from caches.main_cache import cache_object
from caches.settings_cache import get_setting, set_setting
from modules.utils import copy2clip, make_tinyurl, make_qrcode
from modules.source_utils import supported_video_extensions, seas_ep_filter, extras
from modules.kodi_utils import sleep, ok_dialog, progress_dialog, notification
# from modules.kodi_utils import logger

class RealDebridAPI:
	def __init__(self):
		self.client_ID = get_setting('fenlight.rd.client_id', 'empty_setting')
		if self.client_ID in ('empty_setting', ''): self.client_ID = 'X245A4XAIBGVM'
		url = {'true': 'app.real-debrid.com', 'false': 'api.real-debrid.com'}[get_setting('fenlight.rd.alternate_base_url', 'false')]
		self.base_url = 'https://%s/rest/1.0/' % url
		self.auth_url = 'https://%s/oauth/v2/' % url
		self.token = get_setting('fenlight.rd.token', 'empty_setting')
		self.secret = get_setting('fenlight.rd.secret', 'empty_setting')
		self.refresh = get_setting('fenlight.rd.refresh', 'empty_setting')
		self.device_code = ''
		self.refresh_retries = 0
		self.break_auth_loop = False
		self.last_error = None

	def auth(self):
		self.secret = ''
		self.client_ID = 'X245A4XAIBGVM'
		device_url = self.auth_url + 'device/code'
		try:
			response = requests.get(
				device_url,
				params={'client_id': self.client_ID, 'new_credentials': 'yes'},
				timeout=20
			)
			response.raise_for_status()
			response = response.json()
		except Exception as error:
			self.last_error = 'Real-Debrid device authorization failed: %s' % error
			ok_dialog(text=self.last_error)
			return False

		user_code = response.get('user_code')
		device_code = response.get('device_code')
		verification_url = response.get('direct_verification_url') or response.get('verification_url')
		if not user_code or not device_code or not verification_url:
			self.last_error = 'Real-Debrid returned incomplete device authorization data.'
			ok_dialog(text=self.last_error)
			return False

		qr_code = make_qrcode(verification_url) or ''
		short_url = make_tinyurl(verification_url)
		copy2clip(verification_url)
		if short_url:
			p_dialog_insert = 'OR visit this URL: [B]%s[/B][CR]OR Enter this Code: [B]%s[/B]' % (short_url, user_code)
		else:
			p_dialog_insert = 'OR Enter this Code: [B]%s[/B]' % user_code
		content = 'Please Scan the QR Code%s[CR]' % p_dialog_insert
		progressDialog = progress_dialog('Real Debrid Authorize', qr_code)
		progressDialog.update(content, 0)

		expires_in = int(response.get('expires_in') or 1800)
		sleep_interval = max(1, int(response.get('interval') or 5))
		poll_url = self.auth_url + 'device/credentials'
		start = time.time()

		while not progressDialog.iscanceled() and (time.time() - start) < expires_in and not self.secret:
			sleep(1000 * sleep_interval)
			try:
				credentials_response = requests.get(
					poll_url,
					params={'client_id': self.client_ID, 'code': device_code},
					timeout=20
				)
				credentials = credentials_response.json()
			except Exception:
				continue

			if credentials_response.status_code >= 400 or credentials.get('error'):
				time_passed = time.time() - start
				progress = min(100, int(100 * time_passed / float(expires_in)))
				progressDialog.update(content, progress)
				continue

			self.client_ID = credentials.get('client_id') or ''
			self.secret = credentials.get('client_secret') or ''
			if self.client_ID and self.secret:
				set_setting('rd.client_id', self.client_ID)
				set_setting('rd.secret', self.secret)
				break

		try:
			progressDialog.close()
		except Exception:
			pass

		if not self.secret:
			return False

		data = {
			'client_id': self.client_ID,
			'client_secret': self.secret,
			'code': device_code,
			'grant_type': 'http://oauth.net/grant_type/device/1.0'
		}
		try:
			token_response = requests.post(self.auth_url + 'token', data=data, timeout=20)
			token_data = token_response.json()
			if token_response.status_code >= 400 or token_data.get('error'):
				raise RuntimeError(token_data.get('error') or 'HTTP %s' % token_response.status_code)
			self.token = token_data['access_token']
			self.refresh = token_data['refresh_token']
			set_setting('rd.token', self.token)
			set_setting('rd.refresh', self.refresh)
			account = self.account_info() or {}
			username = account.get('username', '') if isinstance(account, dict) else ''
			set_setting('rd.account_id', username)
			set_setting('rd.enabled', 'true')
			ok_dialog(text='Success')
			return True
		except Exception as error:
			self.last_error = 'Real-Debrid token authorization failed: %s' % error
			ok_dialog(text=self.last_error)
			return False

	def refresh_token(self):
		if self.refresh in ('empty_setting', '') or self.secret in ('empty_setting', ''):
			return False
		try:
			url = self.auth_url + 'token'
			data = {
				'client_id': self.client_ID,
				'client_secret': self.secret,
				'code': self.refresh,
				'grant_type': 'http://oauth.net/grant_type/device/1.0'
			}
			response = requests.post(url, data=data, timeout=20)
			payload = response.json()
			if response.status_code >= 400 or payload.get('error'):
				self.last_error = payload.get('error') or 'HTTP %s' % response.status_code
				return False
			self.token = payload['access_token']
			self.refresh = payload['refresh_token']
			set_setting('rd.token', self.token)
			set_setting('rd.refresh', self.refresh)
			return True
		except Exception as error:
			self.last_error = str(error)
			return False

	def revoke(self):
		set_setting('rd.client_id', 'empty_setting')
		set_setting('rd.secret', 'empty_setting')
		set_setting('rd.refresh', 'empty_setting')
		set_setting('rd.token', 'empty_setting')
		set_setting('rd.account_id', 'empty_setting')
		set_setting('rd.enabled', 'false')
		notification('Real Debrid Authorization Reset', 3000)

	def account_info(self):
		url = 'user'
		return self._get(url)

	def check_cache(self, hashes):
		hash_string = '/'.join(hashes)
		url = 'torrents/instantAvailability/%s' % hash_string
		result = self._get(url)
		if not isinstance(result, dict) or result.get('error'):
			return {}
		return result

	def check_hash(self, hash_string):
		url = 'torrents/instantAvailability/%s' % hash_string
		result = self._get(url)
		if not isinstance(result, dict) or result.get('error'):
			return {}
		return result

	def check_single_magnet(self, hash_string):
		cache_info = self.check_hash(hash_string)
		if not isinstance(cache_info, dict):
			return False
		info = cache_info.get(hash_string) or cache_info.get(hash_string.lower()) or {}
		if not isinstance(info, dict):
			return False
		rd = info.get('rd') or []
		return bool(rd)

	def torrents_activeCount(self):
		url = 'torrents/activeCount'
		return self._get(url)

	def user_cloud(self):
		string = 'rd_user_cloud'
		url = 'torrents?limit=500'
		return cache_object(self._get, string, url, False, 0.03)

	def user_cloud_check(self):
		url = 'torrents?limit=500'
		return self._get(url)

	def downloads(self):
		string = 'rd_downloads'
		url = 'downloads?limit=500'
		return cache_object(self._get, string, url, False, 0.03)

	def user_cloud_info(self, file_id):
		string = 'rd_user_cloud_info_%s' % file_id
		url = 'torrents/info/%s' % file_id
		return cache_object(self._get, string, url, False, 0.03)

	def user_cloud_info_check(self, file_id):
		url = 'torrents/info/%s' % file_id
		return self._get(url)

	def torrent_info(self, file_id):
		url = 'torrents/info/%s' % file_id
		return self._get(url)

	def unrestrict_link(self, link):
		url = 'unrestrict/link'
		post_data = {'link': link}
		response = self._post(url, post_data)
		if not isinstance(response, dict) or response.get('error'):
			return None
		return response.get('download')

	def add_magnet(self, magnet):
		post_data = {'magnet': magnet}
		url = 'torrents/addMagnet'
		result = self._post(url, post_data)
		return result

	def create_transfer(self, magnet_url):
		torrent_id = None
		try:
			torrent = self.add_magnet(magnet_url)
			if not isinstance(torrent, dict):
				self.last_error = 'Real-Debrid addMagnet returned no JSON object.'
				return 'failed'
			if torrent.get('error') or not torrent.get('id'):
				self.last_error = torrent.get('error') or 'Real-Debrid addMagnet returned no torrent id.'
				return 'failed'

			torrent_id = torrent['id']
			selection = self.add_torrent_select(torrent_id, 'all')
			if isinstance(selection, dict) and selection.get('error'):
				self.last_error = selection.get('error')
				self.delete_torrent(torrent_id)
				return 'failed'
			return 'success'
		except Exception as error:
			self.last_error = str(error)
			if torrent_id:
				self.delete_torrent(torrent_id)
			return 'failed'

	def add_torrent_select(self, torrent_id, file_ids):
		self.clear_cache(clear_hashes=False)
		url = 'torrents/selectFiles/%s' % torrent_id
		post_data = {'files': file_ids}
		return self._post(url, post_data)

	def delete_torrent(self, folder_id):
		if not folder_id or self.token in ('empty_setting', ''):
			return None
		return self._delete('torrents/delete/%s' % folder_id)

	def delete_download(self, download_id):
		if not download_id or self.token in ('empty_setting', ''):
			return None
		return self._delete('downloads/delete/%s' % download_id)

	def resolve_magnet(self, magnet_url, info_hash, store_to_cloud, title, season, episode):
		compare_title = re.sub(r'[^A-Za-z0-9]+', '.', title.replace('\'', '').replace('&', 'and').replace('%', '.percent')).lower()
		extensions = supported_video_extensions()
		torrent_id = None
		try:
			torrent = self.add_magnet(magnet_url)
			if not isinstance(torrent, dict) or torrent.get('error') or not torrent.get('id'):
				self.last_error = torrent.get('error') if isinstance(torrent, dict) else 'addMagnet failed'
				return None

			torrent_id = torrent['id']
			selection = self.add_torrent_select(torrent_id, 'all')
			if isinstance(selection, dict) and selection.get('error'):
				self.last_error = selection.get('error')
				self.delete_torrent(torrent_id)
				return None

			torrent_info = self._wait_for_torrent_links(torrent_id)
			if not torrent_info:
				self.delete_torrent(torrent_id)
				return None

			all_selected = [item for item in torrent_info.get('files', []) if item.get('selected') == 1]
			links = torrent_info.get('links') or []
			entries = []
			for selected_index, item in enumerate(all_selected):
				if selected_index >= len(links):
					break
				if item.get('path', '').lower().endswith(tuple(extensions)):
					entries.append((selected_index, item, links[selected_index]))
			entries.sort(key=lambda value: value[1].get('bytes', 0), reverse=True)
			if not entries:
				self.delete_torrent(torrent_id)
				return None

			selected_entry = None
			if season:
				for entry in entries:
					item = entry[1]
					if not seas_ep_filter(season, episode, item.get('path', '')):
						continue
					compare_link = seas_ep_filter(season, episode, item.get('path', ''), split=True)
					compare_link = re.sub(compare_title, '', compare_link)
					if any(value in compare_link for value in extras()):
						continue
					selected_entry = entry
					break
			else:
				if self._m2ts_check([(entry[0], entry[1]) for entry in entries]):
					self.delete_torrent(torrent_id)
					return None
				for entry in entries:
					item = entry[1]
					filename = re.sub(
						r'[^A-Za-z0-9-]+',
						'.',
						item.get('path', '').rsplit('/', 1)[-1].replace('\'', '').replace('&', 'and').replace('%', '.percent')
					).lower()
					filename_info = filename.replace(compare_title, '')
					if any(value in filename_info for value in extras()):
						continue
					selected_entry = entry
					break

			if not selected_entry:
				self.delete_torrent(torrent_id)
				return None

			file_url = self.unrestrict_link(selected_entry[2])
			if not file_url:
				self.delete_torrent(torrent_id)
				return None
			path_only = file_url.split('?', 1)[0].lower()
			if path_only.endswith('.rar') or not any(path_only.endswith(ext.lower()) for ext in extensions):
				self.delete_torrent(torrent_id)
				return None

			if not store_to_cloud:
				Thread(target=self.delete_torrent, args=(torrent_id,)).start()
			return file_url
		except Exception as error:
			self.last_error = str(error)
			if torrent_id:
				self.delete_torrent(torrent_id)
			return None

	def display_magnet_pack(self, magnet_url, info_hash):
		torrent_id = None
		try:
			torrent = self.add_magnet(magnet_url)
			if not isinstance(torrent, dict) or torrent.get('error') or not torrent.get('id'):
				self.last_error = torrent.get('error') if isinstance(torrent, dict) else 'addMagnet failed'
				return None

			torrent_id = torrent['id']
			selection = self.add_torrent_select(torrent_id, 'all')
			if isinstance(selection, dict) and selection.get('error'):
				self.last_error = selection.get('error')
				self.delete_torrent(torrent_id)
				return None

			torrent_info = self._wait_for_torrent_links(torrent_id)
			if not torrent_info:
				self.delete_torrent(torrent_id)
				return None

			files = [item for item in torrent_info.get('files', []) if item.get('selected') == 1]
			links = torrent_info.get('links') or []
			list_file_items = []
			for index, item in enumerate(files):
				if index >= len(links):
					break
				list_file_items.append({
					'link': links[index],
					'filename': item.get('path', '').replace('/', ''),
					'size': item.get('bytes', 0)
				})
			self.delete_torrent(torrent_id)
			return list_file_items
		except Exception as error:
			self.last_error = str(error)
			if torrent_id:
				self.delete_torrent(torrent_id)
			return None

	def video_only(self, storage_variant, extensions):
		values = storage_variant.values()
		return False if len([i for i in values if not i['filename'].lower().endswith(tuple(extensions))]) > 0 else True

	def name_check(self, storage_variant, season, episode, seas_ep_filter):
		values = storage_variant.values()
		return len([i for i in values if seas_ep_filter(season, episode, i['filename'])]) > 0

	def sort_cache_list(self, unsorted_list):
		sorted_list = sorted(unsorted_list, key=lambda x: x[1], reverse=True)
		return [i[0] for i in sorted_list]

	def _m2ts_check(self, folder_details):
		for idx, item in folder_details:
			if item['path'].endswith('.m2ts'): return True
		return False

	def _wait_for_torrent_links(self, torrent_id, attempts=20, interval_ms=500):
		"""Poll the torrent itself; /activeCount only returns nb/limit, not torrent ids."""
		failure_states = {
			'magnet_error', 'error', 'virus', 'dead',
	}
		last_info = None
		for _attempt in range(attempts):
			info = self.torrent_info(torrent_id)
			if not isinstance(info, dict) or info.get('error'):
				self.last_error = info.get('error') if isinstance(info, dict) else 'torrent info failed'
				return None
			last_info = info
			status = str(info.get('status') or '').lower()
			if info.get('links'):
				return info
			if status in failure_states:
				self.last_error = 'Torrent entered failure state: %s' % status
				return None
			sleep(interval_ms)
		self.last_error = 'Timed out waiting for Real-Debrid torrent links.'
		return last_info if isinstance(last_info, dict) and last_info.get('links') else None

	def _request(self, method, endpoint, data=None, retry_auth=True):
		if self.token in ('empty_setting', ''):
			return None

		url = self.base_url + endpoint.lstrip('/')
		headers = {'Authorization': 'Bearer %s' % self.token}
		try:
			response = requests.request(method, url, headers=headers, data=data, timeout=20)
		except Exception as error:
			self.last_error = str(error)
			try:
				print('[RealDebridAPI] %s %s failed: %s' % (method, endpoint, error))
			except Exception:
				pass
			return {'error': str(error), 'error_code': None, 'http_status': 0}

		if response.status_code == 204 or not response.content:
			payload = {}
		else:
			try:
				payload = response.json()
			except Exception:
				payload = {
					'error': response.text or 'HTTP %s' % response.status_code,
					'error_code': None,
				}

		error_code = payload.get('error_code') if isinstance(payload, dict) else None
		if response.status_code == 202 and error_code == 31:
			return {}
		if retry_auth and (response.status_code == 401 or error_code == 8):
			if self.refresh_token():
				return self._request(method, endpoint, data=data, retry_auth=False)

		if response.status_code >= 400:
			if not isinstance(payload, dict):
				payload = {'error': 'HTTP %s' % response.status_code}
			payload.setdefault('error', 'HTTP %s' % response.status_code)
			payload['http_status'] = response.status_code
			self.last_error = payload.get('error')
			try:
				print(
					'[RealDebridAPI] %s %s failed: HTTP %s, error=%s, error_code=%s'
					% (method, endpoint, response.status_code, payload.get('error'), payload.get('error_code'))
				)
			except Exception:
				pass
		return payload

	def _get(self, url):
		return self._request('GET', url)

	def _post(self, url, post_data):
		return self._request('POST', url, data=post_data)

	def _delete(self, url):
		return self._request('DELETE', url)

	def clear_cache(self, clear_hashes=True):
		try:
			from caches.debrid_cache import debrid_cache
			from caches.base_cache import connect_database
			dbcon = connect_database('maincache_db')
			user_cloud_success = False
			# USER CLOUD
			try:
				try:
					cache = dbcon.execute("""SELECT data FROM maincache WHERE id LIKE ?""", ('rd_user_cloud_info_%',)).fetchall()
					user_cloud_info_caches = [eval(i[0])['id'] for i in cache]
				except:
					user_cloud_success = True
				if not user_cloud_success:
					dbcon.execute("""DELETE FROM maincache WHERE id=?""", ('rd_user_cloud',))
					for i in user_cloud_info_caches:
						dbcon.execute("""DELETE FROM maincache WHERE id=?""", ('rd_user_cloud_info_%s' % i,))
					user_cloud_success = True
			except: user_cloud_success = False
			# DOWNLOAD LINKS
			try:
				dbcon.execute("""DELETE FROM maincache WHERE id=?""", ('rd_downloads',))
				download_links_success = True
			except: download_links_success = False
			# HASH CACHED STATUS
			if clear_hashes:
				try:
					debrid_cache.clear_debrid_results('rd')
					hash_cache_status_success = True
				except: hash_cache_status_success = False
			else: hash_cache_status_success = True
		except: return False
		if False in (user_cloud_success, download_links_success, hash_cache_status_success): return False
		return True

RealDebrid = RealDebridAPI()