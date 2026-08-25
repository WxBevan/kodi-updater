# -*- coding: utf-8 -*-
import time
from modules.kodi_utils import addon_fanart
from windows.base_window import BaseDialog
from modules.settings import avoid_episode_spoilers
# from modules.kodi_utils import logger

class NextEpisode(BaseDialog):
	episode_status_dict = {
		'season_premiere': ('Season Premiere', 'b30385b5'),
		'mid_season_premiere': ('Mid-Season Premiere', 'b385b503'),
		'series_finale': ('Series Finale', 'b38503b5'),
		'season_finale': ('Season Finale', 'b3b50385'),
		'mid_season_finale': ('Mid-Season Finale', 'b3b58503'),
		'': (None, None)}

	def __init__(self, *args, **kwargs):
		BaseDialog.__init__(self, *args)
		self.closed = False
		self.meta = kwargs.get('meta') or {}
		self.selected = kwargs.get('default_action', 'play')
		if self.selected not in ('play', 'cancel', 'pause'): self.selected = 'play'
		self.set_properties()

	def onInit(self):
		self.setFocusId(11)
		self.monitor()

	def run(self):
		self.doModal()
		self.clearProperties()
		self.clear_modals()
		return self.selected

	def onAction(self, action):
		if action in self.closing_actions:
			self.selected = 'cancel'
			self.closed = True
			self.close()

	def onClick(self, controlID):
		if controlID == 11: self.selected = 'play'
		elif controlID == 12: self.selected = 'cancel'
		else: return
		self.closed = True
		self.close()

	def set_properties(self):
		self.setProperty('thumb', self.get_thumb())
		try:
			self.setProperty('episode_number', 'S%02dE%02d' % (int(self.meta['season']), int(self.meta['episode'])))
		except:
			self.setProperty('episode_number', '')
		self.setProperty('episode_name', self.meta.get('ep_name', ''))
		self.setProperty('show_name', self.meta.get('title', ''))
		status_label, status_highlight = self.episode_status_dict.get(self.meta.get('episode_type', ''), (None, None))
		if status_label:
			self.setProperty('episode_status.label', status_label)
			self.setProperty('episode_status.highlight', status_highlight)

	def get_thumb(self):
		if avoid_episode_spoilers(): return self.meta.get('fanart', '') or addon_fanart()
		return self.meta.get('ep_thumb') or self.meta.get('fanart', '') or addon_fanart()

	def monitor(self):
		total_time = self.player.getTotalTime()
		while self.player.isPlaying():
			if self.closed: break
			try:
				remaining_time = round(total_time - self.player.getTime())
			except:
				remaining_time = 0
			if self.selected == 'pause' and remaining_time <= 10:
				self.player.pause()
				self.sleep(500)
				break
			self.sleep(500)
		if self.selected == 'pause':
			# Preserve the existing Pause & Wait default-action setting even though the
			# visible dialog itself now has only Play and Cancel buttons.
			start_time = time.time()
			while time.time() - start_time < 900 and self.selected == 'pause': self.sleep(1000)
			if self.selected != 'cancel': self.player.pause()
		self.close()

class StingersNotification(BaseDialog):
	def __init__(self, *args, **kwargs):
		BaseDialog.__init__(self, *args)
		self.stinger_dict = {'duringcreditsstinger': {'id': 200, 'property': 'color_during'}, 'aftercreditsstinger': {'id': 201, 'property': 'color_after'}}
		self.closed = False
		self.meta = kwargs.get('meta')
		self.stingers = self.meta.get('stinger_keys')
		self.set_properties()

	def onInit(self):
		self.make_stingers()
		self.monitor()

	def run(self):
		self.doModal()
		self.clearProperties()
		self.clear_modals()

	def onAction(self, action):
		if action in self.closing_actions:
			self.closed = True
			self.close()

	def make_stingers(self):
		for k, v in self.stinger_dict.items():
			if k in self.stingers:
				self.setProperty(v['property'], 'green')
				self.set_image(v['id'], 'fenlight_common/overlay_selected.png')
			else:
				self.setProperty(v['property'], 'red')
				self.set_image(v['id'], 'fenlight_common/cross.png')

	def set_properties(self):
		self.setProperty('mode', 'stinger')
		self.setProperty('thumb', self.meta.get('fanart', '')) or addon_fanart()
		self.setProperty('clearlogo', self.meta.get('clearlogo', ''))

	def monitor(self):
		total_time = 10000
		while self.player.isPlaying() and total_time > 0:
			if self.closed: break
			self.sleep(1000)
			total_time -= 1000
		self.close()