# -*- coding: utf-8 -*-
"""Provider-neutral watched/progress data for Bingie directory items.

Trakt keeps using the helper's native sync cache. MDBList and Local-only read
FLAM's provider-specific SQLite cache, whose schema is intentionally identical
to FLAM's original watched/Trakt tables.
"""
import os
import sqlite3
import xbmcvfs
from tmdbbingiehelper.lib.addon.plugin import get_setting


class TrackingPlayData:
    def __init__(self, pauseplayprogress=False, watchedindicators=False,
                 unwatchedepisodes=False, traktepisodetypes=True):
        self._pauseplayprogress = pauseplayprogress
        self._watchedindicators = watchedindicators
        self._unwatchedepisodes = unwatchedepisodes
        self._traktepisodetypes = traktepisodetypes
        try:
            self.provider = int(get_setting('tracking_provider') or 0)
        except Exception:
            self.provider = 0
        self._trakt = None
        if self.provider == 1:
            from tmdbbingiehelper.lib.items.trakt import TraktPlayData
            self._trakt = TraktPlayData(
                pauseplayprogress=pauseplayprogress,
                watchedindicators=watchedindicators,
                unwatchedepisodes=unwatchedepisodes,
                traktepisodetypes=traktepisodetypes)

    @property
    def db_path(self):
        filename = 'mdblistcache.db' if self.provider == 0 else 'watched.db'
        return xbmcvfs.translatePath(
            'special://profile/addon_data/plugin.video.fenlight/databases/%s' % filename)

    def _query_one(self, query, values=()):
        if not os.path.exists(self.db_path):
            return None
        db = None
        try:
            db = sqlite3.connect(self.db_path, timeout=5)
            return db.execute(query, values).fetchone()
        except Exception:
            return None
        finally:
            try:
                db.close()
            except Exception:
                pass

    def pre_sync(self, **kwargs):
        if self._trakt:
            return self._trakt.pre_sync(**kwargs)

    def pre_sync_start(self, **kwargs):
        if self._trakt:
            return self._trakt.pre_sync_start(**kwargs)

    def pre_sync_join(self):
        if self._trakt:
            return self._trakt.pre_sync_join()

    def set_episode_type(self, li):
        # Episode-type enrichment is a Trakt metadata feature, not watched state.
        if self._trakt:
            return self._trakt.set_episode_type(li)

    @staticmethod
    def _media_id(li):
        if li.infolabels.get('mediatype') == 'movie':
            return li.unique_ids.get('tmdb')
        return li.unique_ids.get('tvshow.tmdb') or li.unique_ids.get('tmdb')

    def set_playprogress(self, li):
        if self._trakt:
            return self._trakt.set_playprogress(li)
        if not self._pauseplayprogress:
            return
        media_type = li.infolabels.get('mediatype')
        if media_type not in ('movie', 'episode'):
            return
        duration = li.infolabels.get('duration')
        media_id = self._media_id(li)
        if not duration or not media_id:
            return
        if media_type == 'movie':
            row = self._query_one(
                'SELECT resume_point FROM progress WHERE db_type = ? AND media_id = ?',
                ('movie', str(media_id)))
        else:
            row = self._query_one(
                'SELECT resume_point FROM progress WHERE db_type = ? AND media_id = ? AND season = ? AND episode = ?',
                ('episode', str(media_id), int(li.infolabels.get('season') or 0), int(li.infolabels.get('episode') or 0)))
        try:
            progress = float(row[0]) if row else 0
        except Exception:
            progress = 0
        if progress < 4 or progress > 96:
            progress = 0
        li.infoproperties['ResumeTime'] = int(float(duration) * progress // 100)
        li.infoproperties['TotalTime'] = int(float(duration))

    def get_playcount(self, li):
        if self._trakt:
            return self._trakt.get_playcount(li)
        if not self._watchedindicators:
            return
        media_type = li.infolabels.get('mediatype')
        media_id = self._media_id(li)
        if not media_id:
            return 0
        if media_type == 'movie':
            row = self._query_one(
                'SELECT 1 FROM watched WHERE db_type = ? AND media_id = ? LIMIT 1',
                ('movie', str(media_id)))
            return 1 if row else 0
        if media_type == 'episode':
            row = self._query_one(
                'SELECT 1 FROM watched WHERE db_type = ? AND media_id = ? AND season = ? AND episode = ? LIMIT 1',
                ('episode', str(media_id), int(li.infolabels.get('season') or 0), int(li.infolabels.get('episode') or 0)))
            return 1 if row else 0
        if media_type == 'season':
            row = self._query_one(
                'SELECT COUNT(*) FROM watched WHERE db_type = ? AND media_id = ? AND season = ?',
                ('episode', str(media_id), int(li.infolabels.get('season') or 0)))
        elif media_type == 'tvshow':
            row = self._query_one(
                'SELECT COUNT(*) FROM watched WHERE db_type = ? AND media_id = ?',
                ('episode', str(media_id)))
        else:
            return 0
        watched_count = int(row[0] or 0) if row else 0
        total = int(li.infolabels.get('episode') or 0)
        return min(watched_count, total) if total else watched_count
