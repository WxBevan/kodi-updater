# -*- coding: utf-8 -*-
"""Provider facade for FLAM personal tracking.

Provider setting values:
    0 = MDBList (default for new installs)
    1 = Trakt
    2 = Local only

Public Trakt discovery data (trending, public lists, comments and calendars) is
left in trakt_api.py.  This facade only owns personal watched/progress/list
state so changing provider cannot break those discovery screens.
"""
from caches.settings_cache import get_setting, set_setting
from modules import settings, kodi_utils


def provider_id():
    try: return int(get_setting('fenlight.tracking.provider', '0'))
    except Exception: return 0


def provider_name():
    return {0: 'MDBList', 1: 'Trakt', 2: 'Local only'}.get(provider_id(), 'MDBList')


def _clean(value):
    return '' if value in (None, '', '0', 'empty_setting') else str(value)


def refresh_account_properties(params=None):
    """Publish account state for the custom Accounts and Meta Accounts windows."""
    from apis.mdblist_api import (
        mdblist_user_active, mdblist_auth_method,
        mdblist_has_oauth, mdblist_has_api_key
    )
    pid = provider_id()
    mdblist_client_id = settings.mdblist_client()
    mdblist_user = _clean(get_setting('fenlight.mdblist.user', ''))
    trakt_user = _clean(get_setting('fenlight.trakt.user', ''))
    mdblist_connected = mdblist_user_active()
    trakt_connected = settings.trakt_user_active()
    mdblist_status = 'Connected as %s' % mdblist_user if mdblist_connected else 'Not connected'
    trakt_status = 'Connected as %s' % trakt_user if trakt_connected else 'Not connected'
    selected_status = (
        mdblist_status if pid == 0 else
        trakt_status if pid == 1 else
        'Local database • No account required'
    )
    selected_summary = '%s • %s' % (provider_name(), selected_status)
    properties = {
        'fenlight.tracking.provider': str(pid),
        'fenlight.tracking.provider_name': provider_name(),
        'fenlight.tracking.provider_status': selected_status,
        'fenlight.tracking.provider_summary': selected_summary,
        'fenlight.mdblist.connected': 'true' if mdblist_connected else 'false',
        'fenlight.mdblist.status': mdblist_status,
        'fenlight.mdblist.auth_method': mdblist_auth_method(),
        'fenlight.mdblist.has_oauth': 'true' if mdblist_has_oauth() else 'false',
        'fenlight.mdblist.has_apikey': 'true' if mdblist_has_api_key() else 'false',
        'fenlight.mdblist.client_id_status': 'Configured' if _clean(mdblist_client_id) else 'Not configured',
        'fenlight.trakt.connected': 'true' if trakt_connected else 'false',
        'fenlight.trakt.status': trakt_status,
        'fenlight.local.status': 'Ready • Stored only on this Kodi device'
    }
    for key, value in properties.items():
        kodi_utils.set_property(key, value)
    return properties


def sync_helper_settings():
    """Keep Bingie Helper and skin-visible properties aligned with FLAM."""
    value = str(provider_id())
    def setting_value(setting_id, default=''):
        result = get_setting('fenlight.%s' % setting_id, default)
        return '' if result in (None, 'empty_setting') else str(result)
    try:
        helper = kodi_utils.addon('plugin.video.tmdb.bingie.helper')
        helper.setSetting('tracking_provider', value)
        helper.setSetting('mdblist_apikey', setting_value('mdblist.apikey'))
        helper.setSetting('mdblist_access_token', setting_value('mdblist.access_token'))
        helper.setSetting('mdblist_refresh_token', setting_value('mdblist.refresh_token'))
        helper.setSetting('mdblist_token_expires', setting_value('mdblist.expires', '0') or '0')
        helper.setSetting('mdblist_client_id', settings.mdblist_client())
    except Exception:
        pass
    refresh_account_properties()
    return value


