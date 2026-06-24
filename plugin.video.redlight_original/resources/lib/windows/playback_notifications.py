# -*- coding: utf-8 -*-
import time
from threading import Thread
from modules.kodi_utils import addon_fanart
from windows.base_window import BaseDialog
from modules.settings import avoid_episode_spoilers
from xbmc import executebuiltin
# from modules.kodi_utils import logger

pause_time_before_end, hold_pause_time = 10, 900
episode_flag_base = 'fenlight_flags/episodes/%s.png'
button_actions = {10: 'close', 11: 'play', 12: 'cancel', 13: 'stop'}
episode_status_dict = {
'season_premiere': 'b30385b5',
'mid_season_premiere': 'b385b503',
'series_finale': 'b38503b5',
'season_finale': 'b3b50385',
'mid_season_finale': 'b3b58503',
'':  ''}

class NextEpisode(BaseDialog):

	episode_status_dict = {
	'season_premiere': ('Season Premiere', 'b30385b5'),
	'mid_season_premiere': ('Mid-Season Premiere', 'b385b503'),
	'series_finale': ('Series Finale', 'b38503b5'),
	'season_finale': ('Season Finale', 'b3b50385'),
	'mid_season_finale': ('Mid-Season Finale', 'b3b58503'),
	'':  (None, None)}

	"""
	def __init__(self, *args, **kwargs):
		BaseDialog.__init__(self, *args)
		self.closed = False
		self.meta = kwargs.get('meta')
		self.selected = kwargs.get('default_action', 'cancel')
		self.set_properties()

	def onInit(self):
		focus_map = {'play': 11, 'cancel': 12, 'pause': 12, 'close': 10}
		self.setFocusId(focus_map.get(self.selected, 12))
		Thread(target=self.monitor, daemon=True).start()

	def run(self):
		self.doModal()
		self.clearProperties()
		self.clear_modals()
		return self.selected

	def onAction(self, action):
		if action in self.closing_actions:
			self.selected = 'close'
			self.closed = True
			self.close()

	def onClick(self, controlID):
		self.selected = {10: 'close', 11: 'play', 12: 'cancel'}[controlID]
		self.closed = True
		self.close()

	def set_properties(self):
		self.setProperty('mode', 'next_episode')
		self.setProperty('thumb', self.get_thumb())
		self.setProperty('clearlogo', self.meta.get('clearlogo', ''))
		self.setProperty('episode_label', '%s[B] | [/B]%02dx%02d[B] | [/B]%s' % (self.meta['title'], self.meta['season'], self.meta['episode'], self.meta['ep_name']))
		self.setProperty('pause_timer', '')
		self.setProperty('nextep_remaining', '')
		status_label, status_highlight = self.episode_status_dict[self.meta.get('episode_type', '')]
		if status_label:
			self.setProperty('episode_status.label', status_label)
			self.setProperty('episode_status.highlight', status_highlight)

	def _format_clock(self, seconds):
		seconds = max(0, int(seconds))
		mins, secs = divmod(seconds, 60)
		return '%d:%02d' % (mins, secs)

	def get_thumb(self):
		if avoid_episode_spoilers() and int(self.meta.get('playcount', '0')) == 0: thumb = self.meta.get('fanart', '') or addon_fanart()
		else: thumb = self.meta.get('ep_thumb', None) or self.meta.get('fanart', '') or addon_fanart()
		return thumb

	def _player_active(self):
		try:
			return self.player.isPlayingVideo() or self.player.isPlaying()
		except:
			return False

	def monitor(self):
		try:
			if self._player_active():
				while self._player_active() and not self.closed:
					try:
						total_time = self.player.getTotalTime()
						remaining_time = max(0, round(total_time - self.player.getTime()))
						self.setProperty('nextep_remaining', self._format_clock(remaining_time))
						if self.selected == 'pause' and remaining_time <= 10:
							try: self.player.pause()
							except: pass
							self.sleep(500)
							break
					except:
						pass
					self.sleep(1000)
		except:
			pass
		if self.closed:
			return
		if self.selected == 'pause':
			start_time = time.time()
			end_time = start_time + 900
			current_time = start_time
			while current_time <= end_time and self.selected == 'pause' and not self.closed:
				try:
					current_time = time.time()
					pause_timer = time.strftime('%M:%S', time.gmtime(max(end_time - current_time, 0)))
					self.setProperty('pause_timer', pause_timer)
					self.sleep(1000)
				except: break
			if self.selected != 'cancel' and not self.closed:
				try: self.player.pause()
				except: pass
		if not self.closed:
			self.close()
	"""
	def __init__(self, *args, **kwargs):
		BaseDialog.__init__(self, *args)
		self.closed = False
		self.meta = kwargs.get('meta')
		self.selected = kwargs.get('default_action', 'cancel')
		#self.selected = 'play'
		#self._initial_file = self.player.getPlayingFile()
		self.set_properties()


	def onInit(self):
		self._initial_file = self.player.getPlayingFile()
		self.sleep(250)  # ✅ let Kodi render firs
		self.setFocusId(11)
		from threading import Thread
		Thread(target=self.monitor, daemon=True).start()
		self.sleep(250)  # ✅ let Kodi render firs
		self.setFocusId(11)

	def run(self):
		self.doModal()
		self.clearProperties()
		self.clear_modals()
		return self.selected

	def onAction(self, action):
		if action in self.closing_actions:
			self.selected = 'close'
			self.closed = True
			self.close()

	def onClick(self, controlID):
		self.selected = button_actions[controlID]
		self.closed = True
		if self.selected == 'play':
			executebuiltin('PlayerControl(BigSkipForward)')
		elif self.selected == 'stop':
			executebuiltin('PlayerControl(Stop)')
		elif self.selected in ('cancel', 'close'):
			pass  # do nothing
		self.close()


	def set_properties(self):
		episode_type = self.meta.get('episode_type', '')
		self.setProperty('thumb', self.meta.get('ep_thumb', None) or self.meta.get('fanart', ''))
		self.setProperty('clearlogo', self.meta.get('clearlogo', ''))
		self.setProperty('episode_label', '%s[B] | [/B]%02dx%02d[B] | [/B]%s' % (self.meta['title'], self.meta['season'], self.meta['episode'], self.meta['ep_name']))
		self.setProperty('episode_status.highlight', episode_status_dict[episode_type])
		self.setProperty('episode_status.flag', episode_flag_base % episode_type)

	def is_paused(self):
		try:
			import xbmc, json
			result = xbmc.executeJSONRPC(
				'{"jsonrpc":"2.0","method":"Player.GetProperties","params":{"playerid":1,"properties":["speed"]},"id":1}'
			)
			speed = json.loads(result)['result']['speed']
			return int(speed) == 0
		except:
			return False

	def monitor(self):
		start_time = time.time()

		while True:
			try:
				if self.closed:
					break

				if not self.player.isPlayingVideo():
					break

				if self.player.getPlayingFile() != self._initial_file:
					break

				# ✅ pause-safe timeout
				if not self.is_paused():
					try: remaining_time = int(self.player.getTotalTime()) - int(self.player.getTime())
					except: remaining_time = 99
					if remaining_time < 2:
						break
				else:
					start_time = time.time()

				self.sleep(500)

			except:
				break

		self.closed = True
		self.sleep(200)

		try:
			self.close()
		except:
			pass
		return executebuiltin('Dialog.Close(next_episode.xml,true)')

