from lemma_connectors.core.auth import (
    ApiKeyCredentials,
    NoAuthCredentials,
    OAuth2Credentials,
)

__all__ = [
    "ApiKeyCredentials",
    "GmailClient",
    "GmailInfoClient",
    "GoogleCalendarClient",
    "GoogleCalendarInfoClient",
    "GoogleDocsClient",
    "GoogleDocsInfoClient",
    "GoogleDriveClient",
    "GoogleDriveInfoClient",
    "GoogleSheetsClient",
    "GoogleSheetsInfoClient",
    "JiraClient",
    "JiraInfoClient",
    "NoAuthCredentials",
    "OAuth2Credentials",
    "SlackClient",
    "SlackInfoClient",
]

_LAZY_CLIENT_IMPORTS: dict[str, tuple[str, str]] = {
    "GmailClient": ("lemma_connectors.gmail.client", "GmailClient"),
    "GmailInfoClient": ("lemma_connectors.gmail.client", "GmailInfoClient"),
    "GoogleCalendarClient": ("lemma_connectors.google_calendar.client", "GoogleCalendarClient"),
    "GoogleCalendarInfoClient": ("lemma_connectors.google_calendar.client", "GoogleCalendarInfoClient"),
    "GoogleDocsClient": ("lemma_connectors.google_docs.client", "GoogleDocsClient"),
    "GoogleDocsInfoClient": ("lemma_connectors.google_docs.client", "GoogleDocsInfoClient"),
    "GoogleDriveClient": ("lemma_connectors.google_drive.client", "GoogleDriveClient"),
    "GoogleDriveInfoClient": ("lemma_connectors.google_drive.client", "GoogleDriveInfoClient"),
    "GoogleSheetsClient": ("lemma_connectors.google_sheets.client", "GoogleSheetsClient"),
    "GoogleSheetsInfoClient": ("lemma_connectors.google_sheets.client", "GoogleSheetsInfoClient"),
    "JiraClient": ("lemma_connectors.jira.client", "JiraClient"),
    "JiraInfoClient": ("lemma_connectors.jira.client", "JiraInfoClient"),
    "SlackClient": ("lemma_connectors.slack.client", "SlackClient"),
    "SlackInfoClient": ("lemma_connectors.slack.client", "SlackInfoClient"),
}


def __getattr__(name: str):
    import importlib

    target = _LAZY_CLIENT_IMPORTS.get(name)
    if target is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module_path, class_name = target
    module = importlib.import_module(module_path)
    value = getattr(module, class_name)
    globals()[name] = value
    return value
