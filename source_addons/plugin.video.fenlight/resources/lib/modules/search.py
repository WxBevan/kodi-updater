# -*- coding: utf-8 -*-
import json
from urllib.parse import unquote
from caches.main_cache import main_cache
from indexers.people import person_search
from indexers.easynews import search_easynews_image
from modules.kodi_utils import close_all_dialog, external, build_url, kodi_dialog, execute_builtin, select_dialog, notification, kodi_refresh
# from modules.kodi_utils import logger

def get_key_id(params):
	close_all_dialog()
	params_key_id = params.get('key_id', None)
	key_id = params_key_id or kodi_dialog().input('')
	if not key_id: return
	key_id = unquote(key_id)
	media_type = params.get('media_type', '')
	search_type = params.get('search_type', 'media_title')
	string = None
	if search_type == 'media_title':
		if media_type == 'movie': url_params, string = {'mode': 'build_movie_list', 'action': 'tmdb_movies_search'}, 'movie_queries'
		elif media_type == 'anime': url_params, string = {'mode': 'build_tvshow_list', 'action': 'trakt_anime_search', 'is_anime_list': 'true'}, 'anime_queries'
		elif media_type == 'tvshow_anime': url_params, string = {'mode': 'build_tvshow_list', 'action': 'tmdb_tv_search'}, 'tvshow_anime_queries'
		else: url_params, string = {'mode': 'build_tvshow_list', 'action': 'trakt_tv_search'}, 'tvshow_queries'
	elif search_type == 'people': string = 'people_queries'
	elif search_type == 'tmdb_keyword':
		url_params, string = {'mode': 'navigator.keyword_results', 'media_type': media_type}, 'keyword_tmdb_%s_queries' % media_type
	elif search_type == 'easynews_video':
		url_params, string = {'mode': 'easynews.search_easynews'}, 'easynews_video_queries'
	elif search_type == 'easynews_image':
		url_params, string = {'mode': 'easynews.search_easynews_image'}, 'easynews_image_queries'
	elif search_type == 'trakt_lists':
		url_params, string = {'mode': 'trakt.list.search_trakt_lists'}, 'trakt_list_queries'
	if string: add_to_search(key_id, string)
	if search_type == 'people': return person_search(key_id)
	if search_type == 'easynews_image': return search_easynews_image(key_id)
	url_params.update({'query': key_id, 'key_id': key_id, 'name': 'Search Results for %s' % key_id})
	return execute_builtin('ActivateWindow(Videos,%s,return)' if external() else 'Container.Update(%s)' % build_url(url_params))

def add_to_search(search_name, search_list):
	try:
		result = []
		cache = main_cache.get(search_list)
		if cache: result = cache
		if search_name in result: result.remove(search_name)
		result.insert(0, search_name)
		result = result[:50]
		main_cache.set(search_list, result, expiration=8760)
	except: return

def remove_from_search(params):
	try:
		result = main_cache.get(params['setting_id'])
		result.remove(params.get('key_id'))
		main_cache.set(params['setting_id'], result, expiration=8760)
		notification('Success', 2500)
		kodi_refresh()
	except: return

def clear_search():
	clear_history_list = [('Clear Movie Search History', 'movie_queries'),
	('Clear TV Show Search History', 'tvshow_queries'),
	('Clear Anime Search History', 'anime_queries'),
	('Clear TV Show & Anime Search History', 'tvshow_anime_queries'),
	('Clear People Search History', 'people_queries'),
	('Clear Keywords Movie Search History', 'keyword_tmdb_movie_queries'),
	('Clear Keywords TV Show Search History', 'keyword_tmdb_tvshow_queries'),
	('Clear Easynews Search History', 'easynews_video_queries'),
	('Clear Easynews Search History', 'easynews_image_queries'),
	('Clear Trakt List Search History', 'trakt_list_queries')]
	try:
		list_items = [{'line1': item[0]} for item in clear_history_list]
		kwargs = {'items': json.dumps(list_items), 'narrow_window': 'true'}
		setting_id = select_dialog([item[1] for item in clear_history_list], **kwargs)
		if setting_id == None: return
		clear_all(setting_id)
	except: return

