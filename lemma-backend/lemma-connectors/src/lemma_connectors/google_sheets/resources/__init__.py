from __future__ import annotations

import importlib

OPERATION_TO_RESOURCE: dict[str, str] = {
    'spreadsheets_batch_update': 'spreadsheets',
    'spreadsheets_create': 'spreadsheets',
    'spreadsheets_developer_metadata_get': 'spreadsheets_developer_metadata',
    'spreadsheets_developer_metadata_search': 'spreadsheets_developer_metadata',
    'spreadsheets_get': 'spreadsheets',
    'spreadsheets_get_by_data_filter': 'spreadsheets',
    'spreadsheets_sheets_copy_to': 'spreadsheets_sheets',
    'spreadsheets_values_append': 'spreadsheets_values',
    'spreadsheets_values_batch_clear': 'spreadsheets_values',
    'spreadsheets_values_batch_clear_by_data_filter': 'spreadsheets_values',
    'spreadsheets_values_batch_get': 'spreadsheets_values',
    'spreadsheets_values_batch_get_by_data_filter': 'spreadsheets_values',
    'spreadsheets_values_batch_update': 'spreadsheets_values',
    'spreadsheets_values_batch_update_by_data_filter': 'spreadsheets_values',
    'spreadsheets_values_clear': 'spreadsheets_values',
    'spreadsheets_values_get': 'spreadsheets_values',
    'spreadsheets_values_update': 'spreadsheets_values',
}

RESOURCE_REGISTRY: dict[str, tuple[str, str]] = {
    'spreadsheets': ('lemma_connectors.google_sheets.resources.spreadsheets', 'GoogleSheetsSpreadsheetsResource'),
    'spreadsheets_developer_metadata': ('lemma_connectors.google_sheets.resources.spreadsheets_developer_metadata', 'GoogleSheetsSpreadsheetsDeveloperMetadataResource'),
    'spreadsheets_sheets': ('lemma_connectors.google_sheets.resources.spreadsheets_sheets', 'GoogleSheetsSpreadsheetsSheetsResource'),
    'spreadsheets_values': ('lemma_connectors.google_sheets.resources.spreadsheets_values', 'GoogleSheetsSpreadsheetsValuesResource'),
}


def build_resource(client, resource_slug: str):
    """Lazily import and build a single resource client by slug."""
    module_path, class_name = RESOURCE_REGISTRY[resource_slug]
    module = importlib.import_module(module_path)
    return getattr(module, class_name)(client)


def build_resources(client):
    """Eagerly build all resource clients (backward-compatible)."""
    return {slug: build_resource(client, slug) for slug in RESOURCE_REGISTRY}
