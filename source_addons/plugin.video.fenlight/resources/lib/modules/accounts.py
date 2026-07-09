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



def toggle_autoplay(params=None):
    movie_enabled = get_setting('fenlight.auto_play_movie', 'false') == 'true'
    episode_enabled = get_setting('fenlight.auto_play_episode', 'false') == 'true'

    # If both are already enabled, turn both off. Otherwise turn both on.
    new_value = 'false' if movie_enabled and episode_enabled else 'true'

    set_setting('auto_play_movie', new_value)
    set_setting('auto_play_episode', new_value)

def toggle_auto_next_episode(params=None):
	current_value = get_setting('fenlight.autoplay_next_episode', 'false') == 'true'
	new_value = 'false' if current_value else 'true'

	set_setting('autoplay_next_episode', new_value)

	# Keep the custom Accounts window row updated immediately.
	k.set_property('fenlight.autoplay_next_episode', new_value)

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
                'Live TV generated successfully.[CR][CR]'
                'Enabled channels: %s[CR]'
                'Catalogue channels: %s[CR]'
                'Disabled channels: %s[CR]'
                'Stream variants: %s[CR]'
                'Dropped/review items: %s[CR][CR]'
                'M3U:[CR]%s[CR][CR]'
                'EPG:[CR]%s[CR][CR]'
                'Catalogue:[CR]%s[CR][CR]'
                'Report:[CR]%s[CR][CR]'
                'IPTV Simple settings updated:[CR]%s[CR][CR]'
                'PVR reload:[CR]%s'
            ) % (
                result.get('channels', 'unknown'),
                result.get('catalog_channels', 'unknown'),
                result.get('disabled', 'unknown'),
                result.get('stream_variants', 'unknown'),
                result.get('dropped', 'unknown'),
                result.get('playlist', ''),
                result.get('epg', ''),
                result.get('catalog', ''),
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


# =========================
# Kodi Audio Settings
# =========================

AUDIO_DEVICE_SETTING = 'audiooutput.audiodevice'
AUDIO_PASSTHROUGH_DEVICE_SETTING = 'audiooutput.passthroughdevice'
AUDIO_GUI_SOUNDS_SETTING = 'audiooutput.guisoundmode'

GUI_SOUND_CHOICES = [
    ('Never', 0),
    ('Only when playback stopped', 1),
    ('Always', 2),
]

AUDIO_TOGGLE_SETTINGS = {
    'audiooutput.passthrough': ('audio.passthrough', 'Allow Passthrough'),
    'audiooutput.ac3passthrough': ('audio.ac3', 'Dolby Digital / AC3'),
    'audiooutput.ac3transcode': ('audio.ac3transcode', 'Dolby Digital / AC3 Transcoding'),
    'audiooutput.eac3passthrough': ('audio.eac3', 'Dolby Digital Plus / E-AC3'),
    'audiooutput.dtspassthrough': ('audio.dts', 'DTS'),
    'audiooutput.truehdpassthrough': ('audio.truehd', 'TrueHD / Atmos'),
    'audiooutput.dtshdpassthrough': ('audio.dtshd', 'DTS-HD'),
}


def _jsonrpc(method, params=None):
    import json
    import xbmc

    request = {
        'jsonrpc': '2.0',
        'id': 1,
        'method': method
    }

    if params is not None:
        request['params'] = params

    response = xbmc.executeJSONRPC(json.dumps(request))
    data = json.loads(response or '{}')

    if data.get('error'):
        raise Exception('%s failed: %s' % (method, data.get('error')))

    return data.get('result')


def _get_audio_settings_details():
    """
    Read Kodi's audio settings metadata so we can discover available devices
    and only show codec toggles that exist on this platform.
    """
    try:
        result = _jsonrpc('Settings.GetSettings', {
            'level': 'expert',
            'filter': {
                'section': 'system',
                'category': 'audio'
            }
        })
    except Exception:
        result = _jsonrpc('Settings.GetSettings', {'level': 'expert'})

    settings = result.get('settings', []) if isinstance(result, dict) else []
    return {item.get('id'): item for item in settings if item.get('id')}


def _get_kodi_setting_value(setting_id, default=None):
    result = _jsonrpc('Settings.GetSettingValue', {'setting': setting_id})
    if isinstance(result, dict):
        return result.get('value', default)
    return default


def _set_kodi_setting_value(setting_id, value):
    return _jsonrpc('Settings.SetSettingValue', {
        'setting': setting_id,
        'value': value
    })


def _setting_available(setting_id, details=None):
    if details and setting_id in details:
        return True

    try:
        _get_kodi_setting_value(setting_id)
        return True
    except Exception:
        return False


def _setting_options(setting_id):
    details = _get_audio_settings_details()
    item = details.get(setting_id, {})
    options = item.get('options') or item.get('values') or []

    parsed = []

    for option in options:
        if isinstance(option, dict):
            value = option.get('value')
            label = option.get('label') or option.get('name') or str(value)
        else:
            value = option
            label = str(option)

        if value not in (None, ''):
            parsed.append((str(label), value))

    return parsed


def _label_for_setting_value(setting_id, value):
    for label, option_value in _setting_options(setting_id):
        if str(option_value) == str(value):
            return label

    if value in ('', None):
        return 'Not Set'

    return str(value)


def _gui_sounds_label(value):
    try:
        value = int(value)
    except Exception:
        return 'Unknown'

    for label, option_value in GUI_SOUND_CHOICES:
        if option_value == value:
            return label

    return 'Unknown'


def refresh_audio_properties(params=None):
    """
    Populate window properties used by accounts_manager.xml.
    """
    try:
        details = _get_audio_settings_details()

        current_device = _get_kodi_setting_value(AUDIO_DEVICE_SETTING, '')
        device_label = _label_for_setting_value(AUDIO_DEVICE_SETTING, current_device)

        k.set_property('fenlight.audio.output_device', str(current_device or ''))
        k.set_property('fenlight.audio.output_device.label', device_label or 'Not Set')

        gui_sounds_available = _setting_available(AUDIO_GUI_SOUNDS_SETTING, details)
        k.set_property('fenlight.audio.gui_sounds.available', 'true' if gui_sounds_available else 'false')

        if gui_sounds_available:
            try:
                gui_sounds_value = _get_kodi_setting_value(AUDIO_GUI_SOUNDS_SETTING, 1)
                k.set_property('fenlight.audio.gui_sounds', str(gui_sounds_value))
                k.set_property('fenlight.audio.gui_sounds.label', _gui_sounds_label(gui_sounds_value))
            except Exception:
                k.set_property('fenlight.audio.gui_sounds', '1')
                k.set_property('fenlight.audio.gui_sounds.label', 'Only when playback stopped')
        else:
            k.set_property('fenlight.audio.gui_sounds', '1')
            k.set_property('fenlight.audio.gui_sounds.label', 'Unavailable')


        for setting_id, data in AUDIO_TOGGLE_SETTINGS.items():
            prop_name, label = data
            available = _setting_available(setting_id, details)
            k.set_property('fenlight.%s.available' % prop_name, 'true' if available else 'false')

            if available:
                try:
                    value = _get_kodi_setting_value(setting_id, False)
                    k.set_property('fenlight.%s' % prop_name, str(bool(value)).lower())
                except Exception:
                    k.set_property('fenlight.%s' % prop_name, 'false')
            else:
                k.set_property('fenlight.%s' % prop_name, 'false')

    except Exception as exc:
        k.set_property('fenlight.audio.output_device.label', 'Unavailable')
        k.notification('Could not read Kodi audio settings: %s' % str(exc), 4000)



def choose_audio_output(params=None):
    """
    Let the user pick from Kodi's actual available audio output devices.
    Sets both normal output and passthrough output to the selected device.
    """
    try:
        import xbmcgui

        options = _setting_options(AUDIO_DEVICE_SETTING)
        if not options:
            return k.ok_dialog(
                heading='Audio Output',
                text='Kodi did not return any audio output devices for this system.'
            )

        current_value = _get_kodi_setting_value(AUDIO_DEVICE_SETTING, '')
        labels = [item[0] for item in options]

        preselect = 0
        for index, item in enumerate(options):
            if str(item[1]) == str(current_value):
                preselect = index
                break

        dialog = xbmcgui.Dialog()
        index = dialog.select('Audio Output Device', labels, preselect=preselect)

        if index < 0:
            return

        label, value = options[index]

        failed = []

        try:
            _set_kodi_setting_value(AUDIO_DEVICE_SETTING, value)
        except Exception as exc:
            failed.append('Output device: %s' % str(exc))

        try:
            _set_kodi_setting_value(AUDIO_PASSTHROUGH_DEVICE_SETTING, value)
        except Exception as exc:
            failed.append('Passthrough device: %s' % str(exc))

        # If a user is choosing an audio device from this menu, passthrough is
        # usually expected for AVR/soundbar setups, so enable it automatically.
        try:
            _set_kodi_setting_value('audiooutput.passthrough', True)
        except Exception:
            pass

        refresh_audio_properties()

        if failed:
            return k.ok_dialog(
                heading='Audio Output',
                text='Audio device selected, but some settings could not be updated:[CR][CR]%s' % '[CR]'.join(failed)
            )


    except Exception as exc:
        return k.ok_dialog(
            heading='Audio Output',
            text='Could not change audio output:[CR][CR]%s' % str(exc)
        )


def toggle_audio_setting(params):
    setting_id = params.get('setting_id')

    if setting_id not in AUDIO_TOGGLE_SETTINGS:
        return k.ok_dialog(
            heading='Audio Settings',
            text='Invalid audio setting requested.'
        )

    try:
        current_value = bool(_get_kodi_setting_value(setting_id, False))
        new_value = not current_value

        # If enabling a codec format, make sure global passthrough is also on.
        if setting_id != 'audiooutput.passthrough' and new_value:
            try:
                _set_kodi_setting_value('audiooutput.passthrough', True)
            except Exception:
                pass

        _set_kodi_setting_value(setting_id, new_value)
        refresh_audio_properties()

    except Exception as exc:
        return k.ok_dialog(
            heading='Audio Settings',
            text='Could not change audio setting:[CR][CR]%s' % str(exc)
        )
    

## Set size limits function  ##

def set_size_limits(params=None):
    try:
        import xbmcgui

        options = [
            ('Recommended', 'Movies 30000 MB / TV 15000 MB', '30000', '15000', '2'),
            ('High', 'Movies 70000 MB / TV 25000 MB', '70000', '25000', '2'),
            ('Lower', 'Movies 20000 MB / TV 10000 MB', '20000', '10000', '2'),
            ('Custom', 'Enter custom limits', None, None, '2'),
        ]

        labels = ['%s - %s' % (item[0], item[1]) for item in options]
        index = xbmcgui.Dialog().select('Size Limits', labels)

        if index < 0:
            return

        name, label, movie_size, episode_size, filter_method = options[index]

        if name == 'Custom':
            movie_size = k.kodi_dialog().input('Movie max size in MB', defaultt='30000')
            if not movie_size:
                return

            episode_size = k.kodi_dialog().input('TV episode max size in MB', defaultt='15000')
            if not episode_size:
                return

            try:
                movie_size = str(int(movie_size))
                episode_size = str(int(episode_size))
            except Exception:
                return k.ok_dialog(
                    heading='Size Limits',
                    text='Please enter numbers only, for example 30000 and 15000.'
                )

            label = 'Movies %s MB / TV %s MB' % (movie_size, episode_size)


        set_setting('results.filter_size_method', '2')
        set_setting('results.movie_size_min', '0')
        set_setting('results.movie_size_max', movie_size)
        set_setting('results.episode_size_min', '0')
        set_setting('results.episode_size_max', episode_size)

        set_setting('simple.size_limits', label)
        k.set_property('fenlight.simple.size_limits', label)


    except Exception as exc:
        return k.ok_dialog(
            heading='Size Limits',
            text='Could not change size limits:[CR][CR]%s' % str(exc)
        )


## Set Kodi Live TV Guide default select action to Switch to Channel.
## Kodi setting:
## Settings -> PVR & Live TV -> Guide -> Default select action

def set_pvr_guide_select_action_switch_channel(params=None, silent=True):
    try:
        show_dialog = silent is False

        if isinstance(params, dict):
            show_dialog = str(params.get('silent', '')).lower() == 'false' or str(params.get('show_dialog', '')).lower() == 'true'

        _set_kodi_setting_value('epg.selectaction', 1)

        current_value = _get_kodi_setting_value('epg.selectaction', None)
        k.logger('Fen Light', 'Kodi EPG select action set to: %s' % str(current_value))

        if show_dialog:
            return k.ok_dialog(
                heading='Live TV',
                text='TV Guide select action has been set to Switch to Channel.[CR][CR]Current value: %s' % str(current_value)
            )

        return str(current_value) == '1'

    except Exception as exc:
        k.logger('Fen Light', 'Could not set Kodi EPG select action: %s' % str(exc))

        if silent is False:
            return k.ok_dialog(
                heading='Live TV',
                text='Could not set TV Guide select action:[CR][CR]%s' % str(exc)
            )

        return False
    
def set_gui_sounds(params=None):
    try:
        import xbmcgui

        current_value = _get_kodi_setting_value(AUDIO_GUI_SOUNDS_SETTING, 1)

        preselect = 1
        try:
            current_value_int = int(current_value)
            for index, item in enumerate(GUI_SOUND_CHOICES):
                if item[1] == current_value_int:
                    preselect = index
                    break
        except Exception:
            pass

        labels = [item[0] for item in GUI_SOUND_CHOICES]
        index = xbmcgui.Dialog().select('Kodi GUI Sounds', labels, preselect=preselect)

        if index < 0:
            return

        label, value = GUI_SOUND_CHOICES[index]

        _set_kodi_setting_value(AUDIO_GUI_SOUNDS_SETTING, value)
        refresh_audio_properties()

    except Exception as exc:
        return k.ok_dialog(
            heading='Kodi GUI Sounds',
            text='Could not change Kodi GUI sounds setting:[CR][CR]%s' % str(exc)
        )

# =========================
# Grouped Live TV channel management
# =========================

def _iptv_progress_dialog(heading='Live TV'):
    try:
        import xbmcgui
        dialog = xbmcgui.DialogProgress()
        dialog.create(heading, 'Preparing...')
        return dialog
    except Exception:
        return None


def _catalog_channel_label(channel):
    # Kodi's multiselect dialog already draws the tick/untick indicator.
    # Do not prefix labels with [X] / [ ] or the rows look duplicated.
    section = channel.get('section') or 'Other'
    epg = 'EPG' if channel.get('xmltv_id') else 'No EPG'
    streams = len(channel.get('streams', []))
    return '%s - %s  (%s links, %s)' % (section, channel.get('name', 'Unknown'), streams, epg)


def manage_iptv_channels(params=None):
    """Open a popup where channels can be ticked/unticked, then rebuild once on OK."""
    try:
        import xbmcgui
        from modules import iptv_generator

        catalog = iptv_generator.load_catalog()
        channels = catalog.get('channels', [])
        if not channels:
            return k.ok_dialog(
                heading='Manage Live TV Channels',
                text='No channel catalogue was found.[CR][CR]Run Generate / Refresh Live TV first.'
            )

        labels = [_catalog_channel_label(item) for item in channels]
        preselect = [index for index, item in enumerate(channels) if item.get('enabled')]

        selected = xbmcgui.Dialog().multiselect(
            'Manage Live TV Channels',
            labels,
            preselect=preselect
        )

        if selected is None:
            return

        selected_keys = [channels[index].get('key') for index in selected if 0 <= index < len(channels)]

        progress = _iptv_progress_dialog('Manage Live TV Channels')
        try:
            _progress_update(progress, 20, 'Saving channel choices...')
            _progress_update(progress, 50, 'Rebuilding M3U and EPG...')
            result = iptv_generator.update_catalog_enabled_states(selected_keys)
            _progress_update(progress, 100, 'Live TV files rebuilt.')
        finally:
            _progress_close(progress)

        if not result or result.get('success') is not True:
            error = result.get('error', 'Unknown error') if isinstance(result, dict) else 'Unknown error'
            return k.ok_dialog(heading='Manage Live TV Channels', text='Could not rebuild Live TV files:[CR][CR]%s' % error)

        pvr_reload = result.get('pvr_reload', {})
        pvr_reload_text = pvr_reload.get('message', 'PVR reload status unknown.') if isinstance(pvr_reload, dict) else str(pvr_reload)
        return k.ok_dialog(
            heading='Manage Live TV Channels',
            text=(
                'Channel choices saved.[CR][CR]'
                'Enabled channels: %s[CR]'
                'Disabled channels: %s[CR]'
                'Filtered EPG: %s[CR][CR]'
                'PVR reload:[CR]%s'
            ) % (
                result.get('channels', 'unknown'),
                result.get('disabled', 'unknown'),
                result.get('filtered_epg', 'unknown'),
                pvr_reload_text
            )
        )

    except Exception as exc:
        return k.ok_dialog(
            heading='Manage Live TV Channels',
            text='Could not open the channel manager:[CR][CR]%s' % str(exc)
        )


def rebuild_iptv_files(params=None):
    """Rebuild IPTV.m3u and IPTV-EPG.xml from the existing catalogue without redownloading provider JSON."""
    progress = _iptv_progress_dialog('Rebuild Live TV')
    try:
        from modules import iptv_generator
        _progress_update(progress, 25, 'Loading channel catalogue...')
        _progress_update(progress, 55, 'Rebuilding M3U and filtered EPG...')
        result = iptv_generator.rebuild_from_catalog(reload_pvr=True)
        _progress_update(progress, 100, 'Done.')
    except Exception as exc:
        _progress_close(progress)
        return k.ok_dialog(heading='Rebuild Live TV', text='Could not rebuild Live TV files:[CR][CR]%s' % str(exc))
    finally:
        _progress_close(progress)

    pvr_reload = result.get('pvr_reload', {})
    pvr_reload_text = pvr_reload.get('message', 'PVR reload status unknown.') if isinstance(pvr_reload, dict) else str(pvr_reload)
    return k.ok_dialog(
        heading='Rebuild Live TV',
        text=(
            'Live TV files rebuilt.[CR][CR]'
            'Enabled channels: %s[CR]'
            'Filtered EPG: %s[CR][CR]'
            'PVR reload:[CR]%s'
        ) % (result.get('channels', 'unknown'), result.get('filtered_epg', 'unknown'), pvr_reload_text)
    )


def refresh_iptv_epg(params=None):
    """Redownload EPGShare UK/US sources and rebuild the filtered local IPTV-EPG.xml."""
    progress = _iptv_progress_dialog('Refresh Live TV EPG')
    try:
        from modules import iptv_generator
        _progress_update(progress, 15, 'Downloading fresh UK/US EPG data...')
        _progress_update(progress, 50, 'Filtering EPG for enabled channels...')
        result = iptv_generator.refresh_epg_only(reload_pvr=True, force=True)
        _progress_update(progress, 100, 'Done.')
    except Exception as exc:
        _progress_close(progress)
        return k.ok_dialog(heading='Refresh Live TV EPG', text='Could not refresh Live TV EPG:[CR][CR]%s' % str(exc))
    finally:
        _progress_close(progress)

    pvr_reload = result.get('pvr_reload', {})
    pvr_reload_text = pvr_reload.get('message', 'PVR reload status unknown.') if isinstance(pvr_reload, dict) else str(pvr_reload)
    return k.ok_dialog(
        heading='Refresh Live TV EPG',
        text=(
            'Live TV EPG refreshed.[CR][CR]'
            'Filtered EPG: %s[CR][CR]'
            'EPG file:[CR]%s[CR][CR]'
            'PVR reload:[CR]%s'
        ) % (result.get('filtered_epg', 'unknown'), result.get('epg', ''), pvr_reload_text)
    )


def iptv_play_channel(params=None):
    """Resolve plugin:// Live TV channel clicks from IPTV Simple."""
    params = params or {}
    channel_key = params.get('channel') or params.get('channel_key') or ''
    if not channel_key:
        return k.ok_dialog(heading='Live TV', text='Missing channel key.')
    try:
        from modules import iptv_generator
        return iptv_generator.play_channel(channel_key)
    except Exception as exc:
        return k.ok_dialog(heading='Live TV Playback', text='Could not play this channel:[CR][CR]%s' % str(exc))