def provider_active(notify=False):
    pid = provider_id()
    active = True
    if pid == 0:
        from apis.mdblist_api import mdblist_user_active
        active = mdblist_user_active()
    elif pid == 1:
        active = settings.trakt_user_active()
    if notify and not active:
        kodi_utils.notification('%s is selected but not connected' % provider_name(), 3500)
    return active


def select_provider(params=None):
    labels = ['MDBList (default)', 'Trakt', 'Local only']
    selection = kodi_utils.select_dialog(
        list(range(3)),
        items=__import__('json').dumps([{'line1': label} for label in labels]),
        heading='Tracking Provider'
    )
    if selection is None:
        return None
    return set_provider({'provider': str(selection)})


def set_provider(params):
    value = str(params.get('provider', params.get('value', '0')))
    if value not in ('0', '1', '2'):
        value = '0'
    # Switching providers never removes credentials for either remote account.
    set_setting('tracking.provider', value)
    set_setting('watched_indicators', '1' if value == '1' else '0')
    sync_helper_settings()
    if provider_active(False):
        tracking_sync_activities(force_update=True)
        kodi_utils.notification('%s Selected' % provider_name(), 2500)
    else:
        kodi_utils.notification('%s Selected • Connect it in Accounts' % provider_name(), 4000)
    kodi_utils.kodi_refresh()
    return True

def build_watchlist(params=None):
    """Build one mixed movie/TV watchlist for Bingie widgets."""
    from indexers.trakt_lists import build_trakt_list
    values = dict(params or {})
    values.update({
        'list_type': 'tracking_watchlist',
        'list_name': '%s Watchlist' % provider_name(),
        'user': '',
        'slug': ''
    })
    return build_trakt_list(values)


def force_sync(params=None):
    status = tracking_sync_activities(force_update=True)
    if status == 'success':
        kodi_utils.set_property('bingie.widgets.tracking.changed', str(__import__('time').time()))
        kodi_utils.notification('%s Sync Complete' % provider_name(), 3000)
    elif status == 'local':
        kodi_utils.notification('Local-only tracking does not require sync', 3000)
    elif status == 'no account':
        kodi_utils.notification('Authorize %s first' % provider_name(), 3500)
    else:
        kodi_utils.notification('%s Sync Failed' % provider_name(), 3500)
    kodi_utils.kodi_refresh()
    return status


def tracking_sync_activities(force_update=False):
    pid = provider_id()
    if pid == 0:
        from apis.mdblist_api import mdblist_sync_activities
        return mdblist_sync_activities(force_update=force_update)
    if pid == 1:
        from apis.trakt_api import trakt_sync_activities
        return trakt_sync_activities(force_update=force_update)
    return 'local'


def tracking_watched_status_mark(action, media, media_id, tvdb_id=0, season=None, episode=None):
    if provider_id() == 0:
        from apis.mdblist_api import mdblist_watched_status_mark
        return mdblist_watched_status_mark(action, media, media_id, tvdb_id, season, episode)
    if provider_id() == 1:
        from apis.trakt_api import trakt_watched_status_mark
        return trakt_watched_status_mark(action, media, media_id, tvdb_id, season, episode)
    return True


def tracking_official_status(media_type):
    if provider_id() != 1: return True
    from apis.trakt_api import trakt_official_status
    return trakt_official_status(media_type)


def tracking_progress(action, media, media_id, percent, season=None, episode=None, resume_id=None, refresh=False):
    if provider_id() == 0:
        from apis.mdblist_api import mdblist_progress
        return mdblist_progress(action, media, media_id, percent, season, episode, resume_id, refresh)
    if provider_id() == 1:
        from apis.trakt_api import trakt_progress
        return trakt_progress(action, media, media_id, percent, season, episode, resume_id, refresh)
    return True


def tracking_scrobble(method, media, media_id, percent, season=None, episode=None):
    if provider_id() == 0:
        from apis.mdblist_api import mdblist_scrobble
        # FLAM's established watched threshold is 90%. MDBList marks a stopped
        # scrobble watched at 80%, so retain the FLAM threshold by pausing any
        # playback that ended between 5% and 89.9% instead of stopping it.
        try:
            if method == 'stop' and float(percent or 0) < 90.0:
                method = 'pause'
        except Exception:
            pass
        return mdblist_scrobble(method, media, media_id, percent, season, episode)
    # FLAM historically lets script.trakt or its final watched/progress write own
    # Trakt scrobbling. Preserve that behaviour to avoid duplicate Trakt plays.
    return True


