from enum import Enum


class PodProvisioningStatus(str, Enum):
    FAILED = "FAILED"
    PROVISIONING = "PROVISIONING"
    READY = "READY"
    UNKNOWN = "UNKNOWN"

    def __str__(self) -> str:
        return str(self.value)
