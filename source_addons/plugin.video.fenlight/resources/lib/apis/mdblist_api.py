# -*- coding: utf-8 -*-
"""MDBList account, tracking, scrobbling and personal-list API for FLAM.

MDBList is a playback-state provider only. TMDb remains FLAM's source for
metadata, artwork and media details. The local MDBList database mirrors the
existing Trakt database layout so the normal FLAM watched/progress list builders
can consume either provider without provider-specific UI code.
"""
import json
import time
import requests
from caches.mdblist_cache import (
    mdblist_cache, mdblist_watched_cache,
    clear_mdblist_collection_watchlist_data, clear_mdblist_list_data
)
from caches.settings_cache import get_setting, set_setting
from modules import kodi_utils, settings
from modules.metadata import movie_meta_external_id, tvshow_meta_external_id
from modules.utils import sort_for_article, copy2clip, make_qrcode, make_tinyurl

API_ENDPOINT = 'https://api.mdblist.com'
OAUTH_DEVICE_ENDPOINT = '%s/oauth/device-authorization/' % API_ENDPOINT
OAUTH_TOKEN_ENDPOINT = '%s/oauth/token/' % API_ENDPOINT
OAUTH_REVOKE_ENDPOINT = '%s/oauth/revoke_token/' % API_ENDPOINT
OAUTH_DEVICE_GRANT = 'urn:ietf:params:oauth:grant-type:device_code'
REQUEST_TIMEOUT = 20
APP_VERSION = 'FLAM-%s' % kodi_utils.addon_info('version')
UTC_FORMAT = '%Y-%m-%dT%H:%M:%SZ'
EMPTY_VALUES = (None, '', '0', 'empty_setting')


def _clean_setting(setting_id, default=''):
    value = get_setting('fenlight.%s' % setting_id, default)
    return '' if value in EMPTY_VALUES else str(value).strip()


def _api_key():
    return _clean_setting('mdblist.apikey')


def _access_token():
    return _clean_setting('mdblist.access_token')


def _refresh_token():
    return _clean_setting('mdblist.refresh_token')


def _client_id():
    value = settings.mdblist_client()
    return '' if value in EMPTY_VALUES else str(value).strip()


def mdblist_has_oauth():
    return bool(_access_token())


def mdblist_has_api_key():
    return bool(_api_key())


def mdblist_auth_method():
    if mdblist_has_oauth() and mdblist_has_api_key():
        return 'OAuth / Device Code + API key fallback'
    if mdblist_has_oauth():
        return 'OAuth / Device Code'
    if mdblist_has_api_key():
        return 'API key'
    return 'Not connected'


def mdblist_user_active():
    user = _clean_setting('mdblist.user')
    return bool(user and (mdblist_has_oauth() or mdblist_has_api_key()))


def _refresh_account_properties():
    try:
        from apis.tracking_api import refresh_account_properties
        refresh_account_properties()
    except Exception:
        pass


def _sync_helper_settings(provider=None):
    if provider is None:
        provider = get_setting('fenlight.tracking.provider', '0')
    try:
        helper = kodi_utils.addon('plugin.video.tmdb.bingie.helper')
        helper.setSetting('mdblist_apikey', _api_key())
        helper.setSetting('mdblist_access_token', _access_token())
        helper.setSetting('mdblist_refresh_token', _refresh_token())
        helper.setSetting('mdblist_token_expires', _clean_setting('mdblist.expires', '0') or '0')
        helper.setSetting('mdblist_client_id', _client_id())
        helper.setSetting('tracking_provider', str(provider))
    except Exception:
        pass
    _refresh_account_properties()


def _save_oauth_tokens(payload):
    access = payload.get('access_token') or ''
    if not access:
        return False
    set_setting('mdblist.access_token', access)
    set_setting('mdblist.refresh_token', payload.get('refresh_token') or _refresh_token() or 'empty_setting')
    try:
        expires = int(time.time()) + int(payload.get('expires_in', 2592000))
    except Exception:
        expires = int(time.time()) + 2592000
    set_setting('mdblist.expires', str(expires))
    set_setting('mdblist.auth_method', 'oauth')
    return True


def _refresh_oauth_access_token():
    refresh = _refresh_token()
    client_id = _client_id()
    if not refresh or not client_id:
        return ''
    try:
        response = requests.post(
            OAUTH_TOKEN_ENDPOINT,
            data={
                'grant_type': 'refresh_token',
                'refresh_token': refresh,
                'client_id': client_id
            },
            headers={'User-Agent': APP_VERSION, 'Accept': 'application/json'},
            timeout=REQUEST_TIMEOUT
        )
        if response.status_code < 200 or response.status_code >= 300:
            kodi_utils.logger('MDBList OAuth', 'Refresh failed: HTTP %s' % response.status_code)
            return ''
        payload = response.json()
        if not _save_oauth_tokens(payload):
            return ''
        _sync_helper_settings()
        return _access_token()
    except Exception as exc:
        kodi_utils.logger('MDBList OAuth', 'Refresh failed: %s' % str(exc))
        return ''


def _valid_access_token():
    token = _access_token()
    if not token:
        return ''
    try:
        expires = float(_clean_setting('mdblist.expires', '0') or 0)
    except Exception:
        expires = 0
    if expires and time.time() >= expires - 300:
        return _refresh_oauth_access_token()
    return token


def _request_once(method, url, query, data, headers):
    return requests.request(
        method.upper(), url, params=query, json=data,
        headers=headers, timeout=REQUEST_TIMEOUT
    )