def tracking_get_hidden_items(list_type='dropped'):
    if provider_id() == 0:
        from apis.mdblist_api import mdblist_get_hidden_items
        return mdblist_get_hidden_items(list_type)
    if provider_id() == 1:
        from apis.trakt_api import trakt_get_hidden_items
        return trakt_get_hidden_items(list_type)
    return []


def tracking_hide_unhide_progress_items(params):
    if provider_id() == 0:
        from apis.mdblist_api import hide_unhide_progress_items
        return hide_unhide_progress_items(params)
    if provider_id() == 1:
        from apis.trakt_api import hide_unhide_progress_items
        return hide_unhide_progress_items(params)
    from modules.watched_status import hide_unhide_progress_items
    return hide_unhide_progress_items(params)


def clear_tracking_collection_watchlist_data(list_type, media_type):
    if provider_id() == 0:
        from caches.mdblist_cache import clear_mdblist_collection_watchlist_data
        return clear_mdblist_collection_watchlist_data(list_type, media_type)
    if provider_id() == 1:
        from caches.trakt_cache import clear_trakt_collection_watchlist_data
        return clear_trakt_collection_watchlist_data(list_type, media_type)


def tracking_collection(media_type, dummy_arg=None):
    if provider_id() == 0:
        from apis.mdblist_api import mdblist_collection
        return mdblist_collection(media_type, dummy_arg)
    if provider_id() == 1:
        from apis.trakt_api import trakt_collection
        return trakt_collection(media_type, dummy_arg)
    return []


def tracking_watchlist(media_type, dummy_arg=None):
    if provider_id() == 0:
        from apis.mdblist_api import mdblist_watchlist
        return mdblist_watchlist(media_type, dummy_arg)
    if provider_id() == 1:
        from apis.trakt_api import trakt_watchlist
        return trakt_watchlist(media_type, dummy_arg)
    return []


def tracking_collection_lists(media_type, list_type=None):
    if provider_id() == 0:
        from apis.mdblist_api import mdblist_collection_lists
        return mdblist_collection_lists(media_type, list_type)
    if provider_id() == 1:
        from apis.trakt_api import trakt_collection_lists
        return trakt_collection_lists(media_type, list_type)
    return []


def tracking_watchlist_lists(media_type, list_type=None):
    if provider_id() == 0:
        from apis.mdblist_api import mdblist_watchlist_lists
        return mdblist_watchlist_lists(media_type, list_type)
    if provider_id() == 1:
        from apis.trakt_api import trakt_watchlist_lists
        return trakt_watchlist_lists(media_type, list_type)
    return []


def add_to_watchlist(data):
    if not provider_active(True): return None
    if provider_id() == 0:
        from apis.mdblist_api import add_to_watchlist as function
    else:
        from apis.trakt_api import add_to_watchlist as function
    return function(data)


def remove_from_watchlist(data):
    if not provider_active(True): return None
    if provider_id() == 0:
        from apis.mdblist_api import remove_from_watchlist as function
    else:
        from apis.trakt_api import remove_from_watchlist as function
    return function(data)


def add_to_collection(data):
    if not provider_active(True): return None
    if provider_id() == 0:
        from apis.mdblist_api import add_to_collection as function
    else:
        from apis.trakt_api import add_to_collection as function
    return function(data)


def remove_from_collection(data):
    if not provider_active(True): return None
    if provider_id() == 0:
        from apis.mdblist_api import remove_from_collection as function
    else:
        from apis.trakt_api import remove_from_collection as function
    return function(data)


