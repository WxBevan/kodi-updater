# -*- coding: utf-8 -*-
from caches.settings_cache import get_setting, set_setting
from modules import kodi_utils as k

_EMPTY = ('empty_setting', '', None)

XTREAM_SETTINGS = {
    'xtream.server': 'Xtream Server URL',
    'xtream.username': 'Xtream Username',
    'xtream.password': 'Xtream Password'
}

RESOLUTION_CHOICES = [
    ('4K', 'SD, 720p, 1080p, 4K'),
    ('1080p', 'SD, 720p, 1080p'),
    ('720p', 'SD, 720p'),
    ('SD', 'SD')
]




def _clean(value):
    return '' if value in _EMPTY else str(value)


def _is_set(value):
    return value not in _EMPTY


def _masked_status(setting_id):
    return 'Configured' if _is_set(get_setting('fenlight.%s' % setting_id, 'empty_setting')) else 'Not Set'



#==================== General Settings Actions ====================#


def set_max_resolution(params=None):
    import json

    list_items = [{'line1': item[0]} for item in RESOLUTION_CHOICES]
    choice = k.select_dialog(
        RESOLUTION_CHOICES,
        items=json.dumps(list_items),
        narrow_window='true',
        heading='Max Resolution'
    )

    if choice is None:
        return

    label, quality_string = choice

    # Source select quality limits.
    set_setting('results_quality_movie', quality_string)
    set_setting('results_quality_episode', quality_string)

    # Autoplay quality limits.
    set_setting('autoplay_quality_movie', quality_string)
    set_setting('autoplay_quality_episode', quality_string)

    # Simple display value for the streamlined Accounts window.
    set_setting('simple.max_resolution', label)

    k.notification('Max resolution set to %s' % label, 3000)


def toggle_autoplay(params=None):
    movie_enabled = get_setting('fenlight.auto_play_movie', 'false') == 'true'
    episode_enabled = get_setting('fenlight.auto_play_episode', 'false') == 'true'

    # If both are already enabled, turn both off. Otherwise turn both on.
    new_value = 'false' if movie_enabled and episode_enabled else 'true'

    set_setting('auto_play_movie', new_value)
    set_setting('auto_play_episode', new_value)

    k.notification('Autoplay %s' % ('enabled' if new_value == 'true' else 'disabled'), 3000)


def show_tutorial(params=None):
    tutorial_file = k.translate_path('special://home/addons/plugin.video.fenlight/resources/text/accounts_tutorial.txt')

    try:
        return k.show_text('FLAM Tutorial', file=tutorial_file, font_size='large')
    except Exception:
        return k.show_text(
            'FLAM Tutorial',
            text='Welcome to FLAM.[CR][CR]Edit resources/text/accounts_tutorial.txt to change this tutorial text.',
            font_size='large'
        )
    

#==================== Xtream IPTV ====================#




def set_xtream_setting(params):
    import time

    setting_id = params.get('setting_id')
    if setting_id not in XTREAM_SETTINGS:
        return k.ok_dialog(text='Invalid Xtream setting requested.')

    last_finished = k.get_property('fenlight.xtream_input_last_finished')
    try:
        if last_finished and time.time() - float(last_finished) < 1.5:
            return
    except Exception:
        pass

    heading = XTREAM_SETTINGS[setting_id]
    current_value = _clean(get_setting('fenlight.%s' % setting_id, 'empty_setting'))

    new_value = k.kodi_dialog().input(heading, defaultt=current_value)

    if new_value == '':
        if not k.confirm_dialog(text='Save this value as blank?', ok_label='Yes', cancel_label='No', default_control=11):
            k.set_property('fenlight.xtream_input_last_finished', str(time.time()))
            return
        new_value = 'empty_setting'

    set_setting(setting_id, new_value or 'empty_setting')
    k.set_property('fenlight.xtream_input_last_finished', str(time.time()))
    k.notification('%s saved' % heading, 3000)




def xtream_status(params=None):
    server = _masked_status('xtream.server')
    username = _masked_status('xtream.username')
    password = _masked_status('xtream.password')
    text = '[B]Xtream Server:[/B] %s[CR][B]Username:[/B] %s[CR][B]Password:[/B] %s' % (server, username, password)
    return k.ok_dialog(heading='Xtream IPTV', text=text)