def call_mdblist(path, params=None, data=None, method='get', authenticated=True, return_response=False):
    """Call MDBList using OAuth Bearer auth first, then the saved API key.

    Both credentials are retained. OAuth token refresh is automatic, and a saved
    API key is used as a fallback if the OAuth token cannot be refreshed.
    """
    query = dict(params or {})
    url = '%s/%s' % (API_ENDPOINT, str(path).lstrip('/'))
    headers = {
        'Accept': 'application/json',
        'Content-Type': 'application/json',
        'User-Agent': APP_VERSION
    }
    auth_kind = 'none'
    if authenticated:
        token = _valid_access_token()
        if token:
            headers['Authorization'] = 'Bearer %s' % token
            auth_kind = 'oauth'
        elif _api_key():
            query['apikey'] = _api_key()
            auth_kind = 'apikey'
        else:
            return None
    try:
        response = _request_once(method, url, query, data, headers)
        if response.status_code == 401 and auth_kind == 'oauth':
            refreshed = _refresh_oauth_access_token()
            if refreshed:
                headers['Authorization'] = 'Bearer %s' % refreshed
                response = _request_once(method, url, query, data, headers)
            elif _api_key():
                headers.pop('Authorization', None)
                query['apikey'] = _api_key()
                response = _request_once(method, url, query, data, headers)
        if response.status_code == 429:
            try:
                retry_after = max(1, min(30, int(response.headers.get('Retry-After', '1'))))
            except Exception:
                retry_after = 1
            kodi_utils.sleep(retry_after * 1000)
            response = _request_once(method, url, query, data, headers)
        if response.status_code < 200 or response.status_code >= 300:
            kodi_utils.logger(
                'MDBList Error', '%s %s: HTTP %s %s' % (
                    method.upper(), path, response.status_code,
                    (response.text or '')[:300]
                )
            )
            return None
        if return_response:
            return response
        if not response.content:
            return True
        try:
            return response.json()
        except Exception:
            return response.text
    except Exception as exc:
        kodi_utils.logger('MDBList Error', '%s %s: %s' % (method.upper(), path, str(exc)))
        return None


def _store_profile(profile):
    if not isinstance(profile, dict):
        return False
    username = profile.get('username') or profile.get('name') or profile.get('user_name') or 'MDBList User'
    set_setting('mdblist.user', str(username))
    return str(username)


def _finish_authentication(profile, message):
    username = _store_profile(profile)
    if not username:
        return False
    _sync_helper_settings()
    try:
        from apis.tracking_api import provider_id
        if provider_id() == 0:
            mdblist_sync_activities(force_update=True)
    except Exception:
        pass
    kodi_utils.notification('%s: %s' % (message, username), 3500)
    kodi_utils.kodi_refresh()
    return True


def mdblist_set_client_id(params=None):
    current = _client_id()
    value = kodi_utils.kodi_dialog().input('MDBList OAuth Client ID', defaultt=current)
    if not value:
        return False
    set_setting('mdblist.client_id', value.strip())
    _sync_helper_settings()
    kodi_utils.notification('MDBList OAuth Client ID Saved', 3000)
    kodi_utils.kodi_refresh()
    return True


def mdblist_api_key_authenticate(params=None):
    current = _api_key()
    key = kodi_utils.kodi_dialog().input('Enter MDBList API Key', defaultt=current)
    if not key:
        return False
    key = key.strip()
    try:
        response = requests.get(
            '%s/user' % API_ENDPOINT,
            params={'apikey': key},
            headers={'Accept': 'application/json', 'User-Agent': APP_VERSION},
            timeout=REQUEST_TIMEOUT
        )
        profile = response.json() if 200 <= response.status_code < 300 else None
    except Exception:
        profile = None
    if not isinstance(profile, dict) or not (profile.get('user_id') or profile.get('username') or profile.get('name')):
        return kodi_utils.notification('Invalid MDBList API Key', 4000)
    set_setting('mdblist.apikey', key)
    if not mdblist_has_oauth():
        set_setting('mdblist.auth_method', 'apikey')
    return _finish_authentication(profile, 'MDBList API Key Authorized')


