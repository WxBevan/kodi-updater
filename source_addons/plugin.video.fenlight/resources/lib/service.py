# -*- coding: utf-8 -*-
from xbmc import Monitor
import os
import json
import inspect
from time import time
from threading import Thread
from caches.settings_cache import get_setting, set_setting, sync_settings
from modules import kodi_utils

pause_services_prop = 'fenlight.pause_services'
firstrun_update_prop = 'fenlight.firstrun_update'
current_skin_prop = 'fenlight.current_skin'
trakt_service_string = 'TraktMonitor Service Update %s - %s'
trakt_success_line_dict = {'success': 'Trakt Update Performed', 'no account': '(Unauthorized) Trakt Update Performed'}
update_string = 'Next Update in %s minutes...'

class DeviceCapabilities:
	def run(self):
		kodi_utils.logger(
			'Fen Light',
			'DeviceCapabilities Service Starting'
		)

		try:
			if get_setting(
				'fenlight.device.auto_video_filter',
				'true'
			) == 'true':
				from modules.device_capabilities import (
					detect_device_capabilities
				)

				detect_device_capabilities()

		except Exception as exc:
			kodi_utils.logger(
				'Fen Light',
				'DeviceCapabilities Error: %s'
				% str(exc)
			)

		return kodi_utils.logger(
			'Fen Light',
			'DeviceCapabilities Service Finished'
		)


class SetAddonConstants:
	def run(self):
		kodi_utils.logger('Fen Light', 'SetAddonConstants Service Starting')
		import random
		addon_items = [('fenlight.addon_version', kodi_utils.addon_info('version')),
						('fenlight.addon_path', kodi_utils.addon_info('path')),
						('fenlight.addon_profile', kodi_utils.translate_path(kodi_utils.addon_info('profile'))),
						('fenlight.addon_icon', kodi_utils.translate_path(kodi_utils.addon_info('icon'))),
						('fenlight.addon_icon_mini', os.path.join(kodi_utils.addon_info('path'), 'resources', 'media', 'addon_icons', 'minis',
						os.path.basename(kodi_utils.translate_path(kodi_utils.addon_info('icon'))))),
						('fenlight.addon_fanart', kodi_utils.translate_path(kodi_utils.addon_info('fanart'))),
						('fenlight.playback_key', str(random.randint(1000, 10000)))]
		for item in addon_items: kodi_utils.set_property(*item)
		return kodi_utils.logger('Fen Light', 'SetAddonConstants Service Finished')


class DebridCacheWipe:
	def run(self):
		kodi_utils.logger('Fen Light', 'DebridCacheWipe Service Starting')

		try:
			from caches.base_cache import database_locations

			db_file = database_locations('debridcache_db')

			if kodi_utils.path_exists(db_file):
				kodi_utils.delete_file(db_file)
				kodi_utils.logger('Fen Light', 'DebridCacheWipe Deleted: %s' % db_file)
			else:
				kodi_utils.logger('Fen Light', 'DebridCacheWipe Not Found: %s' % db_file)

		except Exception as exc:
			kodi_utils.logger('Fen Light', 'DebridCacheWipe Error: %s' % str(exc))

		return kodi_utils.logger('Fen Light', 'DebridCacheWipe Service Finished')


class KodiDefaultSettings:
	def _jsonrpc(self, method, params=None):
		import json
		import xbmc

		request = {
			'jsonrpc': '2.0',
			'id': 1,
			'method': method
		}

		if params is not None:
			request['params'] = params

		response = xbmc.executeJSONRPC(json.dumps(request))
		kodi_utils.logger('Fen Light', 'KodiDefaultSettings JSON-RPC response: %s' % response)

		try:
			data = json.loads(response or '{}')
		except Exception:
			return False

		return not bool(data.get('error'))

	def _set_kodi_setting(self, setting_id, value):
		return self._jsonrpc('Settings.SetSettingValue', {
			'setting': setting_id,
			'value': value
		})

	def _set_language_english(self, setting_id):
		# Kodi can be a little picky depending on build/platform.
		# Try the friendly value first, then common lowercase/code fallbacks.
		for value in ('English', 'english', 'eng'):
			if self._set_kodi_setting(setting_id, value):
				kodi_utils.logger('Fen Light', 'KodiDefaultSettings set %s to %s' % (setting_id, value))
				return True

		kodi_utils.logger('Fen Light', 'KodiDefaultSettings could not set %s to English' % setting_id)
		return False

	def run(self):
		kodi_utils.logger('Fen Light', 'KodiDefaultSettings Service Starting')

		try:
			# Live TV / EPG defaults.
			self._set_kodi_setting('epg.selectaction', 1)

			# Player -> Language defaults.
			self._set_language_english('locale.audiolanguage')
			self._set_language_english('locale.subtitlelanguage')

		except Exception as exc:
			kodi_utils.logger('Fen Light', 'KodiDefaultSettings Error: %s' % str(exc))

		return kodi_utils.logger('Fen Light', 'KodiDefaultSettings Service Finished')


