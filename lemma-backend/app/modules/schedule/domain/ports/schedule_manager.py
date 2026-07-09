from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from app.modules.connectors.domain.account import AccountEntity
from app.modules.connectors.domain.connector_trigger import ConnectorTriggerEntity


class ExternalScheduleManager(ABC):
    """Interface for managing provider-side schedule subscriptions."""

    @abstractmethod
    async def create_schedule(
        self,
        account: AccountEntity,
        app_trigger: ConnectorTriggerEntity,
        config: dict[str, Any],
    ) -> str:
        """
        Create a schedule subscription on the external platform.

        Args:
            account: The user account with credentials.
            app_trigger: The connector event definition.
            config: Provider configuration for the subscription.

        Returns:
            The provider_id of the created subscription.
        """
        pass

    @abstractmethod
    async def delete_schedule(self, account: AccountEntity, provider_id: str) -> None:
        """
        Delete a schedule subscription from the external platform.

        Args:
            account: The user account.
            provider_id: The ID of the subscription on the external platform.
        """
        pass

    @abstractmethod
    async def get_schedule(
        self, account: AccountEntity, provider_id: str
    ) -> Any | None:
        """
        Get schedule subscription details from the external platform.

        Args:
            account: The user account.
            provider_id: The ID of the trigger on the external platform.

        Returns:
            Schedule subscription details or None if not found.
        """
        pass