def get_tracking_list_selection(included_lists=('default', 'personal')):
    if not provider_active(True): return None
    if provider_id() == 0:
        from apis.mdblist_api import get_mdblist_list_selection
        return get_mdblist_list_selection(included_lists)
    if provider_id() == 1:
        from apis.trakt_api import get_trakt_list_selection
        return get_trakt_list_selection(included_lists)
    return None


def add_to_list(user, slug, data):
    if provider_id() == 0:
        from apis.mdblist_api import add_to_list as function
    elif provider_id() == 1:
        from apis.trakt_api import add_to_list as function
    else: return None
    return function(user, slug, data)


def remove_from_list(user, slug, data):
    if provider_id() == 0:
        from apis.mdblist_api import remove_from_list as function
    elif provider_id() == 1:
        from apis.trakt_api import remove_from_list as function
    else: return None
    return function(user, slug, data)


def tracking_get_lists(list_type='my_lists', page_no='1'):
    if provider_id() == 0:
        from apis.mdblist_api import mdblist_get_lists
        return mdblist_get_lists(list_type, page_no)
    if provider_id() == 1:
        from apis.trakt_api import trakt_get_lists
        return trakt_get_lists(list_type, page_no)
    return []


def get_tracking_list_contents(list_type, user, slug, with_auth=True):
    if provider_id() == 0:
        from apis.mdblist_api import get_mdblist_list_contents
        return get_mdblist_list_contents(list_type, user, slug, with_auth)
    if provider_id() == 1:
        from apis.trakt_api import get_trakt_list_contents
        return get_trakt_list_contents(list_type, user, slug, with_auth)
    return []


def make_new_tracking_list(params=None):
    if provider_id() == 0:
        from apis.mdblist_api import make_new_mdblist_list
        return make_new_mdblist_list(params)
    if provider_id() == 1:
        from apis.trakt_api import make_new_trakt_list
        return make_new_trakt_list(params)


def delete_tracking_list(params):
    if provider_id() == 0:
        from apis.mdblist_api import delete_mdblist_list
        return delete_mdblist_list(params)
    if provider_id() == 1:
        from apis.trakt_api import delete_trakt_list
        return delete_trakt_list(params)


def rate_item(params):
    if provider_id() != 0:
        return kodi_utils.notification('Ratings through this manager are currently MDBList-only', 3500)
    from apis.mdblist_api import rate_item as function
    rating = params.get('rating')
    if rating is None:
        rating = kodi_utils.select_dialog(list(range(0, 11)), items=__import__('json').dumps([{'line1': 'Remove Rating' if i == 0 else '%d / 10' % i} for i in range(0, 11)]), heading='Rate on MDBList')
    if rating is None: return
    rating = int(rating)
    if rating == 0:
        from apis.mdblist_api import remove_rating
        return remove_rating(params['media_type'], params['tmdb_id'], params.get('season'), params.get('episode'))
    return function(params['media_type'], params['tmdb_id'], rating, params.get('season'), params.get('episode'))