def mdblist_device_authenticate(params=None):
    client_id = _client_id()
    if not client_id:
        if not kodi_utils.confirm_dialog(
            text='MDBList Device Code login requires an OAuth Client ID registered for this FLAM build. Enter it now?\n\nAPI-key login remains available without a Client ID.',
            ok_label='Enter Client ID', cancel_label='Cancel'):
            return False
        if not mdblist_set_client_id():
            return False
        client_id = _client_id()
    try:
        response = requests.post(
            OAUTH_DEVICE_ENDPOINT,
            data={'client_id': client_id, 'scope': 'write'},
            headers={'Accept': 'application/json', 'User-Agent': APP_VERSION},
            timeout=REQUEST_TIMEOUT
        )
        payload = response.json()
    except Exception as exc:
        kodi_utils.logger('MDBList OAuth', 'Device authorization failed: %s' % str(exc))
        return kodi_utils.notification('MDBList Device Login Failed', 4000)
    if not isinstance(payload, dict) or not payload.get('device_code'):
        message = payload.get('error_description') or payload.get('error') or 'Unknown response'
        kodi_utils.logger('MDBList OAuth', 'Device authorization failed: %s' % message)
        return kodi_utils.notification('MDBList Device Login Failed: %s' % message, 5000)

    device_code = payload['device_code']
    user_code = str(payload.get('user_code') or '')
    verification_uri = payload.get('verification_uri') or payload.get('verification_url') or 'https://mdblist.com'
    qr_url = payload.get('verification_uri_complete') or payload.get('verification_url_complete') or verification_uri
    try:
        expires_in = int(payload.get('expires_in', 300))
    except Exception:
        expires_in = 300
    try:
        interval = max(1, int(payload.get('interval', 5)))
    except Exception:
        interval = 5

    qr_code = make_qrcode(qr_url) or ''
    short_url = make_tinyurl(verification_uri)
    copy2clip(qr_url)
    extra = '[CR]OR....[CR]visit [B]%s[/B]' % short_url if short_url else ''
    content = 'Enter [B]%s[/B] at [B]%s[/B][CR]OR....[CR]Scan the [B]QR Code[/B]%s' % (
        user_code, verification_uri, extra)
    progress = kodi_utils.progress_dialog('MDBList Authorize', qr_code)
    progress.update(content, 0)
    start = time.time()
    token_payload = None
    try:
        while not progress.iscanceled() and time.time() - start < expires_in:
            kodi_utils.sleep(interval * 1000)
            token_response = requests.post(
                OAUTH_TOKEN_ENDPOINT,
                data={
                    'grant_type': OAUTH_DEVICE_GRANT,
                    'device_code': device_code,
                    'client_id': client_id
                },
                headers={'Accept': 'application/json', 'User-Agent': APP_VERSION},
                timeout=REQUEST_TIMEOUT
            )
            try:
                token_data = token_response.json()
            except Exception:
                token_data = {}
            if token_data.get('access_token'):
                token_payload = token_data
                break
            error = token_data.get('error') or ''
            if error == 'slow_down':
                interval += 5
            elif error in ('expired_token', 'access_denied'):
                break
            elif error not in ('authorization_pending', 'slow_down', '') and token_response.status_code >= 400:
                kodi_utils.logger('MDBList OAuth', 'Token poll failed: %s' % (token_data.get('error_description') or error))
                break
            elapsed = time.time() - start
            progress.update(content, min(99, int(100 * elapsed / max(expires_in, 1))))
    except Exception as exc:
        kodi_utils.logger('MDBList OAuth', 'Token poll failed: %s' % str(exc))
    finally:
        try:
            progress.close()
        except Exception:
            pass

    if not token_payload or not _save_oauth_tokens(token_payload):
        kodi_utils.notification('MDBList Authorization Cancelled or Expired', 4000)
        return False
    profile = call_mdblist('user')
    return _finish_authentication(profile, 'MDBList Account Authorized')


def mdblist_authenticate(params=None):
    params = params or {}
    method = params.get('method')
    if method in ('device', 'oauth'):
        return mdblist_device_authenticate(params)
    if method in ('apikey', 'api_key'):
        return mdblist_api_key_authenticate(params)
    options = [
        {'line1': 'Connect with QR / Device Code', 'line2': 'Official MDBList OAuth login for TV devices'},
        {'line1': 'Enter or Replace API Key', 'line2': 'Manual fallback from MDBList Preferences'}
    ]
    selection = kodi_utils.select_dialog(
        [0, 1], items=json.dumps(options), heading='Connect MDBList')
    if selection == 0:
        return mdblist_device_authenticate(params)
    if selection == 1:
        return mdblist_api_key_authenticate(params)
    return False


def mdblist_disconnect_oauth(params=None):
    token = _access_token()
    if not token:
        return False
    if not kodi_utils.confirm_dialog(text='Disconnect MDBList OAuth from FLAM? The saved API key, if any, will be kept.', ok_label='Disconnect', cancel_label='Cancel'):
        return False
    try:
        if _client_id():
            requests.post(
                OAUTH_REVOKE_ENDPOINT,
                data={'token': token, 'client_id': _client_id()},
                headers={'User-Agent': APP_VERSION}, timeout=REQUEST_TIMEOUT)
    except Exception:
        pass
    set_setting('mdblist.access_token', 'empty_setting')
    set_setting('mdblist.refresh_token', 'empty_setting')
    set_setting('mdblist.expires', '0')
    set_setting('mdblist.auth_method', 'apikey' if _api_key() else 'empty_setting')
    if not _api_key():
        set_setting('mdblist.user', 'empty_setting')
    _sync_helper_settings()
    kodi_utils.notification('MDBList OAuth Disconnected', 3000)
    kodi_utils.kodi_refresh()
    return True


def mdblist_remove_api_key(params=None):
    if not _api_key():
        return False
    if not kodi_utils.confirm_dialog(text='Remove the saved MDBList API key? OAuth login, if connected, will be kept.', ok_label='Remove', cancel_label='Cancel'):
        return False
    set_setting('mdblist.apikey', 'empty_setting')
    set_setting('mdblist.auth_method', 'oauth' if _access_token() else 'empty_setting')
    if not _access_token():
        set_setting('mdblist.user', 'empty_setting')
    _sync_helper_settings()
    kodi_utils.notification('MDBList API Key Removed', 3000)
    kodi_utils.kodi_refresh()
    return True


def mdblist_revoke_authentication(params=None):
    """Compatibility route that explicitly removes all MDBList credentials."""
    if not kodi_utils.confirm_dialog(
        text='Disconnect MDBList completely? This removes OAuth tokens and the saved API key. It does not change the selected tracking provider or any Trakt details.',
        ok_label='Disconnect All', cancel_label='Cancel'):
        return False
    token = _access_token()
    try:
        if token and _client_id():
            requests.post(
                OAUTH_REVOKE_ENDPOINT,
                data={'token': token, 'client_id': _client_id()},
                headers={'User-Agent': APP_VERSION}, timeout=REQUEST_TIMEOUT)
    except Exception:
        pass
    for setting_id, value in (
        ('mdblist.apikey', 'empty_setting'),
        ('mdblist.access_token', 'empty_setting'),
        ('mdblist.refresh_token', 'empty_setting'),
        ('mdblist.expires', '0'),
        ('mdblist.auth_method', 'empty_setting'),
        ('mdblist.user', 'empty_setting')):
        set_setting(setting_id, value)
    _sync_helper_settings()
    kodi_utils.notification('MDBList Authorization Removed', 3000)
    kodi_utils.kodi_refresh()
    return True

