from enum import Enum


class ScheduleFireDeliveryStatus(str, Enum):
    DEAD_LETTERED = "DEAD_LETTERED"
    DELIVERED = "DELIVERED"
    FAILED = "FAILED"
    FILTERED = "FILTERED"
    PROCESSING = "PROCESSING"
    RECEIVED = "RECEIVED"

    def __str__(self) -> str:
        return str(self.value)
