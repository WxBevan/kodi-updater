# -*- coding: utf-8 -*-
from __future__ import absolute_import

import re
import unicodedata

from windows.base_window import BaseDialog


class IPTVChannelManager(BaseDialog):
    """Searchable two-column IPTV channel manager.

    With an empty search the first page contains browse groups. Entering text
    always searches the complete catalogue, regardless of the current group.
    Channel selections are held in memory until Save Changes is pressed.
    """

    SEARCH_ID = 100
    CLEAR_SEARCH_ID = 101
    FOCUS_PARK_ID = 199
    PANEL_ID = 200
    ENABLE_VISIBLE_ID = 300
    DISABLE_VISIBLE_ID = 301
    SAVE_ID = 302
    CANCEL_ID = 303
    BACK_GROUPS_ID = 304
    COUNT_ID = 400
    MATCH_ID = 401
    VIEW_TITLE_ID = 402

    GROUP_ORDER = (
        'UK Common',
        'USA Common',
        'All Channels',
        'Sports',
        'News',
        'Documentary',
        'Kids',
        'Music',
        'Other UK Channels',
        'Special Events',
        'Other USA Channels',
    )

    # Fallback memberships for catalogues created before menu_groups existed.
    POPULAR_UK_SPORTS_KEYS = frozenset({
        'sky_sports_main_event', 'sky_sports_premier_league',
        'sky_sports_football', 'sky_sports_cricket', 'sky_sports_golf',
        'sky_sports_tennis', 'tnt_sports_1', 'tnt_sports_2',
        'tnt_sports_3', 'tnt_sports_4',
    })
    UK_COMMON_BROADCASTER_KEYS = frozenset({
        'bbc_one', 'bbc_two', 'uk_bbcnews',
        'itv1', 'itv2', 'itv3', 'channel_4', 'channel_5',
    })
    CORE_COMMON_UK_KEYS = frozenset(
        set(POPULAR_UK_SPORTS_KEYS) | set(UK_COMMON_BROADCASTER_KEYS)
    )
    UK_COMMON_XMLTV_IDS = frozenset({
        'bbconelonhduk', 'bbctwohduk', 'bbcnewshduk',
        'itv1hduk', 'itv2hduk', 'itv3hduk',
        'channel4hduk', 'channel5hduk', 'channel5uk',
    })
    UK_COMMON_PROVIDER_EPG_IDS = frozenset({
        'bbc1uk', 'bbc2uk', 'bbcnewsuk',
        'itv1uk', 'itv2uk', 'itv3uk', 'channel4uk', 'channel5uk',
    })
    UK_COMMON_EXACT_NAMES = frozenset({
        'bbc 1', 'bbc one', 'bbc 2', 'bbc two', 'bbc news',
        'itv1', 'itv 1', 'itv2', 'itv 2', 'itv3', 'itv 3',
        'channel 4', 'channel 5',
    })
    NON_MOVIE_LEGACY_MOVIES_KEYS = frozenset({
        'uk_skycomedy', 'uk_skycrime', 'uk_skymax', 'uk_noepg_skyone',
    })
    USA_COMMON_KEYS = frozenset({
        'dazn_1', 'uk_dazn1', 'dazn_2', 'dazn_3', 'dazn_4',
        'us_espn', 'espn_2', 'us_espnnews',
        'us_foxsports1', 'us_foxsports2', 'us_cbssportsnetwork',
        'us_accnetwork', 'us_secnetwork', 'us_golfchannel',
        'us_tennischannel', 'nba_tv', 'nfl_network', 'nfl_redzone',
        'nhl_network', 'mlb_network', 'wwe_network',
    })

    SECTION_GROUP_NAMES = {
        'BBC': 'Other UK Channels',
        'ITV': 'Other UK Channels',
        'Channel 4 & 5': 'Other UK Channels',
        'US Extras': 'Other USA Channels',
        'USA Channels': 'Other USA Channels',
        'Non-UK Extras': 'Other USA Channels',
        'Other Extras': 'Other USA Channels',
    }
    GROUP_LABEL_ALIASES = {
        'Common': 'UK Common',
        'BBC': 'Other UK Channels',
        'ITV': 'Other UK Channels',
        'Channel 4 & 5': 'Other UK Channels',
        'USA Channels': 'Other USA Channels',
        'Other Extras': 'Other USA Channels',
    }

    def __init__(self, *args, **kwargs):
        BaseDialog.__init__(self, *args)
        supplied_channels = list(kwargs.get('channels') or [])
        self.channels = [
            item for item in supplied_channels
            if item.get('user_selectable', True) and not self._is_movie_channel(item)
        ]
        self.selected_keys = {
            str(item.get('key')) for item in self.channels
            if item.get('key') and item.get('enabled')
        }
        self.visible_channels = []
        self.visible_groups = []
        self.search_text = ''
        self.active_group = None
        self.display_mode = 'groups'
        self.saved = False
        self.result = None
        self._search_blobs = {
            str(item.get('key')): self._build_search_blob(item)
            for item in self.channels if item.get('key')
        }

    @staticmethod
    def _normalise(value):
        text = unicodedata.normalize('NFKD', str(value or '')).lower()
        text = text.replace('+', ' plus ')
        text = re.sub(r'[^a-z0-9]+', ' ', text)
        return re.sub(r'\s+', ' ', text).strip()

    @classmethod
    def _is_movie_channel(cls, channel):
        if not isinstance(channel, dict):
            return False
        values = [
            channel.get('key', ''), channel.get('name', ''),
            channel.get('xmltv_id', ''),
        ]
        for stream in (channel.get('streams') or [])[:20]:
            values.extend((
                stream.get('name', ''), stream.get('display_name', ''),
                stream.get('provider_epg', ''),
            ))
        key = str(channel.get('key') or '')
        section = str(channel.get('section') or '')
        if section == 'Movies' and key not in cls.NON_MOVIE_LEGACY_MOVIES_KEYS:
            return True
        text = cls._normalise(' '.join(str(value or '') for value in values))
        return bool(re.search(
            r'\b(?:sky\s+cinema|film\s*4|talking\s+pictures|movies?|cinema|tcm|sky\s+action)\b',
            text,
        ))

    @staticmethod
    def _identity_token(value):
        return re.sub(r'[^a-z0-9]+', '', str(value or '').lower())

    @classmethod
    def _is_uk_common_broadcaster(cls, channel):
        key = str(channel.get('key') or '')
        if key in cls.UK_COMMON_BROADCASTER_KEYS:
            return True
        if cls._identity_token(channel.get('xmltv_id')) in cls.UK_COMMON_XMLTV_IDS:
            return True
        for stream in (channel.get('streams') or [])[:40]:
            if cls._identity_token(stream.get('provider_epg')) in cls.UK_COMMON_PROVIDER_EPG_IDS:
                return True
        name = cls._normalise(channel.get('name', ''))
        name = re.sub(r'\b(?:hd|fhd|uhd|4k|8k|raw|hevc)\b', ' ', name)
        name = re.sub(r'\s+', ' ', name).strip()
        return name in cls.UK_COMMON_EXACT_NAMES

    @classmethod
    def _is_common_uk_channel(cls, channel):
        if cls._is_movie_channel(channel):
            return False
        if channel.get('dynamic_event') or str(channel.get('section') or '') == 'Special Events':
            return False
        key = str(channel.get('key') or '')
        return key in cls.POPULAR_UK_SPORTS_KEYS or cls._is_uk_common_broadcaster(channel)

    @classmethod
    def _is_uk_broadcaster_family(cls, channel):
        if cls._is_movie_channel(channel):
            return False
        section = str(channel.get('section') or '')
        if section in ('BBC', 'ITV', 'Channel 4 & 5'):
            return True
        key = str(channel.get('key') or '').lower()
        if re.match(r'^(?:bbc|uk_bbc|itv|uk_itv|citv|uk_citv)', key):
            return True
        if key in {
            'channel_4', 'channel_5', 'uk_4seven', 'uk_e4', 'uk_more4',
            'uk_5star', 'uk_5usa', 'uk_5select', 'uk_5action',
        }:
            return True
        name = cls._normalise(channel.get('name', ''))
        return bool(re.match(
            r'^(?:bbc\b|itv\b|citv\b|channel\s+[45]\b|e4\b|more4\b|4seven\b|5star\b|5usa\b|5select\b|5action\b)',
            name,
        ))

    def _menu_groups(self, channel):
        raw_groups = [str(value) for value in (channel.get('menu_groups') or []) if value]
        groups = [self.GROUP_LABEL_ALIASES.get(value, value) for value in raw_groups]
        key = str(channel.get('key') or '')
        common_us = key in self.USA_COMMON_KEYS or bool(channel.get('common_us'))
        if not groups:
            section = str(channel.get('section') or 'Other UK Channels')
            if section == 'Movies' and not self._is_movie_channel(channel):
                section = 'Other UK Channels'
            section_group = self.SECTION_GROUP_NAMES.get(section, section)
            if self._is_common_uk_channel(channel):
                groups.append('UK Common')
            if common_us:
                groups.append('USA Common')
            if not (common_us and section_group == 'Other USA Channels'):
                groups.append(section_group)
            if self._is_uk_broadcaster_family(channel) and 'Other UK Channels' not in groups:
                groups.append('Other UK Channels')
        else:
            # Discard stale virtual memberships from older catalogues, then
            # derive the current policy from stable channel identity/section.
            groups = [
                value for value in groups
                if value not in (
                    'BBC', 'ITV', 'Channel 4 & 5',
                    'UK Common', 'USA Common',
                )
            ]
            if self._is_common_uk_channel(channel):
                groups.insert(0, 'UK Common')
            if common_us:
                groups.insert(0, 'USA Common')
                groups = [value for value in groups if value != 'Other USA Channels']
            section = str(channel.get('section') or 'Other UK Channels')
            section_group = self.SECTION_GROUP_NAMES.get(section, section)
            if section_group not in groups and not (common_us and section_group == 'Other USA Channels'):
                groups.append(section_group)
            if self._is_uk_broadcaster_family(channel) and 'Other UK Channels' not in groups:
                groups.append('Other UK Channels')

        # Keep order while removing duplicates and obsolete groups.
        output = []
        for value in groups:
            if value and value not in ('Movies', 'Other Extras', 'USA Channels', 'Common') and value not in output:
                output.append(value)
        return output

    def _build_search_blob(self, channel):
        values = [
            channel.get('name', ''),
            channel.get('section', ''),
            ' '.join(self._menu_groups(channel)),
            channel.get('xmltv_id', ''),
            channel.get('epg_status', ''),
            'epg' if channel.get('xmltv_id') else 'no epg',
            'enabled' if channel.get('enabled') else 'disabled',
        ]
        for stream in channel.get('streams', [])[:40]:
            values.extend((
                stream.get('name', ''),
                stream.get('display_name', ''),
                stream.get('provider_epg', ''),
                stream.get('category', ''),
                stream.get('event_family', ''),
            ))
        return self._normalise(' '.join(str(value or '') for value in values))

    def onInit(self):
        self._refresh_panel(force=True)
        # The panel is the primary browse control. Keeping it as the initial
        # focus also prevents Kodi briefly falling back to the Search edit
        # while the panel is reset during group transitions.
        try:
            self.setFocusId(self.PANEL_ID)
        except Exception:
            self.setFocusId(self.SEARCH_ID)

    def run(self):
        self.doModal()
        try:
            self.clearProperties()
        except Exception:
            pass
        return self.result

    def _current_search_text(self):
        try:
            return self.get_control(self.SEARCH_ID).getText() or ''
        except Exception:
            return self.search_text

    def _matches(self, channel, query):
        if not query:
            return True
        blob = self._search_blobs.get(str(channel.get('key')), '')
        return all(token in blob for token in self._normalise(query).split())

    def _channel_in_group(self, channel, group_name):
        if group_name == 'All Channels':
            return True
        return group_name in self._menu_groups(channel)

    def _available_groups(self):
        counts = {}
        enabled_counts = {}
        for channel in self.channels:
            groups = self._menu_groups(channel)
            for group in groups:
                counts[group] = counts.get(group, 0) + 1
                if str(channel.get('key') or '') in self.selected_keys:
                    enabled_counts[group] = enabled_counts.get(group, 0) + 1
        counts['All Channels'] = len(self.channels)
        enabled_counts['All Channels'] = len(self.selected_keys)

        ordered = []
        seen = set()
        for group in self.GROUP_ORDER:
            if counts.get(group, 0):
                ordered.append({
                    'name': group,
                    'count': counts[group],
                    'enabled_count': enabled_counts.get(group, 0),
                })
                seen.add(group)
        for group in sorted(value for value in counts if value not in seen):
            ordered.append({
                'name': group,
                'count': counts[group],
                'enabled_count': enabled_counts.get(group, 0),
            })
        return ordered

    def _make_group_item(self, group):
        item = self.make_listitem()
        item.setLabel(str(group.get('name') or 'Other'))
        item.setLabel2('%s channels · %s enabled' % (
            group.get('count', 0), group.get('enabled_count', 0)
        ))
        item.setProperty('entry_type', 'group')
        item.setProperty('group_name', str(group.get('name') or ''))
        item.setProperty('check', '[B]›[/B]')
        return item

    def _make_channel_item(self, channel):
        item = self.make_listitem()
        key = str(channel.get('key') or '')
        selected = key in self.selected_keys
        item.setLabel(str(channel.get('name') or 'Unknown'))
        item.setLabel2('%s · %s links · %s' % (
            channel.get('section') or 'Other',
            len(channel.get('streams', [])),
            'EPG' if channel.get('xmltv_id') else 'No EPG',
        ))
        item.setProperty('entry_type', 'channel')
        item.setProperty('channel_key', key)
        item.setProperty('selected', 'true' if selected else 'false')
        item.setProperty('check', '[COLOR limegreen]●[/COLOR]' if selected else '[COLOR grey]○[/COLOR]')
        item.setProperty('epg', 'EPG' if channel.get('xmltv_id') else 'No EPG')
        item.setProperty('section', str(channel.get('section') or 'Other'))
        return item

    def _set_action_visibility(self, channel_mode):
        for control_id in (self.ENABLE_VISIBLE_ID, self.DISABLE_VISIBLE_ID):
            try:
                self.get_control(control_id).setVisible(bool(channel_mode))
            except Exception:
                pass
        try:
            self.get_control(self.BACK_GROUPS_ID).setVisible(bool(self.active_group and not self.search_text))
        except Exception:
            pass
        self._configure_bottom_navigation()

    def _refresh_panel(self, force=False, focus_panel=False):
        query = self._current_search_text()
        if not force and query == self.search_text:
            return
        self.search_text = query

        previous_identity = ''
        try:
            selected_item = self.get_control(self.PANEL_ID).getSelectedItem()
            if selected_item:
                previous_identity = (
                    selected_item.getProperty('channel_key')
                    or selected_item.getProperty('group_name')
                )
        except Exception:
            pass

        # Resetting a focused Kodi container briefly transfers focus to the
        # window default control. During group entry/return, park focus on an
        # invisible off-screen button until the replacement items are ready,
        # then move directly to the rebuilt panel without flashing Search.
        if focus_panel:
            try:
                self.setFocusId(self.FOCUS_PARK_ID)
            except Exception:
                pass

        control = self.get_control(self.PANEL_ID)
        control.reset()

        if query:
            self.display_mode = 'channels'
            self.visible_groups = []
            self.visible_channels = [item for item in self.channels if self._matches(item, query)]
            entries = [self._make_channel_item(item) for item in self.visible_channels]
            title = 'Search Results'
            footer = '%s matching channels' % len(self.visible_channels)
        elif self.active_group:
            self.display_mode = 'channels'
            self.visible_groups = []
            self.visible_channels = [
                item for item in self.channels
                if self._channel_in_group(item, self.active_group)
            ]
            entries = [self._make_channel_item(item) for item in self.visible_channels]
            title = self.active_group
            footer = '%s channels in %s' % (len(self.visible_channels), self.active_group)
        else:
            self.display_mode = 'groups'
            self.visible_channels = []
            self.visible_groups = self._available_groups()
            entries = [self._make_group_item(item) for item in self.visible_groups]
            title = 'Choose a Group'
            footer = '%s groups' % len(self.visible_groups)

        if entries:
            control.addItems(entries)
            position = 0
            if previous_identity:
                visible_values = self.visible_channels if self.display_mode == 'channels' else self.visible_groups
                for index, value in enumerate(visible_values):
                    identity = str(value.get('key') or value.get('name') or '')
                    if identity == previous_identity:
                        position = index
                        break
            control.selectItem(position)

        self.set_label(self.COUNT_ID, '%s channels enabled' % len(self.selected_keys))
        self.set_label(self.MATCH_ID, footer)
        self.set_label(self.VIEW_TITLE_ID, title)
        self._set_action_visibility(self.display_mode == 'channels')
        if focus_panel:
            try:
                self.setFocusId(self.PANEL_ID)
            except Exception:
                pass

    def _refresh_visible_selection_rows(self):
        self._refresh_panel(force=True)

    def _open_or_toggle_current(self):
        try:
            panel = self.get_control(self.PANEL_ID)
            item = panel.getSelectedItem()
        except Exception:
            panel = None
            item = None
        if not item:
            return
        if item.getProperty('entry_type') == 'group':
            group_name = item.getProperty('group_name')
            if group_name:
                self.active_group = group_name
                self._refresh_panel(force=True, focus_panel=True)
            return

        key = item.getProperty('channel_key')
        if not key:
            return
        if key in self.selected_keys:
            self.selected_keys.remove(key)
            selected = False
        else:
            self.selected_keys.add(key)
            selected = True

        # Do not rebuild/reset the panel for a single toggle. Resetting a
        # focused Kodi panel can temporarily remove its selected item and make
        # Kodi return focus to the window's default Search control. Updating
        # the current ListItem in place preserves the exact row and focus.
        try:
            item.setProperty('selected', 'true' if selected else 'false')
            item.setProperty(
                'check',
                '[COLOR limegreen]●[/COLOR]' if selected else '[COLOR grey]○[/COLOR]',
            )
            self.set_label(self.COUNT_ID, '%s channels enabled' % len(self.selected_keys))
            if panel is not None:
                self.setFocusId(self.PANEL_ID)
        except Exception:
            # Conservative fallback for unusual Kodi builds where mutating the
            # selected ListItem is not reflected by the panel implementation.
            position = self._panel_position()
            self._refresh_panel(force=True)
            try:
                if position >= 0:
                    self.get_control(self.PANEL_ID).selectItem(position)
                self.setFocusId(self.PANEL_ID)
            except Exception:
                pass

    def _set_visible(self, enabled):
        if self.display_mode != 'channels':
            return
        keys = {str(item.get('key')) for item in self.visible_channels if item.get('key')}
        if enabled:
            self.selected_keys.update(keys)
        else:
            self.selected_keys.difference_update(keys)
        self._refresh_visible_selection_rows()

    def _clear_search(self):
        try:
            self.get_control(self.SEARCH_ID).setText('')
        except Exception:
            pass
        self.search_text = ''
        self._refresh_panel(force=True)
        try:
            self.setFocusId(self.SEARCH_ID)
        except Exception:
            pass

    def _back_to_groups(self):
        self.active_group = None
        self._refresh_panel(force=True, focus_panel=True)

    def _save(self):
        self.saved = True
        self.result = sorted(self.selected_keys)
        self.close()

    def _cancel(self):
        self.saved = False
        self.result = None
        self.close()

    def _panel_item_count(self):
        if self.display_mode == 'channels':
            return len(self.visible_channels)
        return len(self.visible_groups)

    def _panel_position(self):
        try:
            return int(self.get_control(self.PANEL_ID).getSelectedPosition())
        except Exception:
            return -1

    def _visible_bottom_controls(self):
        controls = []
        if self.active_group and not self.search_text:
            controls.append(self.BACK_GROUPS_ID)
        if self.display_mode == 'channels':
            controls.extend((self.ENABLE_VISIBLE_ID, self.DISABLE_VISIBLE_ID))
        controls.extend((self.SAVE_ID, self.CANCEL_ID))
        return controls

    def _configure_bottom_navigation(self):
        """Build one navigation chain from the buttons currently visible.

        Kodi already processes the XML navigation for directional actions. The
        former Python left/right handler then processed the same action again,
        which made focus jump over every second button. Runtime navigation is
        configured here instead, so Kodi alone moves through each visible
        control exactly once and hidden controls are never part of the chain.
        """
        try:
            panel = self.get_control(self.PANEL_ID)
            visible_ids = self._visible_bottom_controls()
            visible_controls = [self.get_control(control_id) for control_id in visible_ids]
        except Exception:
            return

        for index, control in enumerate(visible_controls):
            left_control = visible_controls[index - 1] if index > 0 else control
            right_control = visible_controls[index + 1] if index + 1 < len(visible_controls) else control
            try:
                control.setNavigation(panel, control, left_control, right_control)
            except Exception:
                # Older/unusual Kodi builds can retain the static XML graph.
                # The XML order is also sequential when all controls are shown.
                pass

    def onClick(self, control_id):
        if control_id == self.PANEL_ID:
            self._open_or_toggle_current()
        elif control_id == self.CLEAR_SEARCH_ID:
            self._clear_search()
        elif control_id == self.ENABLE_VISIBLE_ID:
            self._set_visible(True)
        elif control_id == self.DISABLE_VISIBLE_ID:
            self._set_visible(False)
        elif control_id == self.BACK_GROUPS_ID:
            self._back_to_groups()
        elif control_id == self.SAVE_ID:
            self._save()
        elif control_id == self.CANCEL_ID:
            self._cancel()

    def onFocus(self, control_id):
        self._refresh_panel()

    def onAction(self, action):
        action_id = action.getId() if hasattr(action, 'getId') else int(action)

        # Panel edge navigation is defined entirely in XML. A conditional
        # onleft applies only while column 0 is focused, allowing Kodi to move
        # naturally from the right column to the corresponding left item and
        # leave the panel for Save Changes only from the true far-left edge.
        # Do not intercept directional panel actions here: Python and Kodi
        # handling the same key caused the previous double-navigation bugs.

        if action_id in self.closing_actions:
            if self._current_search_text():
                self._clear_search()
            elif self.active_group:
                self._back_to_groups()
            else:
                self._cancel()
            return

        # Physical keyboards/remotes dispatch an action for each edit. Reading
        # the edit control after the action provides live global filtering;
        # deleting characters immediately expands the result set again.
        try:
            if self.getFocusId() == self.SEARCH_ID:
                self.sleep(15)
                self._refresh_panel()
        except Exception:
            pass