def _ids(item):
    if not isinstance(item, dict):
        return {}
    values = item.get('ids') if isinstance(item.get('ids'), dict) else item
    result = {}
    aliases = {
        'tmdb': ('tmdb', 'tmdbid', 'tmdb_id'),
        'imdb': ('imdb', 'imdbid', 'imdb_id'),
        'tvdb': ('tvdb', 'tvdbid', 'tvdb_id'),
        'trakt': ('trakt', 'traktid', 'trakt_id'),
        'mdblist': ('mdblist', 'mdblistid', 'mdblist_id')
    }
    for output, keys in aliases.items():
        for key in keys:
            value = values.get(key)
            if value not in (None, '', 0, '0'):
                result[output] = value
                break
    return result


def _resolve_tmdb(media_type, ids):
    value = ids.get('tmdb')
    if value:
        return str(value)
    api_key = settings.tmdb_api_key()
    try:
        if ids.get('imdb'):
            result = movie_meta_external_id('imdb_id', ids['imdb'], api_key) if media_type == 'movie' \
                else tvshow_meta_external_id('imdb_id', ids['imdb'], api_key)
            if result and result.get('id'):
                return str(result['id'])
        if media_type != 'movie' and ids.get('tvdb'):
            result = tvshow_meta_external_id('tvdb_id', ids['tvdb'], api_key)
            if result and result.get('id'):
                return str(result['id'])
    except Exception:
        pass
    return None


def _utc_now():
    return time.strftime(UTC_FORMAT, time.gmtime())


def _sync_payload(media, media_id, season=None, episode=None, rating=None, watched_at=None):
    ids = {'tmdb': int(media_id)}
    if media in ('movie', 'movies'):
        item = {'ids': ids}
        if rating is not None:
            item['rating'] = int(rating)
        if watched_at:
            item['watched_at'] = watched_at
        return {'movies': [item]}
    show = {'ids': ids}
    if season not in (None, ''):
        season_item = {'number': int(season)}
        if episode not in (None, ''):
            ep = {'number': int(episode)}
            if rating is not None:
                ep['rating'] = int(rating)
            if watched_at:
                ep['watched_at'] = watched_at
            season_item['episodes'] = [ep]
        elif rating is not None:
            season_item['rating'] = int(rating)
        show['seasons'] = [season_item]
    elif rating is not None:
        show['rating'] = int(rating)
    return {'shows': [show]}


def _scrobble_payload(media, media_id, percent=0, season=None, episode=None):
    if media in ('movie', 'movies'):
        return {
            'movie': {'ids': {'tmdb': int(media_id)}},
            'progress': float(percent),
            'app_version': APP_VERSION
        }
    return {
        'show': {
            'ids': {'tmdb': int(media_id)},
            'season': {
                'number': int(season),
                'episode': {'number': int(episode)}
            }
        },
        'progress': float(percent),
        'app_version': APP_VERSION
    }


def mdblist_watched_status_mark(action, media, media_id, tvdb_id=0, season=None, episode=None, key='tmdb'):
    path = 'sync/watched' if action == 'mark_as_watched' else 'sync/watched/remove'
    payload = _sync_payload(
        media, media_id, season, episode,
        watched_at=_utc_now() if action == 'mark_as_watched' else None
    )
    return call_mdblist(path, data=payload, method='post') is not None


def mdblist_progress(action, media, media_id, percent, season=None, episode=None,
                     resume_id=None, refresh_mdblist=False):
    if action == 'clear_progress':
        if resume_id not in (None, '', 0, '0'):
            payload = {'id': int(resume_id)}
        else:
            payload = _scrobble_payload(media, media_id, 0, season, episode)
            payload.pop('progress', None)
        result = call_mdblist('scrobble/clear', data=payload, method='post')
    else:
        result = call_mdblist(
            'scrobble/pause',
            data=_scrobble_payload(media, media_id, percent, season, episode),
            method='post'
        )
    if refresh_mdblist:
        mdblist_sync_activities(force_update=True)
    return result


def mdblist_scrobble(method, media, media_id, percent, season=None, episode=None):
    if method not in ('start', 'pause', 'stop', 'clear'):
        return None
    payload = _scrobble_payload(media, media_id, percent, season, episode)
    if method == 'clear':
        payload.pop('progress', None)
    return call_mdblist('scrobble/%s' % method, data=payload, method='post')


def _local_hidden_items():
    return mdblist_watched_cache.get_hidden_progress()


def mdblist_get_hidden_items(list_type='dropped'):
    cached = mdblist_cache.get('mdblist_hidden_%s' % list_type)
    if cached is not None:
        return cached
    data = call_mdblist('sync/dropped')
    if data is None:
        return _local_hidden_items()
    rows = data.get('shows', []) if isinstance(data, dict) else (data or [])
    result = []
    for item in rows:
        show = item.get('show', item) if isinstance(item, dict) else {}
        tmdb_id = _resolve_tmdb('tvshow', _ids(show))
        if tmdb_id:
            try:
                result.append(int(tmdb_id))
            except Exception:
                pass
    # Keep local-only drops too: MDBList cannot create a dropped record for a
    # show that has no watch-history row yet.
    result = sorted(set(result + _local_hidden_items()))
    mdblist_cache.set('mdblist_hidden_%s' % list_type, result)
    mdblist_watched_cache.set_hidden_progress(result)
    return result


