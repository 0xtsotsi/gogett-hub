from enum import Enum


class BundleSourceKind(str, Enum):
    GITHUB = "GITHUB"
    URL = "URL"

    def __str__(self) -> str:
        return str(self.value)
