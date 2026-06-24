from __future__ import annotations

import importlib

OPERATION_TO_RESOURCE: dict[str, str] = {
    'acl_delete': 'acl',
    'acl_get': 'acl',
    'acl_insert': 'acl',
    'acl_list': 'acl',
    'acl_patch': 'acl',
    'acl_update': 'acl',
    'acl_watch': 'acl',
    'calendar_list_delete': 'calendar_list',
    'calendar_list_get': 'calendar_list',
    'calendar_list_insert': 'calendar_list',
    'calendar_list_list': 'calendar_list',
    'calendar_list_patch': 'calendar_list',
    'calendar_list_update': 'calendar_list',
    'calendar_list_watch': 'calendar_list',
    'calendars_clear': 'calendars',
    'calendars_delete': 'calendars',
    'calendars_get': 'calendars',
    'calendars_insert': 'calendars',
    'calendars_patch': 'calendars',
    'calendars_update': 'calendars',
    'channels_stop': 'channels',
    'colors_get': 'colors',
    'events_delete': 'events',
    'events_get': 'events',
    'events_import': 'events',
    'events_insert': 'events',
    'events_instances': 'events',
    'events_list': 'events',
    'events_move': 'events',
    'events_patch': 'events',
    'events_quick_add': 'events',
    'events_update': 'events',
    'events_watch': 'events',
    'freebusy_query': 'freebusy',
    'settings_get': 'settings',
    'settings_list': 'settings',
    'settings_watch': 'settings',
}

RESOURCE_REGISTRY: dict[str, tuple[str, str]] = {
    'acl': ('lemma_connectors.google_calendar.resources.acl', 'GoogleCalendarAclResource'),
    'calendar_list': ('lemma_connectors.google_calendar.resources.calendar_list', 'GoogleCalendarCalendarListResource'),
    'calendars': ('lemma_connectors.google_calendar.resources.calendars', 'GoogleCalendarCalendarsResource'),
    'channels': ('lemma_connectors.google_calendar.resources.channels', 'GoogleCalendarChannelsResource'),
    'colors': ('lemma_connectors.google_calendar.resources.colors', 'GoogleCalendarColorsResource'),
    'events': ('lemma_connectors.google_calendar.resources.events', 'GoogleCalendarEventsResource'),
    'freebusy': ('lemma_connectors.google_calendar.resources.freebusy', 'GoogleCalendarFreebusyResource'),
    'settings': ('lemma_connectors.google_calendar.resources.settings', 'GoogleCalendarSettingsResource'),
}


def build_resource(client, resource_slug: str):
    """Lazily import and build a single resource client by slug."""
    module_path, class_name = RESOURCE_REGISTRY[resource_slug]
    module = importlib.import_module(module_path)
    return getattr(module, class_name)(client)


def build_resources(client):
    """Eagerly build all resource clients (backward-compatible)."""
    return {slug: build_resource(client, slug) for slug in RESOURCE_REGISTRY}