def hide_unhide_progress_items(params):
    action = params['action']
    media_id = int(params['media_id'])
    current = _local_hidden_items()
    if action == 'drop' and media_id not in current:
        current.append(media_id)
    elif action != 'drop' and media_id in current:
        current.remove(media_id)
    mdblist_watched_cache.set_hidden_progress(current)

    show = {'ids': {'tmdb': media_id}}
    if action == 'drop':
        show['dropped_at'] = _utc_now()
    path = 'sync/dropped' if action == 'drop' else 'sync/dropped/remove'
    result = call_mdblist(path, data={'shows': [show]}, method='post')
    # Local state is authoritative for UI hiding when MDBList cannot create a
    # dropped row (its API only updates an existing watched-history record).
    mdblist_cache.set('mdblist_hidden_dropped', sorted(set(current)))
    if params.get('refresh', 'true') == 'true':
        kodi_utils.kodi_refresh()
    return result if result is not None else True


def _paged(path, params=None, root_keys=()):
    """Read every MDBList page using its documented 100-item page size.

    Newer endpoints expose pagination in the JSON body, while list endpoints
    may expose ``X-Has-More`` headers. Supporting both prevents larger
    watchlists, collections and personal lists from being silently truncated.
    """
    offset, limit = 0, 100
    combined = {key: [] for key in root_keys}
    while True:
        query = dict(params or {})
        query.update({'offset': offset, 'limit': limit})
        response = call_mdblist(path, params=query, return_response=True)
        if response is None:
            return None
        try:
            data = response.json() if response.content else True
        except Exception:
            return None
        if not root_keys:
            return data
        if not isinstance(data, dict):
            return data
        page_count = 0
        for key in root_keys:
            value = data.get(key, [])
            if isinstance(value, list):
                combined[key].extend(value)
                page_count = max(page_count, len(value))
        pagination = data.get('pagination', {}) or {}
        if 'has_more' in pagination:
            has_more = bool(pagination.get('has_more'))
        else:
            has_more = str(response.headers.get('X-Has-More', 'false')).lower() == 'true'
        if not has_more:
            break
        page_limit = int(pagination.get('limit') or limit)
        offset = int(pagination.get('offset', offset)) + page_limit
        if page_count == 0:
            break
    combined['pagination'] = {'has_more': False, 'offset': 0, 'limit': limit}
    return combined


def _iter_watched_episodes(data):
    seen = set()

    def row_for(show, season_no, episode_no, watched_at):
        tmdb_id = _resolve_tmdb('tvshow', _ids(show))
        try:
            season_no, episode_no = int(season_no), int(episode_no)
        except Exception:
            return None
        key = (tmdb_id, season_no, episode_no)
        if not tmdb_id or key in seen or season_no < 0 or episode_no <= 0:
            return None
        seen.add(key)
        return ('episode', tmdb_id, season_no, episode_no, watched_at or '', show.get('title', ''))

    for item in data.get('episodes', []) or []:
        episode = item.get('episode', item)
        show = item.get('show') or episode.get('show') or {}
        row = row_for(
            show, episode.get('season'), episode.get('number') or episode.get('episode'),
            item.get('last_watched_at') or item.get('watched_at') or episode.get('watched_at')
        )
        if row:
            yield row

    for item in data.get('shows', []) or []:
        show = item.get('show', item)
        for season in item.get('seasons', show.get('seasons', [])) or []:
            season_no = season.get('number') or season.get('season')
            for episode in season.get('episodes', []) or []:
                row = row_for(
                    show, season_no, episode.get('number') or episode.get('episode'),
                    episode.get('watched_at') or season.get('watched_at') or item.get('last_watched_at')
                )
                if row:
                    yield row

    for item in data.get('seasons', []) or []:
        show = item.get('show') or {}
        season = item.get('season', item)
        season_no = season.get('number') or season.get('season')
        for episode in season.get('episodes', []) or []:
            row = row_for(
                show, season_no, episode.get('number') or episode.get('episode'),
                episode.get('watched_at') or season.get('watched_at') or item.get('last_watched_at')
            )
            if row:
                yield row


def _sync_watched():
    data = _paged('sync/watched', root_keys=('movies', 'shows', 'seasons', 'episodes'))
    if data is None:
        return False
    movies = []
    for item in data.get('movies', []) or []:
        movie = item.get('movie', item)
        tmdb_id = _resolve_tmdb('movie', _ids(movie))
        if tmdb_id:
            movies.append((
                'movie', tmdb_id, '', '',
                item.get('last_watched_at') or item.get('watched_at') or movie.get('watched_at') or '',
                movie.get('title', '')
            ))
    mdblist_watched_cache.set_bulk_movie_watched(movies)
    mdblist_watched_cache.set_bulk_tvshow_watched(list(_iter_watched_episodes(data)))
    return True


def _sync_progress():
    data = call_mdblist('sync/playback')
    if data is None:
        return False
    if isinstance(data, dict):
        data = data.get('items') or data.get('playback') or []
    movies, episodes = [], []
    for item in data or []:
        try:
            progress = float(item.get('progress') or 0)
        except Exception:
            progress = 0
        if progress <= 1 or progress >= 99.9:
            continue
        paused_at, resume_id = item.get('paused_at') or '', item.get('id') or 0
        if item.get('type') == 'movie' or item.get('movie'):
            movie = item.get('movie') or item
            tmdb_id = _resolve_tmdb('movie', _ids(movie))
            if tmdb_id:
                movies.append(('movie', tmdb_id, '', '', str(round(progress, 1)), '0', paused_at, resume_id, movie.get('title', '')))
        else:
            episode, show = item.get('episode') or {}, item.get('show') or {}
            if not show and isinstance(episode.get('show'), dict):
                show = episode['show']
            tmdb_id = _resolve_tmdb('tvshow', _ids(show))
            season = episode.get('season')
            number = episode.get('number') or episode.get('episode')
            nested_season = show.get('season') if isinstance(show.get('season'), dict) else {}
            season = season if season is not None else nested_season.get('number')
            nested_episode = nested_season.get('episode') if isinstance(nested_season.get('episode'), dict) else {}
            number = number if number is not None else nested_episode.get('number')
            if tmdb_id and season is not None and number:
                episodes.append(('episode', tmdb_id, int(season), int(number), str(round(progress, 1)), '0', paused_at, resume_id, show.get('title', '')))
    mdblist_watched_cache.set_bulk_movie_progress(movies)
    mdblist_watched_cache.set_bulk_tvshow_progress(episodes)
    return True


