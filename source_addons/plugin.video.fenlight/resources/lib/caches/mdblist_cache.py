# -*- coding: utf-8 -*-
"""Persistent MDBList cache used by FLAM's tracking provider.

The watched/progress table layout deliberately matches watched.db and
traktcache.db.  That lets the existing FLAM list builders consume MDBList data
without making TMDb (which remains the metadata provider) responsible for
playback state.
"""
from threading import Thread
from ast import literal_eval
from caches.base_cache import connect_database
from modules.kodi_utils import confirm_dialog


class MDBListCache:
    def get(self, key):
        try:
            row = connect_database('mdblist_db').execute(
                'SELECT data FROM mdblist_data WHERE id = ?', (key,)
            ).fetchone()
            return literal_eval(row[0]) if row else None
        except Exception:
            return None

    def set(self, key, data):
        try:
            connect_database('mdblist_db').execute(
                'INSERT OR REPLACE INTO mdblist_data (id, data) VALUES (?, ?)',
                (key, repr(data))
            )
            return True
        except Exception:
            return False

    def delete(self, key):
        try:
            connect_database('mdblist_db').execute(
                'DELETE FROM mdblist_data WHERE id = ?', (key,)
            )
        except Exception:
            pass

    def delete_like(self, pattern):
        try:
            connect_database('mdblist_db').execute(
                'DELETE FROM mdblist_data WHERE id LIKE ?', (pattern,)
            )
        except Exception:
            pass


class MDBListWatchedCache:
    @staticmethod
    def _replace(db_type, table, rows):
        dbcon = connect_database('mdblist_db')
        dbcon.execute('DELETE FROM %s WHERE db_type = ?' % table, (db_type,))
        if rows:
            if table == 'watched':
                dbcon.executemany(
                    'INSERT OR REPLACE INTO watched VALUES (?, ?, ?, ?, ?, ?)', rows
                )
            else:
                dbcon.executemany(
                    'INSERT OR REPLACE INTO progress VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)', rows
                )

    def set_bulk_movie_watched(self, rows):
        self._replace('movie', 'watched', rows)

    def set_bulk_tvshow_watched(self, rows):
        self._replace('episode', 'watched', rows)

    def set_bulk_movie_progress(self, rows):
        self._replace('movie', 'progress', rows)

    def set_bulk_tvshow_progress(self, rows):
        self._replace('episode', 'progress', rows)


    @staticmethod
    def get_hidden_progress():
        try:
            row = connect_database('mdblist_db').execute(
                'SELECT status FROM watched_status WHERE db_type = ?',
                ('hidden_progress_items',)
            ).fetchone()
            return literal_eval(row[0]) if row else []
        except Exception:
            return []

    @staticmethod
    def set_hidden_progress(items):
        connect_database('mdblist_db').execute(
            'INSERT OR REPLACE INTO watched_status VALUES (?, ?, ?)',
            ('hidden_progress_items', 'hidden', repr(items or []))
        )


mdblist_cache = MDBListCache()
mdblist_watched_cache = MDBListWatchedCache()


def clear_mdblist_collection_watchlist_data(list_type=None, media_type=None):
    if list_type and media_type:
        media_type = 'movie' if media_type in ('movie', 'movies') else 'tvshow'
        mdblist_cache.delete('mdblist_%s_%s' % (list_type, media_type))
        return
    mdblist_cache.delete_like('mdblist_collection_%')
    mdblist_cache.delete_like('mdblist_watchlist_%')


def clear_mdblist_list_data():
    mdblist_cache.delete_like('mdblist_lists%')
    mdblist_cache.delete_like('mdblist_list_contents_%')


def clear_all_mdblist_cache_data(silent=False, refresh=True):
    try:
        if not (silent or confirm_dialog()):
            return False
        dbcon = connect_database('mdblist_db')
        for table in ('mdblist_data', 'progress', 'watched', 'watched_status'):
            dbcon.execute('DELETE FROM %s' % table)
        dbcon.execute('VACUUM')
        if refresh:
            from apis.mdblist_api import mdblist_sync_activities
            Thread(target=mdblist_sync_activities, kwargs={'force_update': True}).start()
        return True
    except Exception:
        return False