class StillWatching(BaseDialog):
	def __init__(self, *args, **kwargs):
		BaseDialog.__init__(self, *args)
		self.closed = False
		self.selected = False
		self.meta = kwargs.get('meta')
		self.check_text = kwargs.get('check_text')
		self.heading = kwargs.get('heading') or 'Still Watching?'
		right_align = kwargs.get('right_align', 'false')
		self.compact_confirm = str(right_align).lower() in ('true', '1', 'yes')
		self.set_properties()

	def onInit(self):
		self.set_properties()
		self.setFocusId(10)
		Thread(target=self.monitor, daemon=True).start()

	def run(self):
		self.doModal()
		self.clearProperties()
		self.clear_modals()
		return self.selected

	def onAction(self, action):
		if action in self.closing_actions:
			self.selected = False
			self.closed = True
			self.close()

	def onClick(self, controlID):
		self.selected = {10: True, 11: False}[controlID]
		self.closed = True
		self.close()

	def set_properties(self):
		landscape, fanart, clearlogo = self.meta.get('landscape', ''), self.meta.get('fanart', ''), self.meta.get('clearlogo', '')
		self.setProperty('mode', 'autoscrape_confirm' if self.compact_confirm else 'still_watching')
		if self.compact_confirm:
			if avoid_episode_spoilers() and int(self.meta.get('playcount', '0')) == 0:
				thumb = fanart or addon_fanart()
			else:
				thumb = self.meta.get('ep_thumb') or fanart or addon_fanart()
			self.setProperty('thumb', thumb)
			self.setProperty('clearlogo', clearlogo)
			self.setProperty('episode_label', '%s[B] | [/B]%02dx%02d[B] | [/B]%s' % (
				self.meta['title'], self.meta['season'], self.meta['episode'], self.meta.get('ep_name', '')))
		else:
			self.setProperty('thumb', landscape or fanart)
			if not landscape: self.setProperty('clearlogo', clearlogo)
			self.setProperty('episode_label', self.check_text % self.meta['title'])
		self.setProperty('still_watching_heading', self.heading)
		self.setProperty('pause_timer', '')

	def monitor(self):
		pause_timer = 10
		try:
			while not self.closed and pause_timer >= 0:
				if self.compact_confirm:
					try:
						if not self.player.isPlayingVideo() and not self.player.isPlaying(): break
					except: pass
				self.setProperty('pause_timer', '%02d %s' % (pause_timer, 'seconds' if pause_timer > 1 else 'second'))
				self.sleep(1000)
				if self.closed: return
				if pause_timer == 0: break
				pause_timer -= 1
		except:
			pass
		if not self.closed:
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
		Thread(target=self.monitor, daemon=True).start()

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
				self.set_image(v['id'], 'redlight_common/overlay_selected.png')
			else:
				self.setProperty(v['property'], 'red')
				self.set_image(v['id'], 'redlight_common/cross.png')

	def set_properties(self):
		self.setProperty('mode', 'stinger')
		self.setProperty('thumb', self.meta.get('fanart', '')) or addon_fanart()
		self.setProperty('clearlogo', self.meta.get('clearlogo', ''))

	def monitor(self):
		total_time = 10000
		try:
			while self.player.isPlaying() and total_time > 0 and not self.closed:
				self.sleep(1000)
				total_time -= 1000
		except:
			pass
		if not self.closed:
			self.close()