class DatabaseMaintenance:
	def run(self):
		kodi_utils.logger('Fen Light', 'DatabaseMaintenance Service Starting')
		from caches.base_cache import make_databases
		make_databases()
		return kodi_utils.logger('Fen Light', 'DatabaseMaintenance Service Finished')

class SyncSettings:
	def run(self):
		kodi_utils.logger('Fen Light', 'SyncSettings Service Starting')
		sync_settings()
		return kodi_utils.logger('Fen Light', 'SyncSettings Service Finished')

class OnUpdateChanges:
	def run(self):
		kodi_utils.logger('Fen Light', 'OnUpdateChanges Service Starting')
		try:
			for method in list(filter(lambda x: x[0] != 'run', inspect.getmembers(OnUpdateChanges, predicate=inspect.isfunction))):
				if not get_setting('fenlight.updatechecks.%s' % method[0], 'false') == 'true':
					method[1](self)
					set_setting('updatechecks.%s' % method[0], 'true')
		except: pass
		return kodi_utils.logger('Fen Light', 'OnUpdateChanges Service Finished')

	def context_menu_update_03(self):
		from caches.settings_cache import default_setting_values
		set_setting('context_menu.order', default_setting_values('context_menu.order')['setting_default'])
		set_setting('extras.enabled', default_setting_values('extras.enabled')['setting_default'])
	
	def trakt_watched_progress_update_01(self):
		from caches.trakt_cache import clear_trakt_activity

		if clear_trakt_activity():
			kodi_utils.logger(
				'Fen Light',
				'Cleared old Trakt activity marker '
				'for watched progress API update'
			)
		else:
			kodi_utils.logger(
				'Fen Light',
				'Could not clear old Trakt activity marker'
			)

class CustomFonts:
	def run(self):
		kodi_utils.logger('Fen Light', 'CustomFonts Service Starting')
		from windows.base_window import FontUtils
		monitor, player = kodi_utils.kodi_monitor(), kodi_utils.kodi_player()
		wait_for_abort, is_playing = monitor.waitForAbort, player.isPlayingVideo
		kodi_utils.clear_property(current_skin_prop)
		font_utils = FontUtils()
		while not monitor.abortRequested():
			font_utils.execute_custom_fonts()
			wait_for_abort(20)
		try: del monitor
		except: pass
		try: del player
		except: pass
		return kodi_utils.logger('Fen Light', 'CustomFonts Service Finished')

class TraktMonitor:
	def run(self):
		kodi_utils.logger('Fen Light', 'TraktMonitor Service Starting')
		from apis.trakt_api import trakt_sync_activities
		from modules.settings import trakt_sync_interval
		monitor, player = kodi_utils.kodi_monitor(), kodi_utils.kodi_player()
		wait_for_abort, is_playing = monitor.waitForAbort, player.isPlayingVideo
		while not monitor.abortRequested():
			while is_playing() or kodi_utils.get_property(pause_services_prop) == 'true': wait_for_abort(10)
			wait_time = 1800
			try:
				sync_interval, wait_time = trakt_sync_interval()
				next_update_string = update_string % sync_interval
				status = trakt_sync_activities()
				if status == 'failed': kodi_utils.logger('Fen Light', trakt_service_string % ('Failed. Error from Trakt', next_update_string))
				else:
					if status in ('success', 'no account'): kodi_utils.logger('Fen Light', trakt_service_string % ('Success. %s' % trakt_success_line_dict[status], next_update_string))
					else: kodi_utils.logger('Fen Light', trakt_service_string % ('Success. No Changes Needed', next_update_string))# 'not needed'
					if status == 'success' and get_setting('fenlight.trakt.refresh_widgets', 'false') == 'true': kodi_utils.run_plugin({'mode': 'kodi_refresh'})
			except Exception as e: kodi_utils.logger('Fen Light', trakt_service_string % ('Failed', 'The following Error Occured: %s' % str(e)))
			wait_for_abort(wait_time)
		try: del monitor
		except: pass
		try: del player
		except: pass
		return kodi_utils.logger('Fen Light', 'TraktMonitor Service Finished')