def _normalise_catalog_item(item, media_type, date_key):
    key = 'movie' if media_type == 'movie' else 'show'
    media = item.get(key, item)
    ids = _ids(media)
    # Watchlist/list catalogue responses expose the TMDb ID as plain ``id``.
    if not ids.get('tmdb') and media.get('id') and media.get('mediatype') in ('movie', 'show'):
        ids['tmdb'] = media.get('id')
    tmdb_id = _resolve_tmdb(media_type, ids)
    if tmdb_id:
        ids['tmdb'] = tmdb_id
    year = media.get('release_year') or media.get('year')
    released = media.get('released') or media.get('release_date') or media.get('first_aired')
    if not released and year:
        released = '%s-01-01' % year
        if media_type != 'movie':
            released += 'T00:00:00.000Z'
    if not released:
        released = '2050-01-01' if media_type == 'movie' else '2050-01-01T00:00:00.000Z'
    return {
        'media_ids': {'tmdb': ids.get('tmdb', ''), 'imdb': ids.get('imdb', ''), 'tvdb': ids.get('tvdb', '')},
        'title': media.get('title', ''),
        'collected_at': item.get(date_key) or media.get(date_key) or item.get('listed_at') or item.get('watchlist_at') or '',
        'released': released
    }


def mdblist_fetch_collection_watchlist(list_type, media_type):
    media_type = 'movie' if media_type in ('movie', 'movies') else 'tvshow'
    cache_key = 'mdblist_%s_%s' % (list_type, media_type)
    cached = mdblist_cache.get(cache_key)
    if cached is not None:
        return cached
    if list_type == 'watchlist':
        root = 'movies' if media_type == 'movie' else 'shows'
        response = _paged(
            'watchlist/items/%s' % ('movie' if media_type == 'movie' else 'show'),
            root_keys=(root, 'items')
        ) or {}
        raw = response.get(root, []) or response.get('items', [])
        date_key = 'watchlist_at'
    else:
        response = _paged('sync/collection', root_keys=('movies', 'shows', 'seasons', 'episodes')) or {}
        raw = response.get('movies' if media_type == 'movie' else 'shows', [])
        date_key = 'collected_at'
    result = []
    for item in raw:
        normalised = _normalise_catalog_item(item, media_type, date_key)
        if normalised['media_ids'].get('tmdb'):
            result.append(normalised)
    mdblist_cache.set(cache_key, result)
    return result


def mdblist_collection_lists(media_type, list_type=None):
    data = mdblist_fetch_collection_watchlist('collection', media_type)
    return sorted(data, key=lambda x: x.get('collected_at', ''), reverse=True)[:20] if list_type == 'recent' else data


def mdblist_watchlist_lists(media_type, list_type=None):
    data = mdblist_fetch_collection_watchlist('watchlist', media_type)
    return sorted(data, key=lambda x: x.get('collected_at', ''), reverse=True)[:20] if list_type == 'recent' else data


def mdblist_collection(media_type, dummy_arg=None):
    data = mdblist_fetch_collection_watchlist('collection', media_type)
    order = settings.lists_sort_order('collection')
    if order == 0:
        return sort_for_article(data, 'title', settings.ignore_articles())
    if order == 1:
        return sorted(data, key=lambda x: x.get('collected_at', ''), reverse=True)
    return sorted(data, key=lambda x: x.get('released', ''), reverse=True)


def mdblist_watchlist(media_type, dummy_arg=None):
    data = mdblist_fetch_collection_watchlist('watchlist', media_type)
    order = settings.lists_sort_order('watchlist')
    if order == 0:
        return sort_for_article(data, 'title', settings.ignore_articles())
    if order == 1:
        return sorted(data, key=lambda x: x.get('collected_at', ''), reverse=True)
    return sorted(data, key=lambda x: x.get('released', ''), reverse=True)


def mdblist_up_next(params=None):
    """Return MDBList's server-side Up Next rows when a caller needs them."""
    query = dict(params or {})
    query.setdefault('limit', 100)
    data = call_mdblist('upnext', params=query)
    if isinstance(data, dict):
        return data.get('shows', data.get('items', data.get('episodes', []))) or []
    return data or []


def _convert_manager_payload(data, with_ids=True):
    result = {'movies': [], 'shows': []}
    for key in ('movies', 'shows'):
        for item in data.get(key, []) or []:
            ids = item.get('ids', item)
            clean = {k: v for k, v in ids.items() if k in ('tmdb', 'imdb') and v not in (None, '', 'None')}
            if clean:
                result[key].append({'ids': clean} if with_ids else clean)
    return {k: v for k, v in result.items() if v}


def _mutation(path, data, clear=None, refresh=False):
    result = call_mdblist(path, data=data, method='post')
    if clear:
        clear()
    kodi_utils.notification('Success' if result is not None else 'Error', 3000)
    if result is not None and refresh:
        kodi_utils.kodi_refresh()
    return result


def add_to_watchlist(data):
    return _mutation('watchlist/items/add', _convert_manager_payload(data, False), clear=clear_mdblist_collection_watchlist_data)


def remove_from_watchlist(data):
    return _mutation('watchlist/items/remove', _convert_manager_payload(data, False), clear=clear_mdblist_collection_watchlist_data, refresh=True)


