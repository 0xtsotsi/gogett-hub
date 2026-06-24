from __future__ import annotations

import importlib

OPERATION_TO_RESOURCE: dict[str, str] = {
    'about_get': 'about',
    'changes_get_start_page_token': 'changes',
    'changes_list': 'changes',
    'changes_watch': 'changes',
    'channels_stop': 'channels',
    'comments_create': 'comments',
    'comments_delete': 'comments',
    'comments_get': 'comments',
    'comments_list': 'comments',
    'comments_update': 'comments',
    'drives_create': 'drives',
    'drives_delete': 'drives',
    'drives_get': 'drives',
    'drives_hide': 'drives',
    'drives_list': 'drives',
    'drives_unhide': 'drives',
    'drives_update': 'drives',
    'files_copy': 'files',
    'files_create': 'files',
    'files_delete': 'files',
    'files_empty_trash': 'files',
    'files_export': 'files',
    'files_generate_ids': 'files',
    'files_get': 'files',
    'files_list': 'files',
    'files_list_labels': 'files',
    'files_modify_labels': 'files',
    'files_update': 'files',
    'files_watch': 'files',
    'permissions_create': 'permissions',
    'permissions_delete': 'permissions',
    'permissions_get': 'permissions',
    'permissions_list': 'permissions',
    'permissions_update': 'permissions',
    'replies_create': 'replies',
    'replies_delete': 'replies',
    'replies_get': 'replies',
    'replies_list': 'replies',
    'replies_update': 'replies',
    'revisions_delete': 'revisions',
    'revisions_get': 'revisions',
    'revisions_list': 'revisions',
    'revisions_update': 'revisions',
    'teamdrives_create': 'teamdrives',
    'teamdrives_delete': 'teamdrives',
    'teamdrives_get': 'teamdrives',
    'teamdrives_list': 'teamdrives',
    'teamdrives_update': 'teamdrives',
}

RESOURCE_REGISTRY: dict[str, tuple[str, str]] = {
    'about': ('lemma_connectors.google_drive.resources.about', 'GoogleDriveAboutResource'),
    'changes': ('lemma_connectors.google_drive.resources.changes', 'GoogleDriveChangesResource'),
    'channels': ('lemma_connectors.google_drive.resources.channels', 'GoogleDriveChannelsResource'),
    'comments': ('lemma_connectors.google_drive.resources.comments', 'GoogleDriveCommentsResource'),
    'drives': ('lemma_connectors.google_drive.resources.drives', 'GoogleDriveDrivesResource'),
    'files': ('lemma_connectors.google_drive.resources.files', 'GoogleDriveFilesResource'),
    'permissions': ('lemma_connectors.google_drive.resources.permissions', 'GoogleDrivePermissionsResource'),
    'replies': ('lemma_connectors.google_drive.resources.replies', 'GoogleDriveRepliesResource'),
    'revisions': ('lemma_connectors.google_drive.resources.revisions', 'GoogleDriveRevisionsResource'),
    'teamdrives': ('lemma_connectors.google_drive.resources.teamdrives', 'GoogleDriveTeamdrivesResource'),
}


def build_resource(client, resource_slug: str):
    """Lazily import and build a single resource client by slug."""
    module_path, class_name = RESOURCE_REGISTRY[resource_slug]
    module = importlib.import_module(module_path)
    return getattr(module, class_name)(client)


def build_resources(client):
    """Eagerly build all resource clients (backward-compatible)."""
    return {slug: build_resource(client, slug) for slug in RESOURCE_REGISTRY}