def helper_sync(params):
    """Handle TMDb Bingie Helper/skin actions through the selected provider."""
    from modules import watched_status
    pid = provider_id()
    sync_type = params.get('sync_type') or 'menu'
    tmdb_type = params.get('tmdb_type', 'movie')
    media_type = 'movie' if tmdb_type == 'movie' else 'tvshow'
    tmdb_id = params.get('tmdb_id')
    season = params.get('season')
    episode = params.get('episode')
    try: tmdb_id_int = int(tmdb_id)
    except Exception: return kodi_utils.notification('Missing TMDb ID', 3000)

    if sync_type in ('watchlist', 'favorites'):
        if pid == 2: return kodi_utils.notification('Local-only tracking has no watchlist', 3000)
        current = tracking_watchlist(media_type, '')
        present = any(str(i.get('media_ids', {}).get('tmdb')) == str(tmdb_id_int) for i in current)
        data = {'movies' if media_type == 'movie' else 'shows': [{'ids': {'tmdb': tmdb_id_int}}]}
        result = remove_from_watchlist(data) if present else add_to_watchlist(data)
    elif sync_type == 'collection':
        if pid == 2: return kodi_utils.notification('Local-only tracking has no collection', 3000)
        current = tracking_collection(media_type, '')
        present = any(str(i.get('media_ids', {}).get('tmdb')) == str(tmdb_id_int) for i in current)
        if pid == 0 and season not in (None, '', 'None'):
            from apis.mdblist_api import mdblist_collection_contains, mdblist_collection_item
            item_type = 'episode' if episode not in (None, '', 'None') else 'season'
            present = mdblist_collection_contains(item_type, tmdb_id_int, season, episode)
            result = mdblist_collection_item(item_type, tmdb_id_int, present, season, episode)
        else:
            data = {'movies' if media_type == 'movie' else 'shows': [{'ids': {'tmdb': tmdb_id_int}}]}
            result = remove_from_collection(data) if present else add_to_collection(data)
    elif sync_type in ('watched', 'unwatched'):
        action = 'mark_as_watched' if sync_type == 'watched' else 'mark_as_unwatched'
        values = {'action': action, 'tmdb_id': str(tmdb_id_int), 'title': params.get('title', ''), 'refresh': 'false'}
        if media_type == 'movie': result = watched_status.mark_movie(values)
        elif season not in (None, '', 'None') and episode not in (None, '', 'None'):
            values.update({'season': season, 'episode': episode, 'tvdb_id': params.get('tvdb_id', '0')})
            result = watched_status.mark_episode(values)
        else:
            values['tvdb_id'] = params.get('tvdb_id', '0')
            result = watched_status.mark_tvshow(values)
    elif sync_type == 'progress':
        result = watched_status.erase_bookmark('episode' if episode not in (None, '', 'None') else media_type, str(tmdb_id_int), season or '', episode or '', 'false')
    elif sync_type == 'dropped':
        if media_type == 'movie': return kodi_utils.notification('Only shows can be dropped', 3000)
        hidden = tracking_get_hidden_items('dropped')
        action = 'undrop' if tmdb_id_int in [int(i) for i in hidden] else 'drop'
        result = tracking_hide_unhide_progress_items({'action': action, 'media_type': 'shows', 'media_id': str(tmdb_id_int), 'section': 'dropped', 'refresh': 'false'})
    elif sync_type in ('rating', 'like', 'dislike', 'reset'):
        if pid != 0: return kodi_utils.notification('Use Trakt rating controls while Trakt is selected', 3000)
        rating = params.get('rating')
        result = rate_item({'media_type': 'episode' if episode not in (None, '', 'None') else media_type,
                            'tmdb_id': str(tmdb_id_int), 'season': season, 'episode': episode, 'rating': rating})
    elif sync_type in ('userlist', 'mdblistuser'):
        if pid == 2: return kodi_utils.notification('Local-only tracking has no remote lists', 3000)
        selected = get_tracking_list_selection(['personal'])
        if selected is None: return
        data = {'movies' if media_type == 'movie' else 'shows': [{'ids': {'tmdb': tmdb_id_int}}]}
        result = add_to_list(selected['user'], selected['slug'], data)
    else:
        from indexers.dialogs import trakt_manager_choice
        result = trakt_manager_choice({'tmdb_id': str(tmdb_id_int), 'imdb_id': params.get('imdb_id', 'None'),
                                       'tvdb_id': params.get('tvdb_id', 'None'), 'media_type': media_type})
    kodi_utils.set_property('bingie.widgets.tracking.changed', str(__import__('time').time()))
    kodi_utils.kodi_refresh()
    return result


# Name intentionally mirrors the original Trakt API helper. Existing list
# builders can switch imports without changing their returned data contract.
def tracking_fetch_collection_watchlist(list_type, media_type):
    if provider_id() == 0:
        from apis.mdblist_api import mdblist_fetch_collection_watchlist
        return mdblist_fetch_collection_watchlist(list_type, media_type)
    if provider_id() == 1:
        from apis.trakt_api import trakt_fetch_collection_watchlist
        return trakt_fetch_collection_watchlist(list_type, media_type)
    return []