def _progress_dialog():
    try:
        import xbmcgui
        dialog = xbmcgui.DialogProgress()
        dialog.create('Generate IPTV', 'Preparing IPTV generator...')
        return dialog
    except Exception:
        return None


def _progress_update(dialog, percent, message):
    if dialog is None:
        return

    try:
        dialog.update(percent, message)
    except Exception:
        pass


def _progress_close(dialog):
    if dialog is None:
        return

    try:
        dialog.close()
    except Exception:
        pass

def generate_iptv(params=None):
    server = _clean(get_setting('fenlight.xtream.server', 'empty_setting'))
    username = _clean(get_setting('fenlight.xtream.username', 'empty_setting'))
    password = _clean(get_setting('fenlight.xtream.password', 'empty_setting'))

    missing = []
    if not server: missing.append('Server URL')
    if not username: missing.append('Username')
    if not password: missing.append('Password')
    if missing:
        return k.ok_dialog(
            heading='Generate IPTV',
            text='Missing required Xtream details:[CR][CR]%s' % '[CR]'.join(missing)
        )

    if k.get_property('fenlight.iptv_generation_running') == 'true':
        return k.ok_dialog(
            heading='Generate IPTV',
            text='IPTV generation is already running.[CR][CR]Please wait for it to finish.'
        )

    progress = None
    final_heading = 'Generate IPTV'
    final_text = ''

    try:
        k.set_property('fenlight.iptv_generation_running', 'true')

        progress = _progress_dialog()
        _progress_update(progress, 5, 'Checking Xtream details...')
        _progress_update(progress, 15, 'Generating M3U and EPG files...[CR][CR]This may take a minute.')

        from modules import iptv_generator
        result = iptv_generator.generate(server, username, password)

        _progress_update(progress, 90, 'Reloading IPTV Simple and Live TV...')

        if not result or result.get('success') is not True:
            error = result.get('error', 'Unknown error') if isinstance(result, dict) else 'Unknown error'
            report = result.get('report', '') if isinstance(result, dict) else ''

            final_text = 'IPTV generation failed:[CR][CR]%s' % error
            if report:
                final_text += '[CR][CR]Report:[CR]%s' % report

        else:
            iptv_simple_settings = result.get('iptv_simple_settings', '')
            pvr_reload = result.get('pvr_reload', {})
            pvr_reload_text = pvr_reload.get('message', 'PVR reload status unknown.') if isinstance(pvr_reload, dict) else str(pvr_reload)

            _progress_update(progress, 100, 'IPTV generated successfully.')

            final_text = (
                'IPTV generated successfully.[CR][CR]'
                'Channels: %s[CR]'
                'Dropped/review items: %s[CR][CR]'
                'M3U:[CR]%s[CR][CR]'
                'EPG:[CR]%s[CR][CR]'
                'Report:[CR]%s[CR][CR]'
                'IPTV Simple settings updated:[CR]%s[CR][CR]'
                'PVR reload:[CR]%s'
            ) % (
                result.get('channels', 'unknown'),
                result.get('dropped', 'unknown'),
                result.get('playlist', ''),
                result.get('epg', ''),
                result.get('report', ''),
                iptv_simple_settings or 'Not updated',
                pvr_reload_text
            )

    except ImportError:
        final_text = (
            'The IPTV generator module is not connected yet.[CR][CR]'
            'Missing file:[CR]resources/lib/modules/iptv_generator.py'
        )

    except Exception as exc:
        final_text = 'IPTV generation failed:[CR][CR]%s' % str(exc)

    finally:
        _progress_close(progress)
        k.set_property('fenlight.iptv_generation_running', 'false')

    return k.ok_dialog(heading=final_heading, text=final_text)


def open_live_tv(params=None):
    iptv_m3u = 'special://userdata/addon_data/plugin.video.fenlight/iptv/IPTV.m3u'

    if not k.path_exists(iptv_m3u):
        return k.ok_dialog(
            heading='Live TV',
            text='Please enter Xtream details in Account Settings and generate Live TV.'
        )

    return k.execute_builtin('ActivateWindow(TVGuide)')