def add_to_collection(data):
    return _mutation('sync/collection', _convert_manager_payload(data, True), clear=clear_mdblist_collection_watchlist_data)


def remove_from_collection(data):
    return _mutation('sync/collection/remove', _convert_manager_payload(data, True), clear=clear_mdblist_collection_watchlist_data, refresh=True)


def mdblist_collection_contains(media_type, tmdb_id, season=None, episode=None):
    """Return whether the exact movie/show/season/episode is collected."""
    data = _paged('sync/collection', root_keys=('movies', 'shows', 'seasons', 'episodes')) or {}
    wanted = str(tmdb_id)
    if media_type in ('movie', 'movies'):
        return any(str(_resolve_tmdb('movie', _ids(item.get('movie', item)))) == wanted
                   for item in data.get('movies', []) or [])
    if episode not in (None, '', 'None'):
        for item in data.get('episodes', []) or []:
            ep = item.get('episode', item)
            show = item.get('show') or ep.get('show') or {}
            if (str(_resolve_tmdb('tvshow', _ids(show))) == wanted
                    and int(ep.get('season') or 0) == int(season)
                    and int(ep.get('number') or ep.get('episode') or 0) == int(episode)):
                return True
        # Some responses nest collected episodes under the show/season.
        for item in data.get('shows', []) or []:
            show = item.get('show', item)
            if str(_resolve_tmdb('tvshow', _ids(show))) != wanted:
                continue
            for season_item in item.get('seasons', show.get('seasons', [])) or []:
                if int(season_item.get('number') or season_item.get('season') or 0) != int(season):
                    continue
                if any(int(ep.get('number') or ep.get('episode') or 0) == int(episode)
                       for ep in season_item.get('episodes', []) or []):
                    return True
        return False
    if season not in (None, '', 'None'):
        for item in data.get('seasons', []) or []:
            season_item = item.get('season', item)
            show = item.get('show') or season_item.get('show') or {}
            if (str(_resolve_tmdb('tvshow', _ids(show))) == wanted
                    and int(season_item.get('number') or season_item.get('season') or 0) == int(season)):
                return True
        for item in data.get('shows', []) or []:
            show = item.get('show', item)
            if str(_resolve_tmdb('tvshow', _ids(show))) != wanted:
                continue
            if any(int(i.get('number') or i.get('season') or 0) == int(season)
                   for i in item.get('seasons', show.get('seasons', [])) or []):
                return True
        return False
    return any(str(_resolve_tmdb('tvshow', _ids(item.get('show', item)))) == wanted
               for item in data.get('shows', []) or [])


def mdblist_collection_item(media_type, tmdb_id, remove=False, season=None, episode=None):
    path = 'sync/collection/remove' if remove else 'sync/collection'
    result = call_mdblist(path, data=_sync_payload(media_type, tmdb_id, season, episode), method='post')
    clear_mdblist_collection_watchlist_data()
    kodi_utils.notification('Success' if result is not None else 'Error', 3000)
    return result


def rate_item(media_type, tmdb_id, rating, season=None, episode=None):
    result = call_mdblist('sync/ratings', data=_sync_payload(media_type, tmdb_id, season, episode, rating=rating), method='post')
    mdblist_cache.delete('mdblist_ratings')
    kodi_utils.notification('Rating Saved' if result is not None else 'Error', 3000)
    return result


def remove_rating(media_type, tmdb_id, season=None, episode=None):
    result = call_mdblist('sync/ratings/remove', data=_sync_payload(media_type, tmdb_id, season, episode), method='post')
    mdblist_cache.delete('mdblist_ratings')
    return result


def mdblist_get_lists(list_type='my_lists', page_no='1'):
    if list_type != 'my_lists':
        return []
    cached = mdblist_cache.get('mdblist_lists_my')
    if cached is not None:
        return cached
    raw = call_mdblist('lists/user', params={'unified': 'false'}) or []
    if isinstance(raw, dict):
        raw = raw.get('lists', raw.get('items', []))
    result = []
    for item in raw:
        list_id = item.get('id')
        result.append({
            'name': item.get('name', ''),
            'description': item.get('description', ''),
            'item_count': item.get('items') or item.get('item_count') or 0,
            'privacy': 'private' if item.get('private') else 'public',
            'user': {'ids': {'slug': str(item.get('user_name') or item.get('username') or get_setting('fenlight.mdblist.user', 'me'))}},
            'ids': {'slug': str(list_id), 'mdblist': list_id},
            'mdblist_id': list_id,
            'mediatype': item.get('mediatype') or 'both',
            'dynamic': bool(item.get('dynamic'))
        })
    mdblist_cache.set('mdblist_lists_my', result)
    return result


def get_mdblist_list_contents(list_type, user, list_id, with_auth=True):
    cache_key = 'mdblist_list_contents_%s' % list_id
    cached = mdblist_cache.get(cache_key)
    if cached is not None:
        return cached
    response = _paged(
        'lists/%s/items' % list_id,
        root_keys=('movies', 'shows', 'items')
    ) or {}
    typed_items = []
    typed_items.extend(('movie', item) for item in response.get('movies', []))
    typed_items.extend(('show', item) for item in response.get('shows', []))
    for item in response.get('items', []):
        typed_items.append((None, item))
    result = []
    for order, (declared_type, item) in enumerate(typed_items):
        media_type = declared_type or item.get('mediatype') or item.get('media_type') or item.get('type')
        if media_type in ('tv', 'tvshow'):
            media_type = 'show'
        if media_type not in ('movie', 'show'):
            media_type = 'movie' if item.get('release_date') else 'show'
        media = item.get(media_type, item) if isinstance(item.get(media_type), dict) else item
        ids = _ids(media)
        if not ids.get('tmdb') and media.get('id') and (media.get('mediatype') or media_type) in ('movie', 'show'):
            ids['tmdb'] = media.get('id')
        tmdb_id = _resolve_tmdb('movie' if media_type == 'movie' else 'tvshow', ids)
        if tmdb_id:
            ids['tmdb'] = tmdb_id
        if not ids.get('tmdb'):
            continue
        released = media.get('release_date') or media.get('first_aired')
        if not released and media.get('release_year'):
            released = '%s-01-01' % media['release_year']
        result.append({
            'media_ids': ids,
            'title': media.get('title', ''),
            'type': media_type,
            'order': order,
            'released': released or '2050-01-01',
            'media_type': media_type
        })
    mdblist_cache.set(cache_key, result)
    return result


