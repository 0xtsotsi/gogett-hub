from __future__ import annotations

from pathlib import Path

from lemma_connectors.core.auth import CredentialTypes
from lemma_connectors.core.client import BaseInfoClient, BaseIntegrationClient
from lemma_connectors.google_sheets import resources as _resources

BASE_URL = "https://sheets.googleapis.com"
CLIENT_MODULE_PATH = "lemma_connectors.google_sheets.generated.client.client"
METADATA_PATH = (
    Path(__file__).resolve().parent / "generated" / "openapi_metadata.json"
)


class GoogleSheetsInfoClient(BaseInfoClient):
    def __init__(self):
        super().__init__(
            metadata_path=METADATA_PATH,
            base_url=BASE_URL,
            client_module_path=CLIENT_MODULE_PATH,
        )
        self.register_resource_registry(_resources)


class GoogleSheetsClient(BaseIntegrationClient):
    def __init__(self, *, credentials: CredentialTypes):
        super().__init__(
            metadata_path=METADATA_PATH,
            base_url=BASE_URL,
            client_module_path=CLIENT_MODULE_PATH,
            credentials=credentials,
        )
        self.register_resource_registry(_resources)
