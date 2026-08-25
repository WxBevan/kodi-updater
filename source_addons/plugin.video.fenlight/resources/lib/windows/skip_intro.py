# -*- coding: utf-8 -*-
from windows.base_window import BaseDialog

kind_labels = {'intro': 'Skip Intro', 'recap': 'Skip Recap', 'outro': 'Skip Outro'}

class SkipIntro(BaseDialog):
	def __init__(self, *args, **kwargs):
		BaseDialog.__init__(self, *args)
		self.closed = False
		self.selected = 'ignore'
		self.kind = kwargs.get('kind', 'intro')
		self.dismiss_seconds = max(1, int(kwargs.get('seconds', 8)))
		self.meta = kwargs.get('meta') or {}
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
			self.closed = True
			self.close()

	def onClick(self, controlID):
		if controlID == 11: self.selected = 'skip'
		self.closed = True
		self.close()

	def set_properties(self):
		self.setProperty('skip_label', kind_labels.get(self.kind, 'Skip'))
		self.setProperty('dismiss_label', 'Close')
		try:
			self.setProperty('episode_label', '%s[B] | [/B]%02dx%02d[B] | [/B]%s' %
				(self.meta['title'], int(self.meta['season']), int(self.meta['episode']), self.meta['ep_name']))
		except:
			self.setProperty('episode_label', self.meta.get('title', ''))

	def monitor(self):
		remaining = self.dismiss_seconds
		while self.player.isPlaying() and remaining > 0:
			if self.closed: break
			self.sleep(1000)
			remaining -= 1
		if not self.closed: self.close()