def get_mdblist_list_selection(included_lists=('personal',)):
    used_lists = []
    if 'default' in included_lists:
        used_lists.extend([
            {'name': 'Movies Collection', 'display': '[B][I]MOVIES COLLECTION[/I][/B]', 'user': 'Collection', 'slug': 'Collection', 'list_type': 'collection', 'media_type': 'movie'},
            {'name': 'TV Show Collection', 'display': '[B][I]TV SHOW COLLECTION[/I][/B]', 'user': 'Collection', 'slug': 'Collection', 'list_type': 'collection', 'media_type': 'show'},
            {'name': 'Movies Watchlist', 'display': '[B][I]MOVIES WATCHLIST[/I][/B]', 'user': 'Watchlist', 'slug': 'Watchlist', 'list_type': 'watchlist', 'media_type': 'movie'},
            {'name': 'TV Show Watchlist', 'display': '[B][I]TV SHOW WATCHLIST[/I][/B]', 'user': 'Watchlist', 'slug': 'Watchlist', 'list_type': 'watchlist', 'media_type': 'show'}
        ])
    if 'personal' in included_lists:
        for item in mdblist_get_lists('my_lists'):
            if item.get('dynamic'):
                continue
            used_lists.append({
                'name': item['name'],
                'display': '[B]PERSONAL:[/B] [I]%s[/I]' % item['name'].upper(),
                'user': item['user']['ids']['slug'],
                'slug': item['ids']['slug'],
                'list_type': 'my_lists',
                'item_count': item.get('item_count', 0),
                'media_type': item.get('mediatype', 'both')
            })
    if not used_lists:
        return kodi_utils.notification('No writable MDBList lists found', 3000)
    list_items = [{'line1': '%s%s' % (i['display'], ' [I](x%02d)[/I]' % i['item_count'] if 'item_count' in i else '')} for i in used_lists]
    return kodi_utils.select_dialog(used_lists, items=json.dumps(list_items), heading='Select MDBList')


def add_to_list(user, list_id, data):
    return _mutation('lists/%s/items/add' % list_id, _convert_manager_payload(data, False), clear=clear_mdblist_list_data)


def remove_from_list(user, list_id, data):
    return _mutation('lists/%s/items/remove' % list_id, _convert_manager_payload(data, False), clear=clear_mdblist_list_data, refresh=True)


def make_new_mdblist_list(params=None):
    title = kodi_utils.kodi_dialog().input('New MDBList List Name')
    if not title:
        return
    result = call_mdblist('lists/user/add', data={'name': title, 'private': True}, method='post')
    clear_mdblist_list_data()
    kodi_utils.notification('Success' if result is not None else 'Error', 3000)
    kodi_utils.kodi_refresh()
    return result


def delete_mdblist_list(params):
    list_id = params.get('list_id') or params.get('list_slug') or params.get('slug')
    if not list_id or not kodi_utils.confirm_dialog():
        return
    result = call_mdblist('lists/%s' % list_id, method='delete')
    clear_mdblist_list_data()
    kodi_utils.kodi_refresh()
    return result


def mdblist_sync_activities(force_update=False):
    if not mdblist_user_active():
        return 'no account'
    try:
        latest = call_mdblist('sync/last_activities')
        if latest is None:
            return 'failed'
        cached = mdblist_cache.get('mdblist_last_activities') or {}
        if not force_update and latest == cached:
            return 'not needed'
        watched_changed = force_update or any(latest.get(k) != cached.get(k) for k in ('watched_at', 'season_watched_at', 'episode_watched_at'))
        progress_changed = force_update or any(latest.get(k) != cached.get(k) for k in ('paused_at', 'episode_paused_at'))
        if watched_changed and not _sync_watched():
            return 'failed'
        if progress_changed and not _sync_progress():
            return 'failed'
        if force_update or latest.get('dropped_at') != cached.get('dropped_at'):
            mdblist_cache.delete('mdblist_hidden_dropped')
            mdblist_get_hidden_items('dropped')
        if force_update or latest.get('watchlisted_at') != cached.get('watchlisted_at'):
            mdblist_cache.delete_like('mdblist_watchlist_%')
        if force_update or latest.get('collected_at') != cached.get('collected_at'):
            mdblist_cache.delete_like('mdblist_collection_%')
        if force_update or latest.get('rated_at') != cached.get('rated_at'):
            ratings = _paged('sync/ratings', root_keys=('movies', 'shows', 'seasons', 'episodes'))
            if ratings is not None:
                mdblist_cache.set('mdblist_ratings', ratings)
        if force_update or latest.get('list_updated_at') != cached.get('list_updated_at'):
            clear_mdblist_list_data()
        mdblist_cache.set('mdblist_last_activities', latest)
        set_setting('mdblist.last_sync', str(int(time.time())))
        return 'success'
    except Exception as exc:
        kodi_utils.logger('MDBList Sync Error', str(exc))
        return 'failed'