class UpdateCheck:
	def run(self):
		if kodi_utils.get_property(firstrun_update_prop) == 'true': return
		kodi_utils.logger('Fen Light', 'UpdateCheck Service Starting')
		from modules.updater import update_check
		from modules.settings import update_action, update_delay
		end_pause = time() + update_delay()
		monitor, player = kodi_utils.kodi_monitor(), kodi_utils.kodi_player()
		wait_for_abort, is_playing = monitor.waitForAbort, player.isPlayingVideo
		while not monitor.abortRequested():
			while time() < end_pause: wait_for_abort(1)
			while kodi_utils.get_property(pause_services_prop) == 'true' or is_playing(): wait_for_abort(1)
			update_check(update_action())
			break
		kodi_utils.set_property(firstrun_update_prop, 'true')
		try: del monitor
		except: pass
		try: del player
		except: pass
		return kodi_utils.logger('Fen Light', 'UpdateCheck Service Finished')

class WidgetRefresher:
	def run(self):
		kodi_utils.logger('Fen Light', 'WidgetRefresher Service Starting')
		from time import time
		from indexers.random_lists import refresh_widgets
		monitor, player = kodi_utils.kodi_monitor(), kodi_utils.kodi_player()
		wait_for_abort, self.is_playing = monitor.waitForAbort, player.isPlayingVideo
		wait_for_abort(10)
		self.set_next_refresh(time())
		while not monitor.abortRequested():
			try:
				wait_for_abort(10)
				offset = int(get_setting('fenlight.widget_refresh_timer', '60'))
				if offset != self.offset:
					self.set_next_refresh(time())
					continue
				if self.condition_check(): continue
				if self.next_refresh < time():
					kodi_utils.logger('Fen Light', 'WidgetRefresher Service - Widgets Refreshed')
					refresh_widgets()
					self.set_next_refresh(time())
			except: pass
		try: del monitor
		except: pass
		try: del player
		except: pass
		return kodi_utils.logger('Fen Light', 'WidgetRefresher Service Finished')

	def condition_check(self):
		if not self.external(): return True

		if self.next_refresh == None or self.is_playing() or kodi_utils.get_property(pause_services_prop) == 'true': return True
		if kodi_utils.get_property('fenlight.window_loaded') == 'true': return True 
		try:
			window_stack = json.loads(kodi_utils.get_property('fenlight.window_stack'))
			if window_stack or window_stack == []: return True
		except: pass
		return False

	def set_next_refresh(self, _time):
		self.offset = int(get_setting('fenlight.widget_refresh_timer', '60'))
		if self.offset: self.next_refresh = _time + (self.offset*60)
		else: self.next_refresh = None

	def external(self):
		return 'plugin' not in kodi_utils.get_infolabel('Container.PluginName')

class AutoStart:
	def run(self):
		kodi_utils.logger('Fen Light', 'AutoStart Service Starting')
		from modules.settings import auto_start_fenlight
		if auto_start_fenlight(): kodi_utils.run_addon()
		return kodi_utils.logger('Fen Light', 'AutoStart Service Finished')

