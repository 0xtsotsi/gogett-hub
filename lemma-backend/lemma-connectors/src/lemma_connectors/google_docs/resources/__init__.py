from __future__ import annotations

import importlib

OPERATION_TO_RESOURCE: dict[str, str] = {
    'documents_batch_update': 'documents',
    'documents_create': 'documents',
    'documents_get': 'documents',
}

RESOURCE_REGISTRY: dict[str, tuple[str, str]] = {
    'documents': ('lemma_connectors.google_docs.resources.documents', 'GoogleDocsDocumentsResource'),
}


def build_resource(client, resource_slug: str):
    """Lazily import and build a single resource client by slug."""
    module_path, class_name = RESOURCE_REGISTRY[resource_slug]
    module = importlib.import_module(module_path)
    return getattr(module, class_name)(client)


def build_resources(client):
    """Eagerly build all resource clients (backward-compatible)."""
    return {slug: build_resource(client, slug) for slug in RESOURCE_REGISTRY}
