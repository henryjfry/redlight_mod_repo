# -*- coding: utf-8 -*-
import os
import xbmc
import json
from threading import Thread
from apis.trakt_api import make_trakt_slug
from caches.settings_cache import get_setting
from modules import kodi_utils as ku, settings as st, watched_status as ws
# logger = ku.logger

import base64
from inspect import currentframe, getframeinfo
import xbmcgui
import time
#xbmc.log(str(str('Line ')+str(getframeinfo(currentframe()).lineno)+'___'+str(getframeinfo(currentframe()).filename)), level=xbmc.LOGINFO)

PROP_RESOLVE_CANCEL = 'redlight.resolve_cancelled'
PROP_PLAY_OPENING = 'redlight.play_opening'
PROP_NEXTEP_PENDING = 'redlight.nextep_pending'

set_resume, set_watched = 5, 90

class RedLightPlayer(xbmc.Player):
	_monitor_running = False
	def __init__ (self):
		xbmc.Player.__init__(self)
		self.player = xbmc.Player()
		self.currently_popping = False
		self.sources_object = None
		self.nextep_info_gathered = False
		self.media_marked = False
		self.playback_successful = None
		self.cancel_all_playback = False
		self.stop_file = False
		self._next_added = False
		self._nextep_dialog_shown = False
		self._dialog_locked = False

	def _resolve_cancelled(self):
		if not self.is_generic and (self.sources_object._resolve_user_cancelled or self.sources_object.cancel_all_playback):
			return True
		return ku.get_property(PROP_RESOLVE_CANCEL) == 'true'

	def run(self, url=None, obj=None):
		ku.hide_busy_dialog()
		self.clear_playback_properties(clear_navigation=False)
		if not url:
			self.is_generic = obj == 'video'
			return self.run_error('No playable link was returned.')
		try: return self.play_video(url, obj)
		except:
			self.is_generic = obj == 'video'
			return self.run_error()

	def decode_sorttitle(self, st):
		"""
		Safely decodes base64 sorttitle and extracts season/episode integers.
		Handles potentially stripped base64 padding automatically.
		"""
		if not st:
			return None, None
		try:
			# Fix base64 padding issues if strings get stripped by database
			padded_st = st + '=' * (-len(st) % 4)
			decoded = base64.b64decode(padded_st).decode('utf-8', errors='ignore')
			
			# Extract structural elements from: tmdb_id_season_episode
			parts = decoded.split('_')
			if len(parts) == 3:
				_, season, episode = parts
				return int(season), int(episode)
			
		except Exception as e:
			xbmc.log(f"[decode_sorttitle] Failed parsing structure: {e}", xbmc.LOGDEBUG)
		
		return None, None

	def get_current_sorttitle(self, playerid=1):
		"""
		Retrieves the current item's sorttitle using a dynamic, retry-backed lookup
		to eliminate timing race conditions when new files initialize.
		"""
		def rpc(method, params=None):
			payload = {"jsonrpc": "2.0", "method": method, "params": params or {}, "id": 1}
			response = xbmc.executeJSONRPC(json.dumps(payload))
			return json.loads(response)

		# Try up to 10 times (1 second total) for Kodi's internal state machine to sync
		for _ in range(10):
			try:
				# ✅ Step 1: Resolve dynamic Player ID if hardcoded value is stale/unmapped
				active_players = rpc("Player.GetActivePlayers").get("result", [])
				if active_players:
					playerid = next((p["playerid"] for p in active_players if p["type"] == "video"), playerid)

				# ✅ Step 2: Extract current active file info directly (Most reliable target)
				item = rpc("Player.GetItem", {"playerid": playerid, "properties": ["sorttitle"]})
				sorttitle = item.get("result", {}).get("item", {}).get("sorttitle")
				if sorttitle:
					return sorttitle

				# ✅ Step 3: Playlist position lookup fallback if direct item lookup is slow
				res = rpc("Player.GetProperties", {"playerid": playerid, "properties": ["position", "playlistid"]})
				result = res.get("result", {})
				position = result.get("position", -1)
				playlistid = result.get("playlistid", 1)  # Default video playlist is 1

				if position >= 0:
					pl = rpc("Playlist.GetItems", {"playlistid": playlistid, "properties": ["sorttitle"]})
					items = pl.get("result", {}).get("items", [])
					if 0 <= position < len(items):
						sorttitle = items[position].get("sorttitle")
						if sorttitle:
							return sorttitle
							
			except Exception as e:
				xbmc.log(f"[get_current_sorttitle] Internal iteration exception: {e}", xbmc.LOGDEBUG)

			xbmc.sleep(100)  # Pause briefly between attempts to let Kodi populate data structures

		xbmc.log("[get_current_sorttitle] Warning: Exited retry engine without capturing metadata", xbmc.LOGWARN)
		return None


	def get_current_episode_from_player(self):
		try:
			st = self.get_current_sorttitle()
			if not st:
				return None

			season, episode = self.decode_sorttitle(st)
			if season is None or episode is None:
				return None

			return season, episode

		except Exception as e:
			xbmc.log(f"get_current_episode_from_player error: {str(e)}", xbmc.LOGDEBUG)
			return None


	def get_playlist_sorttitles(self):
		"""
		Gathers all available sorttitle tags from the active video queue safely.
		Uses an explicit property fallback if the video tag container lags.
		"""
		sorttitles = []
		try:
			playlist = xbmc.PlayList(xbmc.PLAYLIST_VIDEO)
			for i in range(playlist.size()):
				try:
					item = playlist[i]
					# Primary: Fetch native tag metadata structure
					st = item.getVideoInfoTag().getSortTitle()
					
					# Secondary Fallback: Fetch direct layout list property
					if not st:
						st = item.getProperty("sorttitle")
						
					if st:
						sorttitles.append(st)
				except:
					continue
		except Exception as e:
			xbmc.log(f"[get_playlist_sorttitles] Critical failure: {e}", xbmc.LOGDEBUG)

		return sorttitles

	def play_video(self, url, obj):
		self.set_constants(url, obj)
		if self.is_generic:
			ku.clear_video_playlist()
		if not self.is_generic and self._resolve_cancelled():
			self.playback_successful = False
			self.cancel_all_playback = True
			self.sources_object.cancel_all_playback = True
			self.sources_object._resolve_user_cancelled = True
			return
		ku.volume_checker()
		ku.set_property(PROP_PLAY_OPENING, 'true')
		self.play(self.url, self.make_listing())
		if self.is_generic:
			self.check_playback_start_generic()
			if self.playback_successful:
				ku.clear_property(PROP_PLAY_OPENING)
			else:
				self.safe_stop()
				return self.run_error()
		else:
			self.check_playback_start()
			if self.playback_successful:
				ku.clear_property(PROP_PLAY_OPENING)
				try:
					if self.sources_object:
						self.sources_object._release_resolve_busy()
				except:
					pass
				self.monitor()
			else:
				self.sources_object.playback_successful = self.playback_successful
				cancelled = self.cancel_all_playback or self.sources_object._resolve_user_cancelled
				if cancelled:
					self.sources_object.cancel_all_playback = True
					self.sources_object._resolve_user_cancelled = True
				else:
					self.sources_object.cancel_all_playback = self.cancel_all_playback
				if cancelled:
					if not self.sources_object._resolve_user_cancelled:
						self.kill_dialog()
				else:
					# Keep the resolver progress UI so play_file can try the next queued source.
					self.run_error()
				self.safe_stop()
		try: del self.kodi_monitor
		except: pass

	def check_playback_start_generic(self):
		resolve_percent = 0
		while self.playback_successful is None:
			ku.hide_busy_dialog()
			if self.kodi_monitor.abortRequested():
				self.playback_successful = False
				break
			elif resolve_percent >= 100:
				self.playback_successful = False
				break
			elif ku.get_visibility('Window.IsTopMost(okdialog)'):
				ku.execute_builtin('SendClick(okdialog, 11)')
				self.playback_successful = False
			elif self.isPlayingVideo():
				try:
					if ku.get_property('redlight.browse_playback') == 'true':
						browse_window = getattr(self, '_browse_results_window', None)
						if browse_window:
							try:
								browse_window.selected = (None, '')
								browse_window.close()
								self._browse_results_window = None
							except:
								pass
					if not ku.get_visibility('Window.IsActive(fullscreenvideo)'):
						ku.execute_builtin('ActivateWindow(fullscreenvideo)', block=False)
					if self.getTotalTime() not in ('0.0', '', 0.0, None):
						self.playback_successful = True
				except:
					pass
			resolve_percent = round(resolve_percent + 0.26, 1)
			ku.sleep(50)

	def check_playback_start(self):
		resolve_percent = 0
		while self.playback_successful is None:
			ku.hide_busy_dialog()
			if self._resolve_cancelled():
				self.sources_object.cancel_all_playback = True
				self.sources_object._resolve_user_cancelled = True
				self.playback_successful = False
				self.safe_stop()
				break
			elif not self.sources_object.progress_dialog:
				if self._resolve_cancelled():
					self.sources_object.cancel_all_playback = True
					self.sources_object._resolve_user_cancelled = True
					self.playback_successful = False
					self.safe_stop()
					break
				elif self.isPlayingVideo():
					try:
						if self.getTotalTime() not in ('0.0', '', 0.0, None) and ku.get_visibility('Window.IsActive(fullscreenvideo)'):
							self.playback_successful = True
					except: pass
			elif self.sources_object.progress_dialog.skip_resolved(): self.playback_successful = False
			elif self.sources_object.progress_dialog.iscanceled() or self.kodi_monitor.abortRequested():
				self.sources_object.cancel_all_playback = True
				self.sources_object._resolve_user_cancelled = True
				self.playback_successful = False
				self.safe_stop()
				break
			elif resolve_percent >= 100:
				self.playback_successful = False
				break
			elif ku.get_visibility('Window.IsTopMost(okdialog)'):
				ku.execute_builtin('SendClick(okdialog, 11)')
				self.playback_successful = False
			elif self.isPlayingVideo():
				if self._resolve_cancelled():
					self.sources_object.cancel_all_playback = True
					self.sources_object._resolve_user_cancelled = True
					self.playback_successful = False
					self.safe_stop()
					break
				try:
					if self.getTotalTime() not in ('0.0', '', 0.0, None) and ku.get_visibility('Window.IsActive(fullscreenvideo)'): self.playback_successful = True
				except: pass
			resolve_percent = round(resolve_percent + 0.26, 1)
			try:
				if self.sources_object.progress_dialog:
					self.sources_object.progress_dialog.update_resolver(percent=resolve_percent)
			except: pass
			ku.sleep(50)

	def playback_close_dialogs(self):
		self.sources_object.playback_successful = True
		self.kill_dialog()
		ku.sleep(200)
		ku.close_all_dialog()

	def run_playlist(self, url=None, obj=None):
		"""
		PLAYLIST MODE ENTRYPOINT (FIXED)
		"""
		ku.hide_busy_dialog()
		self.clear_playback_properties()

		if not url:
			return self.run_error()

		self.set_constants(url, obj)

		playlist = xbmc.PlayList(xbmc.PLAYLIST_VIDEO)

		# ✅ ONLY clear if nothing is playing
		if not self.isPlaying():
			playlist.clear()

		self._next_added = False
		self._nextep_dialog_shown = False

		# ✅ ONLY add first item if empty
		if playlist.size() == 0:
			listitem = self.make_listing()
			try:
				from modules.sources import make_sorttitle
				sorttitle_b64 = make_sorttitle(self.meta)
				listitem.getVideoInfoTag().setSortTitle(sorttitle_b64)
			except:
				pass

			xbmc.log(str(str('Line ')+str(getframeinfo(currentframe()).lineno)+'___'+str(getframeinfo(currentframe()).filename)), level=xbmc.LOGINFO)
			xbmc.log(str(url), xbmc.LOGINFO)

			playlist.add(url, listitem)
			self.play(playlist)

		# ✅ DO NOT depend on old resolve success logic
		# check_playback_start is unreliable for playlist model
		# self.check_playback_start()

		# ✅ start monitor ALWAYS (NEW)
		#Thread(target=self.monitor_playlist, daemon=True).start()

	def is_next_in_playlist(self):
		"""
		Checks if the next physical item in the playlist matches our expected up-next tag.
		"""
		try:
			playlist = xbmc.PlayList(xbmc.PLAYLIST_VIDEO)
			current_pos = playlist.getposition()

			if current_pos < playlist.size() - 1:
				next_item = playlist[current_pos + 1]
				# Check native tag first
				st = next_item.getVideoInfoTag().getSortTitle()
				# Fallback to general listing item properties
				if not st:
					st = next_item.getProperty("sorttitle")

				if st:
					ns, ne = self.decode_sorttitle(st)
					expected = self.get_expected_next_episode()
					if expected == (ns, ne):
						return True
		except Exception as e:
			xbmc.log(f"is_next_in_playlist error: {str(e)}", xbmc.LOGDEBUG)

		return False

	def get_expected_next_episode(self):
		"""
		Resolves season and episode markers for the upcoming chronological episode.
		"""
		try:
			from modules.episode_tools import EpisodeTools

			base_meta = dict(self.meta)
			current_ep = self.get_current_episode_from_player()
			if not current_ep:
				xbmc.log("NEXT_EP: Unable to resolve current episode from player", xbmc.LOGINFO)
				xbmcgui.Window(10000).clearProperty('fenlight.monitor_id')
				return
			cur_season, cur_episode = current_ep
			# ✅ Inject correct episode into meta
			base_meta.update({
				'season': cur_season,
				'episode': cur_episode
			})
			ep_tools = EpisodeTools(base_meta, self.nextep_settings)
			info = ep_tools.next_episode_info()

			#ep_tools = EpisodeTools(self.meta, self.nextep_settings)
			#info = ep_tools.next_episode_info()

			if not info:
				return None

			return int(info.get('season')), int(info.get('episode'))
		except:
			return None

	def _reset_state(self):
		"""
		Wipes local state logic parameters cleanly during track transition boundaries.
		"""
		self._next_added = False
		#self._nextep_dialog_shown = False
		self.nextep_info_gathered = False
		self.media_marked = False
		self.start_prep = None

		try:
			self.info_next_ep()
		except:
			pass

	def monitor_playlist(self, monitor_id=None):
		"""
		ROBUST PLAYLIST MONITOR (PRODUCTION READY)
		"""
		xbmc.log("PLAYLIST MONITOR STARTED", xbmc.LOGINFO)
		check_ep_flag = False
		try:
			# Short baseline buffer for incoming playback allocations
			xbmc.sleep(1000)
			if self.isPlaying():
				self.stop_file = False

			not_playing_since = None
			was_playing = False
			seconds_stopped = 0.0
			last_loop_time = time.time() 

			self._simkl_scrobble_start()
			if st.auto_enable_subs() and not st.submaker_enabled(): self.showSubtitles(True)

			self._max_progress = 0.0
			while not self.stop_file:
				xbmc.sleep(100)
				current_id = xbmcgui.Window(10000).getProperty('fenlight.monitor_id')
				if current_id == None or current_id == '':
					current_id = str(0)
				if monitor_id != None:
					if monitor_id != current_id:
						xbmc.log("PLAYLIST MONITOR ENDED", xbmc.LOGINFO)
						xbmcgui.Window(10000).clearProperty('fenlight.monitor_id')
						self.stop_file = True
				if self.stop_file:
					break

				current_loop_time = time.time()
				loop_duration = current_loop_time - last_loop_time
				last_loop_time = current_loop_time
				# ✅ PLAYBACK STATE ACCUMULATION WATCHDOG
				#if not self.isPlayingVideo():
				#	seconds_stopped += loop_duration
				#	if seconds_stopped >= 10.0:
				#		xbmc.log(f"PLAYLIST MONITOR: Continuous stoppage reached {seconds_stopped:.2f}s. Terminating.", xbmc.LOGINFO)
				#		self.stop_file = True
				#		break
				#	xbmc.sleep(250)
				#	# Removed 'continue' so the loop progresses normally and hits the main throttle below
				#else:
				#	seconds_stopped = 0.0
				#	progress_params_flag = 0
				#if not self.isPlayingVideo():
				#	if progress_params_flag < progress:
				#		progress_params_flag = progress
				#		if progress >= 5:
				#			progress_params = {'media_type': self.media_type, 'tmdb_id': self.tmdb_id, 'curr_time': self.curr_time, 'total_time': self.total_time,
				#							'title': self.title, 'season': self.season, 'episode': self.episode, 'from_playback': 'true'}
				#			Thread(target=self.run_media_progress, args=(ws.set_bookmark, progress_params)).start()


				if not self.isPlayingVideo():
					seconds_stopped += loop_duration

					if seconds_stopped >= 5.0:   # shorter, responsive
						xbmc.log(f"PLAYLIST: Final progress = {self._max_progress:.2f}", xbmc.LOGINFO)

						if self._max_progress >= 90:
							self.media_watched_marker()
							self.media_marked = True
							self._simkl_scrobble_stop(100)
							xbmc.log(f"NEXTEP: media_watched_marker", xbmc.LOGERROR)

						elif self._max_progress >= 5:
							self.current_point = self._max_progress
							self.media_watched_marker()
							self.media_marked = False
							#progress_params = {
							#	'media_type': self.media_type,
							#	'tmdb_id': self.tmdb_id,
							#	'curr_time': self.curr_time,
							#	'total_time': self.total_time,
							#	'title': self.title,
							#	'season': self.season,
							#	'episode': self.episode,
							#	'from_playback': 'true'
							#}
							#Thread(target=self.run_media_progress, args=(ws.set_bookmark, progress_params)).start()


						self.stop_file = True
						break


				if self.isPlaying() and check_ep_flag == False:
					if self.media_type == 'movie':
						check_ep_flag = True
						self._dialog_locked = True
						self._next_added = True
					else:
						current_ep = self.get_current_episode_from_player()
						if not current_ep:
							xbmcgui.Window(10000).clearProperty('fenlight.monitor_id')
							xbmc.log("NEXT_EP: Unable to resolve current episode from player", xbmc.LOGINFO)
							self.stop_file = True
							xbmc.log("PLAYLIST MONITOR ENDED", xbmc.LOGINFO)
							xbmcgui.Window(10000).clearProperty('fenlight.monitor_id')
							break
						check_ep_flag = True


				# ✅ 2. PLAYBACK TIMING ENGINE
				try:
					total = self.getTotalTime()
					current = self.getTime()
					if not total or total <= 0:
						xbmc.sleep(200)
						continue
					
					progress = (current / total) * 100

					# ✅ sync legacy fields
					self.curr_time = current
					self.total_time = total
					self.current_point = progress

					# ✅ track max progress seen
					if progress > self._max_progress:
						self._max_progress = progress

					remaining = total - current
				except Exception as e:
					xbmc.log(f"PLAYLIST TIME ACCUMULATION ERROR: {str(e)}", xbmc.LOGDEBUG)
					xbmc.sleep(500)
					continue

				# ✅ 3. DETECT TRACK CHANGE VIA PLAYLIST INDICES (FAST & NON-BLOCKING)
				try:
					playlist = xbmc.PlayList(xbmc.PLAYLIST_VIDEO)
					pos = playlist.getposition()
				except:
					pos = -1

				if pos != getattr(self, "_last_pos", None) and pos >= 0:
					xbmc.log(f"PLAYLIST: Track index changed → {pos}", xbmc.LOGINFO)
					self._last_pos = pos
					self._current_sort = None  # Force sorttitle recalculation flag
					self._nextep_dialog_shown = False
					self._dialog_locked = False
					check_ep_flag = False
					self._reset_state()
					if not self.media_type == 'movie':
						current_ep = self.get_current_episode_from_player()
						cur_season, cur_episode = current_ep
						TMDbHelper_NEW_PlayerInfoString = {'tmdb_type': self.media_type, 'tmdb_id': str(self.tmdb_id), 'imdb_id': str(self.imdb_id), 'tvdb_id': str(self.tvdb_id), 'season': cur_season, 'episode': cur_episode}
						xbmcgui.Window(10000).setProperty('TMDbHelper.PlayerInfoString', f'{TMDbHelper_NEW_PlayerInfoString}'.replace('\'','"'))
						PlayerInfoString = xbmcgui.Window(10000).getProperty('TMDbHelper.PlayerInfoString')

				# ✅ 4. VALIDATE META DATA SIGNATURES (NON-BLOCKING LOOKUP WITH TIMEOUT OMITTED IN LOOP)
				# Passing a custom dynamic flag or setting playerid parameters prevents loop stalls
				if not self._dialog_locked:
					current_sort = self.get_current_sorttitle()
					if current_sort and current_sort != getattr(self, "_current_sort", None):
						prev_sort = getattr(self, "_current_sort", None)
						self._current_sort = current_sort
						xbmc.log(f"PLAYLIST: Item changed metadata signature {prev_sort} → {current_sort}", xbmc.LOGINFO)
						self._reset_state()
						if not self.media_type == 'movie':
							current_ep = self.get_current_episode_from_player()
							cur_season, cur_episode = current_ep
							TMDbHelper_NEW_PlayerInfoString = {'tmdb_type': self.media_type, 'tmdb_id': str(self.tmdb_id), 'imdb_id': str(self.imdb_id), 'tvdb_id': str(self.tvdb_id), 'season': cur_season, 'episode': cur_episode}
							xbmcgui.Window(10000).setProperty('TMDbHelper.PlayerInfoString', f'{TMDbHelper_NEW_PlayerInfoString}'.replace('\'','"'))
							PlayerInfoString = xbmcgui.Window(10000).getProperty('TMDbHelper.PlayerInfoString')


				# ✅ 5. INITIALISE METADATA ENGINE ONCE PER EPISODE
				if not self.nextep_info_gathered and not self.media_type == 'movie':
					if not hasattr(self, "autoplay_nextep"):
						self.autoplay_nextep = False
					if not hasattr(self, "autoscrape_nextep"):
						self.autoscrape_nextep = True
					if not hasattr(self, "nextep_settings") and self.sources_object:
						self.nextep_settings = getattr(self.sources_object, "nextep_settings", None)
					
					try:
						self.info_next_ep()
						self.nextep_info_gathered = True
						xbmc.log("PLAYLIST: Successfully loaded upcoming item meta payload.", xbmc.LOGINFO)
					except Exception as e:
						xbmc.log(f"NEXTEP INFO ENGINE RECOVERY FAILURE: {str(e)}", xbmc.LOGERROR)

				# ✅ 6. WATCHED STATUS SYNC
				#if progress >= set_watched and not self.media_marked:
				#	xbmc.log(f"NEXTEP: media_watched_marker", xbmc.LOGERROR)
				#	self.media_watched_marker()
				#	self.media_marked = True

				# ✅ 7. 35% SCRAPE COMPLETION TRIGGER
				if progress >= 35 and not self._next_added:
					if self.media_type == 'movie':
						self._next_added = True
						continue
					# ✅ CRITICAL GUARD
					if self.is_next_in_playlist():
						xbmc.log("PLAYLIST: Next already queued → skipping scrape trigger", xbmc.LOGINFO)
						self._next_added = True
						continue
					self._next_added = True
					xbmc.log(f"PLAYLIST: Processing scrape targets. Keys: {list(self.meta.keys())}", xbmc.LOGINFO)
					try:
						if self.is_next_in_playlist():
							xbmc.log("PLAYLIST: Expected target exists down-queue → skipping background engine scrape", xbmc.LOGINFO)
						else:
							xbmc.log("PLAYLIST: Querying engines for subsequent stream links...", xbmc.LOGINFO)
							current_ep = self.get_current_episode_from_player()
							cur_season, cur_episode = current_ep
							self.run_next_ep_playlist()
							TMDbHelper_NEW_PlayerInfoString = {'tmdb_type': self.media_type, 'tmdb_id': str(self.tmdb_id), 'imdb_id': str(self.imdb_id), 'tvdb_id': str(self.tvdb_id), 'season': cur_season, 'episode': cur_episode}
							xbmcgui.Window(10000).setProperty('TMDbHelper.PlayerInfoString', f'{TMDbHelper_NEW_PlayerInfoString}'.replace('\'','"'))
							PlayerInfoString = xbmcgui.Window(10000).getProperty('TMDbHelper.PlayerInfoString')

							
					except Exception as e:
						xbmc.log(f"NEXT SCRAPE OPERATION EXCEPTION: {str(e)}", xbmc.LOGERROR)

				# ✅ 8. 97% NEXT EPISODE DIALOG TRIGGER
				#if (progress >= 97 or remaining <= 30) and not self._nextep_dialog_shown:
				#	if self.sources_object:
				#		self._nextep_dialog_shown = True
				#		xbmc.log("PLAYLIST: Rendering playback transition interface overlay", xbmc.LOGINFO)
				#		self.sources_object._make_nextep_dialog(default_action='play')

				if (progress >= 97 or remaining <= 30):

					#self._simkl_scrobble_stop(100)
					#watched_function = ws.mark_movie if self.media_type == 'movie' else ws.mark_episode
					#watched_params = {'action': 'mark_as_watched', 'tmdb_id': self.tmdb_id, 'title': self.title, 'year': self.year, 'season': self.season, 'episode': self.episode,
					#				'tvdb_id': self.tvdb_id, 'from_playback': 'true'}
					#Thread(target=self.run_media_progress, args=(watched_function, watched_params)).start()

					if self.sources_object and not self._dialog_locked:
						self._dialog_locked = True      # ✅ HARD LOCK
						self._nextep_dialog_shown = True

						xbmc.log("PLAYLIST: Rendering playback transition interface overlay", xbmc.LOGINFO)
						self.sources_object._make_nextep_dialog(default_action='play')

				# ✅ 9. SYSTEM BALANCE THROTTLE
				xbmc.sleep(500) # Balanced to 500ms to balance accuracy with low CPU usage
		except Exception as e:
			xbmc.log(f"MONITOR CRASH: {str(e)}", xbmc.LOGERROR)
			xbmcgui.Window(10000).clearProperty('fenlight.monitor_id')

		finally:
			RedLightPlayer._monitor_running = False
			xbmc.log("PLAYLIST MONITOR STOPPED", xbmc.LOGINFO)
			xbmcgui.Window(10000).clearProperty('fenlight.monitor_id')
			playlist = xbmc.PlayList(xbmc.PLAYLIST_VIDEO)
			current_pos = playlist.getposition()
			if current_pos < playlist.size() - 1 and self._next_added == True:
				xbmcgui.Window(10000).setProperty('fenlight.onPlayBackStarted', 'onPlayBackStarted')

	def check_playlist(self, b64_encode_dict, playlistid=1):
		def _json_rpc(method, params=None):
			if params is None:
				params = {}
			payload = {"jsonrpc": "2.0","method": method,"params": params,"id": 1}
			response = xbmc.executeJSONRPC(json.dumps(payload))
			return json.loads(response)

		try:
			# Get playlist items with sorttitle
			res = _json_rpc("Playlist.GetItems", {"playlistid": playlistid,"properties": ["sorttitle"]})
			items = res.get("result", {}).get("items", [])
			for item in items:
				existing = item.get("sorttitle")
				if not existing:
					continue
				# Fast path: direct string compare
				if existing == b64_encode_dict:
					return True
				# Optional fallback: decoded compare (future-proof)
				try:
					decoded_existing = json.loads(base64.b64decode(existing).decode('utf-8'))
					decoded_target = json.loads(base64.b64decode(b64_encode_dict).decode('utf-8'))
					if decoded_existing == decoded_target:
						return True
				except Exception:
					continue
			return False
		except Exception as e:
			xbmc.log(f" playlist_contains_b64 error: {e}", xbmc.LOGERROR)
			return False

	def run_next_ep_playlist(self):
		#try:
		if 1==1:
			from modules.episode_tools import EpisodeTools
			from modules.sources import Sources, make_sorttitle

			#nextep_settings = getattr(self, "nextep_settings", None) or getattr(self.sources_object, "nextep_settings", None)
			# ✅ HARD GUARANTEE nextep_settings is valid
			if not getattr(self, "nextep_settings", None):
				try:
					# rebuild using same logic as info_next_ep
					play_type = 'autoplay_nextep' if getattr(self, "autoplay_nextep", False) else 'autoscrape_nextep'
					nextep_settings = st.auto_nextep_settings(play_type)

					self.nextep_settings = {
						'use_window': nextep_settings.get('alert_method', 0) == 0,
						'window_time': int((nextep_settings.get('window_percentage', 20) / 100) * (self.getTotalTime() or 1800)),
						'default_action': nextep_settings.get('default_action', 'play'),
						'play_type': play_type
					}
				except:
					self.nextep_settings = {
						'use_window': False,
						'window_time': 300,
						'default_action': 'play',
						'play_type': 'autoscrape_nextep'
					}

			nextep_settings = self.nextep_settings
			# ✅ Get next episode
			#ep_tools = EpisodeTools(self.meta, nextep_settings)

			from modules import metadata

			# ✅ rebuild full meta (CRITICAL FIX)
			try:
				if self.meta.get('media_type') == 'episode':
					base_meta = metadata.tvshow_meta(
						'tmdb_id',
						self.meta.get('tmdb_id'),
						st.tmdb_api_key(),
						st.mpaa_region(),
						ku.get_datetime()
					)

					# ✅ merge current episode context back in
					base_meta.update({
						'season': self.meta.get('season'),
						'episode': self.meta.get('episode')
					})
				else:
					base_meta = self.meta
			except:
				base_meta = self.meta

			#ep_tools = EpisodeTools(base_meta, nextep_settings)
			#url_params = ep_tools.next_episode_info()

			current_ep = self.get_current_episode_from_player()
			if not current_ep:
				xbmc.log("NEXT_EP: Unable to resolve current episode from player", xbmc.LOGINFO)
				return
			cur_season, cur_episode = current_ep
			# ✅ Inject correct episode into meta
			base_meta.update({
				'season': cur_season,
				'episode': cur_episode
			})
			ep_tools = EpisodeTools(base_meta, nextep_settings)
			url_params = ep_tools.next_episode_info()


			if not url_params or url_params in ('error', 'no_next_episode'):
				xbmc.log('NEXT_EP: No valid next episode info', xbmc.LOGINFO)
				return
			next_season = int(url_params.get('season', 0))
			next_episode_num = int(url_params.get('episode', 0))
			if not next_season or not next_episode_num:
				xbmc.log('NEXT_EP: Invalid episode numbers', xbmc.LOGINFO)
				return

			#if url_params == 'no_next_episode':
			#	xbmc.log("NEXT_EP: Series finished", xbmc.LOGINFO)
			#	return

			#params = {
			#	'media_type': 'episode',
			#	'tmdb_id': self.meta.get('tmdb_id'),
			#	'season': next_ep.get('season'),
			#	'episode': next_ep.get('episode'),
			#	'autoplay': 'true',
			#	'background': 'true'
			#}
			params = url_params
			
			# 🚨 HARD FIXES — THESE ARE THE WHOLE PROBLEM
			params.pop('nextep_settings', None)
			params.pop('play_type', None)

			params.update({
				'background': 'true',   # ✅ run in background
				'autoplay': 'false',    # ✅ DO NOT trigger play_file
			})


			#current_st = self.get_current_sorttitle()
			#cur_season, cur_episode = self.decode_sorttitle(current_st)
			## ✅ safety: only scrape NEXT episode
			#if cur_episode and cur_episode != self.meta.get('episode'):
			#	return


			sources = Sources()

			# ✅ Inject missing playback context (CRITICAL)
			if not hasattr(sources, 'playback_percent'):
				sources.playback_percent = 0.0

			if not hasattr(sources, 'playing_filename'):
				sources.playing_filename = ''

			if not hasattr(sources, 'playing_item'):
				sources.playing_item = {}

			# ✅ retry scrape (good)
			results = None
			for _ in range(2):
				results = sources.playback_prep(params)
				if results:
					break

			if not results:
				xbmc.log('NEXT_EP: No results after retry', xbmc.LOGINFO)
				return

			# ✅ ✅ CRITICAL FIX — use FULL autoplay resolve logic
			if isinstance(results, str):
				# ✅ Already resolved URL
				url = results

			elif isinstance(results, list):
				# ✅ Got source list (fallback case)
				#url = sources.resolve_url(results)
				url = None
				for item in results:
					#url = sources.resolve_sources(item)
					#xbmc.log(url, xbmc.LOGINFO)
					#if url:
					#	url = sources._ensure_play_headers(url, item)
					#xbmc.log(url, xbmc.LOGINFO)
					#if url:
					#	break
					url = sources._resolve_sources_wait(item)
					if url:
						url = sources._ensure_play_headers(url, item)
					#xbmc.log(url, xbmc.LOGINFO)
					#if not url:
					#	continue
					if not sources._is_valid_playable_url(url):
						xbmc.log(f"INVALID URL - skipping: {str(url)}", xbmc.LOGINFO)
						continue
					else:
						break

				
			elif isinstance(results, dict):
				# ✅ Got source dict (fallback case)
				#url = sources.resolve_url(results)
				url = None
				for item in results:
					#url = sources.resolve_sources(item)
					#xbmc.log(url, xbmc.LOGINFO)
					#if url:
					#	url = sources._ensure_play_headers(url, item)
					#xbmc.log(url, xbmc.LOGINFO)
					#if url:
					#	break
					url = sources._resolve_sources_wait(item)
					if url:
						url = sources._ensure_play_headers(url, item)
					#xbmc.log(url, xbmc.LOGINFO)
					#if not url:
					#	continue
					if not sources._is_valid_playable_url(url):
						xbmc.log(f"INVALID URL - skipping: {str(url)}", xbmc.LOGINFO)
						continue
					else:
						break
			else:
				xbmc.log('NEXT_EP: unexpected result type', xbmc.LOGINFO)
				return


			if not url:
				xbmc.log('NEXT_EP: resolve_url failed', xbmc.LOGINFO)
				return

			## ✅ Build listitem
			#player = FenLightPlayer()
			#player.set_constants(url, sources)
			#listitem = player.make_listing()


			# ✅ preserve current playback state
			saved_meta = self.meta
			saved_sources = self.sources_object

			# ✅ reuse CURRENT player instance (CRITICAL FIX)
			self.set_constants(url, sources)
			listitem = self.make_listing()

			# ✅ restore playback state
			self.meta = saved_meta
			self.sources_object = saved_sources

			try:
				sorttitle_b64 = make_sorttitle(sources.meta)
				listitem.getVideoInfoTag().setSortTitle(sorttitle_b64)
			except:
				sorttitle_b64 = None

			playlist = xbmc.PlayList(xbmc.PLAYLIST_VIDEO)

			# ✅ duplicate protection (correct)
			#existing_sorttitles = self.get_playlist_sorttitles()
			#if sorttitle_b64 in existing_sorttitles:
			#	xbmc.log('NEXT_EP: already exists in playlist, skipping scrape', xbmc.LOGINFO)
			#	return
			#if self.playlist_contains(sorttitle_b64):
			#	xbmc.log('NEXT_EP: already exists (logical match), skipping scrape', xbmc.LOGINFO)
			#	return

			check_playlist_flag = self.check_playlist(b64_encode_dict=sorttitle_b64, playlistid=1)

			if check_playlist_flag == False:
				xbmc.log(str(str('Line ')+str(getframeinfo(currentframe()).lineno)+'___'+str(getframeinfo(currentframe()).filename)), level=xbmc.LOGINFO)
				xbmc.log(str(url), xbmc.LOGINFO)
				playlist.add(url, listitem)
				xbmc.log('NEXT_EP: added to playlist', xbmc.LOGINFO)
			else:
				xbmc.log('NEXT_EP: ALREADY_IN_PLAYLIST', xbmc.LOGINFO)

		#except Exception as e:
		#	xbmc.log(f'NEXT_EP ERROR: {str(e)}', xbmc.LOGINFO)

	def monitor(self):
		# DISABLED - using playlist monitor
		return
		try:
			ensure_dialog_dead, total_check_time = False, 0
			if self.media_type == 'episode':
				play_random_continual = self.sources_object.random_continual
				play_random = self.sources_object.random
				disable_autoplay_next_episode = self.sources_object.disable_autoplay_next_episode
				if disable_autoplay_next_episode: ku.notification('Scrape with Custom Values - Autoplay Next Episode Cancelled', 4500)
				if any((play_random_continual, play_random, disable_autoplay_next_episode)): self.autoplay_nextep, self.autoscrape_nextep = False, False
				else: self.autoplay_nextep, self.autoscrape_nextep = self.sources_object.autoplay_nextep, self.sources_object.autoscrape_nextep
			else:
				show_stinger, stinger_use_chapters, stingers_percentage_fallback = st.stingers_show(), st.stingers_use_chapters(), st.stingers_percentage()
				play_random_continual, self.autoplay_nextep, self.autoscrape_nextep = False, False, False
			while total_check_time <= 30 and not ku.get_visibility('Window.IsActive(fullscreenvideo)'):
				ku.sleep(100)
				total_check_time += 0.10
			ku.hide_busy_dialog()
			ku.sleep(1000)
			self._simkl_scrobble_start()
			if st.auto_enable_subs() and not st.submaker_enabled(): self.showSubtitles(True)
			while self.isPlayingVideo():
				try:
					if not ensure_dialog_dead:
						ensure_dialog_dead = True
						self.playback_close_dialogs()
					ku.sleep(1000)
					try: self.total_time, self.curr_time = self.getTotalTime(), self.getTime()
					except: ku.sleep(250); continue
					if not self._valid_playback_duration(self.total_time, self.curr_time):
						ku.sleep(250)
						continue
					self.current_point = round(float(self.curr_time/self.total_time * 100), 1)
					if play_random_continual:
						if self._should_prep_random_continual():
							self.random_continual_triggered = True
							self.run_random_continual()
							break
					elif self.current_point >= 90:
						if not self.media_marked: self.media_watched_marker()
					if self.media_type == 'episode':
						if self.autoplay_nextep or self.autoscrape_nextep:
							if not self.nextep_info_gathered: self.info_next_ep()
							if self._should_prep_next_ep(): self._schedule_next_ep(); break
					elif show_stinger and not self.movie_stingers_run: 
						final_chapter = (self.final_chapter(75) or stingers_percentage_fallback) if stinger_use_chapters else stingers_percentage_fallback
						if self.current_point >= final_chapter: self.run_movie_stingers()
				except: pass
				if not self.subs_searched: self.run_subtitles()
			ku.hide_busy_dialog()
			if not self.media_marked: self.media_watched_marker()
			self.clear_playback_properties(clear_navigation=False)
		except:
			ku.hide_busy_dialog()
			self.sources_object.playback_successful = False
			self.sources_object.cancel_all_playback = True
			return self.kill_dialog()

	def make_listing(self):
		listitem = ku.make_listitem()
		listitem.setPath(self.url)
		listitem.setContentLookup(False)
		if self.is_generic:
			info_tag = listitem.getVideoInfoTag(True)
			info_tag.setMediaType('video')
			play_name = ku.get_property('redlight.tb.play_filename') or self.url
			info_tag.setFilenameAndPath(play_name)
			info_tag.setTitle(os.path.basename(play_name) if play_name else '')
			mime = ku.get_property('redlight.tb.play_mime')
			if not mime:
				path_lower = (play_name or self.url or '').lower().split('|')[0].split('?')[0]
				for ext, mt in (
					('.m2ts', 'video/mp2t'), ('.mts', 'video/mp2t'), ('.ts', 'video/mp2t'),
					('.mkv', 'video/x-matroska'), ('.mp4', 'video/mp4'), ('.avi', 'video/x-msvideo'),
					('.mov', 'video/quicktime'), ('.webm', 'video/webm'),
				):
					if path_lower.endswith(ext):
						mime = mt
						break
			if mime:
				try:
					listitem.setMimeType(mime)
				except Exception:
					pass
			self._disable_kodi_url_resume(listitem)
		else:
			self.tmdb_id, self.imdb_id, self.tvdb_id = self.meta_get('tmdb_id', ''), self.meta_get('imdb_id', ''), self.meta_get('tvdb_id', '')
			self.media_type, self.title, self.year = self.meta_get('media_type'), self.meta_get('title'), self.meta_get('year')
			self.season, self.episode = self.meta_get('season', ''), self.meta_get('episode', '')
			poster = self.meta_get('poster') or ku.get_icon('box_office')
			fanart = self.meta_get('fanart') or ku.get_addon_fanart()
			clearlogo = self.meta_get('clearlogo') or ''
			duration, genre, trailer, mpaa = self.meta_get('duration'), self.meta_get('genre', ''), self.meta_get('trailer'), self.meta_get('mpaa')
			rating, votes = self.meta_get('rating'), self.meta_get('votes')
			premiered, studio, tagline = self.meta_get('premiered'), self.meta_get('studio', ''), self.meta_get('tagline')
			director, writer, country = self.meta_get('director', ''), self.meta_get('writer', ''), self.meta_get('country', '')
			cast = self.meta_get('short_cast', []) or self.meta_get('cast', []) or []
			listitem.setLabel(self.title)
			if self.media_type == 'movie':
				plot = self.meta_get('plot')
				listitem.setArt({'poster': poster, 'fanart': fanart, 'icon': poster, 'clearlogo': clearlogo})
				info_tag = listitem.getVideoInfoTag(True)
				info_tag.setMediaType('movie'), info_tag.setTitle(self.title), info_tag.setOriginalTitle(self.meta_get('original_title')), info_tag.setPlot(plot)
				info_tag.setYear(int(self.year)), info_tag.setRating(rating), info_tag.setVotes(votes), info_tag.setMpaa(mpaa)
				info_tag.setDuration(duration), info_tag.setCountries(country), info_tag.setTrailer(trailer), info_tag.setPremiered(premiered)
				info_tag.setTagLine(tagline), info_tag.setStudios(studio), info_tag.setIMDBNumber(self.imdb_id), info_tag.setGenres(genre)
				info_tag.setWriters(writer), info_tag.setDirectors(director), info_tag.setUniqueIDs({'imdb': self.imdb_id, 'tmdb': str(self.tmdb_id)})
				info_tag.setCast([ku.kodi_actor()(name=item['name'], role=item['role'], thumbnail=item['thumbnail']) for item in cast])
			else:
				if st.avoid_episode_spoilers() and int(self.meta_get('playcount', '0')) == 0: plot = self.meta_get('tvshow_plot') or '* Hidden to Prevent Spoilers *'
				else: plot = self.meta_get('plot') or self.meta_get('tvshow_plot')
				listitem.setArt({'poster': poster, 'fanart': fanart, 'icon': poster, 'clearlogo': clearlogo, 'tvshow.poster': poster, 'tvshow.clearlogo': clearlogo})
				info_tag = listitem.getVideoInfoTag(True)
				info_tag.setMediaType('episode'), info_tag.setTitle(self.meta_get('ep_name')), info_tag.setOriginalTitle(self.meta_get('original_title'))
				info_tag.setTvShowTitle(self.title), info_tag.setTvShowStatus(self.meta_get('status')), info_tag.setSeason(self.season), info_tag.setEpisode(self.episode)
				info_tag.setPlot(plot), info_tag.setYear(int(self.year)), info_tag.setRating(rating), info_tag.setVotes(votes)
				info_tag.setMpaa(mpaa), info_tag.setDuration(duration), info_tag.setTrailer(trailer), info_tag.setFirstAired(premiered)
				info_tag.setStudios(studio), info_tag.setIMDBNumber(self.imdb_id), info_tag.setGenres(genre), info_tag.setWriters(writer)
				info_tag.setDirectors(director), info_tag.setUniqueIDs({'imdb': self.imdb_id, 'tmdb': str(self.tmdb_id), 'tvdb': str(self.tvdb_id)})
				info_tag.setCast([ku.kodi_actor()(name=item['name'], role=item['role'], thumbnail=item['thumbnail']) for item in cast])
				info_tag.setFilenameAndPath(self.url)
			self.set_resume_point(listitem)
			if self.url and str(self.url).startswith('http'):
				self._disable_kodi_url_resume(listitem, keep_start_percent=True)
			self.set_playback_properties()
		return listitem

	def _simkl_scrobble_start(self):
		if self.is_generic or st.watched_indicators() != 2 or not st.simkl_user_active(): return
		from apis.simkl_api import simkl_scrobble
		percent = self.playback_percent if self.playback_percent else 0
		Thread(target=simkl_scrobble, args=('start', self.media_type, self.tmdb_id, percent, self.season, self.episode)).start()

	def _simkl_scrobble_stop(self, percent):
		if self.is_generic or st.watched_indicators() != 2 or not st.simkl_user_active(): return
		from apis.simkl_api import simkl_scrobble
		Thread(target=simkl_scrobble, args=('stop', self.media_type, self.tmdb_id, percent, self.season, self.episode)).start()

	def media_watched_marker(self, force_watched=False):
		self.media_marked = True
		try:
			if self.current_point >= 90 or force_watched:
				self._simkl_scrobble_stop(100)
				watched_function = ws.mark_movie if self.media_type == 'movie' else ws.mark_episode
				watched_params = {'action': 'mark_as_watched', 'tmdb_id': self.tmdb_id, 'title': self.title, 'year': self.year, 'season': self.season, 'episode': self.episode,
									'tvdb_id': self.tvdb_id, 'from_playback': 'true'}
				Thread(target=self.run_media_progress, args=(watched_function, watched_params)).start()
			else:
				ku.clear_property('redlight.random_episode_history')
				if self.current_point >= 5:
					progress_params = {'media_type': self.media_type, 'tmdb_id': self.tmdb_id, 'curr_time': self.curr_time, 'total_time': self.total_time,
									'title': self.title, 'season': self.season, 'episode': self.episode, 'from_playback': 'true'}
					Thread(target=self.run_media_progress, args=(ws.set_bookmark, progress_params)).start()
		except: pass

	def run_media_progress(self, function, params):
		try: function(params)
		except: pass

	def _valid_playback_duration(self, total_time=None, curr_time=None):
		try:
			total = total_time if total_time is not None else self.getTotalTime()
			curr = curr_time if curr_time is not None else self.getTime()
			if total in (0, 0.0, '0.0', '', None): return False
			if curr in (0, 0.0, '0.0', '', None): return False
			if float(total) < 60: return False
			return float(curr) > 0
		except:
			return False

	def _should_prep_next_ep(self):
		if ku.get_property(PROP_NEXTEP_PENDING) == 'true':
			return False
		if not self._valid_playback_duration(self.total_time, self.curr_time):
			return False
		try:
			remaining = round(float(self.total_time) - float(self.curr_time))
		except:
			return False
		return remaining > 0 and remaining <= self.start_prep

	def _ensure_random_continual_prep(self):
		if getattr(self, 'random_continual_start_prep', None) is not None: return
		if st.autoscrape_next_episode(): play_type = 'autoscrape_nextep'
		elif st.autoplay_next_episode(): play_type = 'autoplay_nextep'
		else: play_type = 'autoscrape_nextep'
		nextep_settings = st.auto_nextep_settings(play_type)
		final_chapter = self.final_chapter(90) if nextep_settings['use_chapters'] else None
		percentage = 100 - final_chapter if final_chapter else nextep_settings['window_percentage']
		try: window_time = round((percentage / 100) * self.total_time)
		except: window_time = nextep_settings['window_percentage']
		self.random_continual_start_prep = nextep_settings['scraper_time'] + window_time

	def _should_prep_random_continual(self):
		if getattr(self, 'random_continual_triggered', False): return False
		if not self._valid_playback_duration(self.total_time, self.curr_time): return False
		self._ensure_random_continual_prep()
		try: remaining = round(float(self.total_time) - float(self.curr_time))
		except: return False
		return remaining > 0 and remaining <= self.random_continual_start_prep

	def _schedule_next_ep(self):
		if ku.get_property(PROP_NEXTEP_PENDING) == 'true':
			return
		ku.set_property(PROP_NEXTEP_PENDING, 'true')
		meta = dict(self.meta)
		nextep_settings = dict(self.nextep_settings)
		player = self
		def _worker():
			try:
				if not player.media_marked:
					player.media_watched_marker(force_watched=True)
				ku.clear_property(PROP_NEXTEP_PENDING)
				from modules.episode_tools import EpisodeTools
				EpisodeTools(meta, nextep_settings).auto_nextep()
			except:
				pass
			finally:
				ku.clear_property(PROP_NEXTEP_PENDING)
		Thread(target=_worker, daemon=True).start()

	def run_next_ep(self):
		from modules.episode_tools import EpisodeTools
		if not self.media_marked: self.media_watched_marker(force_watched=True)
		EpisodeTools(self.meta, self.nextep_settings).auto_nextep()

	def run_random_continual(self):
		from modules.episode_tools import EpisodeTools
		if not self.media_marked: self.media_watched_marker(force_watched=True)
		EpisodeTools(self.meta).play_random_continual(False)

	def run_movie_stingers(self):
		self.movie_stingers_run = True
		stinger_keys = self.meta.get('stinger_keys', None)
		if not stinger_keys:
			try:
				keywords = self.meta.get('keywords', [])
				stinger_keys = [i['name'] for i in keywords['keywords'] if i['name'] in ('duringcreditsstinger', 'aftercreditsstinger')]
				self.meta['stinger_keys'] = stinger_keys
			except: pass
		if stinger_keys:
			from windows.base_window import open_window
			Thread(target=lambda: open_window(('windows.playback_notifications', 'StingersNotification'), 'playback_notifications.xml', meta=self.meta)).start()

	def set_resume_point(self, listitem):
		if self.playback_percent > 0.0: listitem.setProperty('StartPercent', str(self.playback_percent))

	def _disable_kodi_url_resume(self, listitem, keep_start_percent=False):
		# Kodi stores resume by stream URL/filename; debrid links reuse the same name and can reopen near EOF.
		if not keep_start_percent or float(listitem.getProperty('StartPercent') or 0) <= 0:
			listitem.setProperty('StartPercent', '0')
		listitem.setProperty('StartOffset', '0')
		try:
			listitem.getVideoInfoTag(True).setResumePoint(0.0)
		except:
			pass

	def info_next_ep(self):
		self.nextep_info_gathered = True
		play_type = 'autoplay_nextep' if self.autoplay_nextep else 'autoscrape_nextep'
		nextep_settings = st.auto_nextep_settings(play_type)
		watching_check = nextep_settings['watching_check']
		still_watching_check = 15 if self.meta_get('watch_count') == watching_check else 0
		final_chapter = self.final_chapter(90) if nextep_settings['use_chapters'] else None
		percentage = 100 - final_chapter if final_chapter else nextep_settings['window_percentage']
		try:
			window_time = round((percentage/100) * self.total_time) + still_watching_check
		except:
			window_time = nextep_settings['window_percentage'] + still_watching_check
		use_window = nextep_settings['alert_method'] == 0
		default_action = nextep_settings['default_action']
		self.start_prep = nextep_settings['scraper_time'] + window_time
		self.nextep_settings = {'use_window': use_window, 'window_time': window_time, 'default_action': default_action, 'play_type': play_type, 'watching_check': watching_check}

	def final_chapter(self, threshhold):
		try:
			final_chapter = float(ku.get_infolabel('Player.Chapters').split(',')[-1])
			if final_chapter >= threshhold: return final_chapter
		except: pass
		return None

	def kill_dialog(self):
		try:
			self.sources_object._kill_progress_dialog()
		except:
			if not getattr(self.sources_object, '_resolve_user_cancelled', False):
				ku.close_all_dialog()

	def set_constants(self, url, obj):
		self.url = url
		self.sources_object = obj
		self.is_generic = self.sources_object == 'video'
		self.kodi_monitor = ku.kodi_monitor()
		self.playback_successful = None
		self.cancel_all_playback = False
		#if not self.is_generic:
		#	self.meta = self.sources_object.meta
		#	self.meta_get, self.playback_percent = self.meta.get, self.sources_object.playback_percent or 0.0
		#	self.playing_filename = self.sources_object.playing_filename
		#	self.media_marked, self.nextep_info_gathered, self.movie_stingers_run = False, False, False
		#	self.subs_searched = False
		#	self.playing_item = self.sources_object.playing_item
		if not self.is_generic:
			self.meta = self.sources_object.meta
			self.meta_get, self.kodi_monitor, self.playback_percent = self.meta.get, ku.kodi_monitor(), self.sources_object.playback_percent or 0.0
			#self.playing_filename = self.sources_object.playing_filename
			self.media_marked, self.nextep_info_gathered = False, False
			self.playback_successful, self.cancel_all_playback = None, False
			#self.playing_item = self.sources_object.playing_item
			self.playing_filename = getattr(self.sources_object, 'playing_filename', '')
			self.playing_item = getattr(self.sources_object, 'playing_item', {})

	def run_subtitles(self):
		self.subs_searched = True
		if not st.auto_enable_subs(): return
		if not st.submaker_enabled(): return
		if not self.imdb_id: return
		try:
			from indexers.subtitles import Subtitles
			poster = self.meta.get('poster') or ku.get_icon('box_office')
			season = self.season if self.media_type == 'episode' else None
			episode = self.episode if self.media_type == 'episode' else None
			Thread(target=Subtitles().run, args=(self.imdb_id, season, episode, poster)).start()
		except: pass

	def set_playback_properties(self):
		try:
			trakt_ids = {'tmdb': self.tmdb_id, 'imdb': self.imdb_id, 'slug': make_trakt_slug(self.title)}
			if self.media_type == 'episode': trakt_ids['tvdb'] = self.tvdb_id
			ku.set_property('script.trakt.ids', json.dumps(trakt_ids))
			if self.playing_filename: ku.set_property('subs.player_filename', self.playing_filename)
		except: pass
		if self.media_type == 'episode': 
			TMDbHelper_NEW_PlayerInfoString = {'tmdb_type': self.media_type, 'tmdb_id': str(self.tmdb_id), 'imdb_id': str(self.imdb_id), 'tvdb_id': str(trakt_ids['tvdb']), 'season': self.meta['season'], 'episode': self.meta['episode']}
			xbmcgui.Window(10000).setProperty('TMDbHelper.PlayerInfoString', f'{TMDbHelper_NEW_PlayerInfoString}'.replace('\'','"'))
		elif self.media_type == 'movie':
			TMDbHelper_NEW_PlayerInfoString = {'tmdb_type': self.media_type, 'tmdb_id': str(self.tmdb_id), 'imdb_id': str(self.imdb_id), 'year': str(self.year)}
			xbmcgui.Window(10000).setProperty('TMDbHelper.PlayerInfoString', f'{TMDbHelper_NEW_PlayerInfoString}'.replace('\'','"'))


		#elif self.media_type == 'movie':
		#	data = {'tmdb_type': 'movie', 'tmdb_id': str(self.tmdb_id), 'imdb_id': str(self.imdb_id)}
		#	xbmcgui.Window(10000).setProperty('TMDbHelper.PlayerInfoString',json.dumps(data))

	def safe_stop(self):
		try:
			if ku.get_property(PROP_PLAY_OPENING) == 'true' or (self.isPlaying() and not self.isPlayingVideo()):
				for _ in range(80):
					try:
						if self.isPlayingVideo():
							ku.sleep(300)
							break
					except:
						pass
					ku.sleep(100)
				else:
					ku.sleep(400)
			ku.execute_builtin('PlayerControl(Stop)', block=True)
			stable_idle = 0
			for _ in range(80):
				playing = False
				try:
					playing = self.isPlaying() or self.isPlayingVideo()
				except:
					pass
				if playing:
					stable_idle = 0
					try:
						self.stop()
					except:
						pass
					ku.execute_builtin('PlayerControl(Stop)', block=False)
				else:
					stable_idle += 1
					if stable_idle >= 6:
						ku.sleep(400)
						return
				ku.sleep(100)
		except:
			pass
		finally:
			ku.clear_property(PROP_PLAY_OPENING)

	def clear_playback_properties(self, clear_navigation=True):
		if clear_navigation:
			ku.clear_property('redlight.window_stack')
		ku.clear_property('script.trakt.ids')
		ku.clear_property('subs.player_filename')

	def run_error(self, message=None):
		ku.clear_property(PROP_PLAY_OPENING)
		try:
			if not self.is_generic:
				self.sources_object.playback_successful = False
		except:
			pass
		self.clear_playback_properties(clear_navigation=not self.is_generic)
		if self.is_generic and ku.get_property('redlight.browse_playback') == 'true':
			return ku.notification('Playback Failed', 4000, settle_ms=400)
		# play_file walks the resolve queue and calls playback_failed_action after the last attempt.
		if not self.is_generic and getattr(self, 'sources_object', None):
			return
		text = message or 'This link could not be played. It may be expired, removed, or unsupported on this device.'
		ku.hide_busy_dialog()
		ku.sleep(400)
		try:
			return ku.kodi_dialog().ok('Playback failed', text)
		except Exception:
			try:
				return ku.ok_dialog(heading='Playback failed', text=text)
			except Exception:
				return ku.notification('Playback Failed', 4000, settle_ms=400)