class AddonXMLCheck:
	def run(self):
		kodi_utils.logger('Fen Light', 'AddonXMLCheck Service Starting')
		from xml.dom.minidom import parse as mdParse
		self.addon_xml = kodi_utils.translate_path('special://home/addons/plugin.video.fenlight/addon.xml')
		self.root = mdParse(self.addon_xml)
		self.change_file = False
		self.check_property('reuse_language_invoker', 'reuselanguageinvoker')
		self.check_property('addon_icon_choice', 'icon')
		self.change_xml_file()
		return kodi_utils.logger('Fen Light', 'AddonXMLCheck Service Finished')

	def check_property(self, setting, tag_name):
		current_addon_setting = get_setting('fenlight.%s' % setting, None)
		if current_addon_setting is None: return
		tag_instance = self.root.getElementsByTagName(tag_name)[0].firstChild
		current_property = tag_instance.data
		if current_property != current_addon_setting:
			tag_instance.data = current_addon_setting
			self.change_file = True

	def change_xml_file(self):
		if not self.change_file: return
		kodi_utils.notification('Refreshing Addon XML After Update. Restarting Addons')
		new_xml = str(self.root.toxml()).replace('<?xml version="1.0" ?>', '')
		with open(self.addon_xml, 'w') as f: f.write(new_xml)
		kodi_utils.logger('Fen Light', 'AddonXMLCheck Service - Change Detected. Restarting Addons')
		kodi_utils.execute_builtin('ActivateWindow(Home)', True)
		kodi_utils.update_local_addons()
		kodi_utils.disable_enable_addon()


class IPTVEPGRefresh:
	def run(self):
		kodi_utils.logger('Fen Light', 'IPTVEPGRefresh Service Starting')
		monitor, player = kodi_utils.kodi_monitor(), kodi_utils.kodi_player()
		wait_for_abort, is_playing = monitor.waitForAbort, player.isPlayingVideo

		# Let Kodi, PVR, network and Fen Light startup settle first.
		if wait_for_abort(60):
			return kodi_utils.logger('Fen Light', 'IPTVEPGRefresh Service Aborted Before First Run')

		while not monitor.abortRequested():
			try:
				# Do not refresh while playback is active or services are paused for sleep.
				if is_playing() or kodi_utils.get_property(pause_services_prop) == 'true':
					wait_for_abort(300)
					continue

				from modules import iptv_generator
				result = iptv_generator.refresh_epg_if_needed(max_age_hours=12, reload_pvr=True)
				if isinstance(result, dict) and result.get('skipped'):
					kodi_utils.logger('Fen Light', 'IPTVEPGRefresh Skipped: %s' % result.get('reason', 'not needed'))
				else:
					kodi_utils.logger('Fen Light', 'IPTVEPGRefresh Complete: %s' % str(result))
			except Exception as exc:
				kodi_utils.logger('Fen Light', 'IPTVEPGRefresh Error: %s' % str(exc))

			# Check hourly, but refresh_epg_if_needed only does real work when stale.
			if wait_for_abort(3600):
				break

		try: del monitor
		except: pass
		try: del player
		except: pass
		return kodi_utils.logger('Fen Light', 'IPTVEPGRefresh Service Finished')


class FenLightMonitor(Monitor):
	def __init__ (self):
		Monitor.__init__(self)
		self.startServices()

	def startServices(self):
		SetAddonConstants().run()
		KodiDefaultSettings().run()
		DebridCacheWipe().run()
		DatabaseMaintenance().run()
		SyncSettings().run()
		OnUpdateChanges().run()
		DeviceCapabilities().run()
		AddonXMLCheck().run()
		Thread(target=CustomFonts().run).start()
		Thread(target=TraktMonitor().run).start()
		## FLAM private build: disable built-in Fen Light GitHub updater.
		## Thread(target=UpdateCheck().run).start()
		Thread(target=WidgetRefresher().run).start()
		Thread(target=IPTVEPGRefresh().run).start()
		AutoStart().run()

	def onNotification(self, sender, method, data):
		if method in ('GUI.OnScreensaverActivated', 'System.OnSleep'):
			kodi_utils.set_property(pause_services_prop, 'true')
			kodi_utils.logger('OnNotificationActions', 'PAUSING Fen Light Services Due to Device Sleep')
		elif method in ('GUI.OnScreensaverDeactivated', 'System.OnWake'):
			kodi_utils.clear_property(pause_services_prop)
			kodi_utils.logger('OnNotificationActions', 'UNPAUSING Fen Light Services Due to Device Awake')

kodi_utils.logger('Fen Light', 'Main Monitor Service Starting')
FenLightMonitor().waitForAbort()
kodi_utils.logger('Fen Light', 'Main Monitor Service Finished')