def clear_all(setting_id, refresh='false'):
	main_cache.set(setting_id, '', expiration=365)
	notification('Success', 2500)
	if refresh == 'true': kodi_refresh()

def bingie_all(params):
    from urllib.parse import unquote
    from apis.tmdb_api import tmdb_movies_search, tmdb_tv_search, tmdb_people_info
    from indexers.movies import Movies
    from indexers.tvshows import TVShows
    from modules import kodi_utils

    def _safe_results(function, query, page_no=1):
        try:
            return function(query, page_no).get('results', [])
        except Exception:
            return []

    def _people_items(query, limit=20):
        try:
            people = tmdb_people_info(query).get('results', [])
            people = sorted(people, key=lambda k: k.get('popularity', 0.0), reverse=True)[:limit]
        except Exception:
            people = []

        icon = kodi_utils.get_icon('empty_person')
        fanart = kodi_utils.addon_fanart()

        for item in people:
            try:
                actor_id = int(item['id'])
                actor_name = item['name']
                profile_path = item.get('profile_path')
                actor_image = 'https://image.tmdb.org/t/p/h632%s' % profile_path if profile_path else icon

                known_titles = []
                for known_item in item.get('known_for', []):
                    title = known_item.get('title') or known_item.get('name')
                    if title:
                        known_titles.append(title)

                known_for = '[B]Known for:[/B]\n%s' % '\n'.join(known_titles) if known_titles else ' '

                url = kodi_utils.build_url({
                    'mode': 'person_data_dialog',
                    'actor_name': actor_name,
                    'actor_image': actor_image,
                    'actor_id': actor_id
                })

                listitem = kodi_utils.make_listitem()
                listitem.setLabel(actor_name)
                listitem.setArt({
                    'icon': actor_image,
                    'poster': actor_image,
                    'thumb': actor_image,
                    'fanart': fanart,
                    'banner': actor_image
                })

                info_tag = listitem.getVideoInfoTag(True)
                info_tag.setMediaType('video')
                info_tag.setTitle(actor_name)
                info_tag.setPlot(known_for)

                yield (url, listitem, False)
            except Exception:
                pass

    query = unquote(params.get('query') or params.get('key_id') or '').strip()
    handle = int(kodi_utils.sys.argv[1])

    if not query:
        kodi_utils.set_content(handle, 'movies')
        kodi_utils.set_category(handle, 'Search')
        return kodi_utils.end_directory(handle, cacheToDisc=False)

    items = []
    limit_each = int(params.get('limit_each', '20'))

    # Movies, built by FLAM so movie context menu/playback is FLAM.
    movie_results = _safe_results(tmdb_movies_search, query)[:limit_each]
    movie_ids = [i.get('id') for i in movie_results if i.get('id')]
    if movie_ids:
        movie_indexer = Movies({
            'mode': 'build_movie_list',
            'action': 'tmdb_movies_search',
            'query': query,
            'name': 'Movies'
        })
        movie_indexer.list = movie_ids
        movie_indexer.new_page = {}
        items.extend(movie_indexer.worker())

    # TV shows, built by FLAM so TV context menu/browse/playback is FLAM.
    tv_results = _safe_results(tmdb_tv_search, query)[:limit_each]
    tv_ids = [i.get('id') for i in tv_results if i.get('id')]
    if tv_ids:
        tv_indexer = TVShows({
            'mode': 'build_tvshow_list',
            'action': 'tmdb_tv_search',
            'query': query,
            'name': 'TV Shows'
        })
        tv_indexer.list = tv_ids
        tv_indexer.new_page = {}
        items.extend(tv_indexer.worker())

    # People, still opened through FLAM's people dialog.
    items.extend(list(_people_items(query, limit_each)))

    kodi_utils.add_items(handle, items)
    kodi_utils.set_content(handle, 'movies')
    kodi_utils.set_category(handle, 'Search: %s' % query)
    kodi_utils.end_directory(handle, cacheToDisc=False)