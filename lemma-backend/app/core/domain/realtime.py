"""Port for transient realtime channels used by SSE and websocket APIs."""

from collections.abc import AsyncIterator, Sequence
from contextlib import AbstractAsyncContextManager
from typing import Protocol


class RealtimeChannel(Protocol):
    """Publish and subscribe to ephemeral fan-out messages.

    Domain events use the durable Redis Streams message bus instead. Realtime
    channels intentionally provide no replay or delivery guarantee.
    """

    async def publish(self, channel: str, message: object) -> None:
        """Publish a transient message to all current subscribers."""
        ...

    def subscribe(
        self, channels: Sequence[str]
    ) -> AbstractAsyncContextManager[AsyncIterator[str | bytes]]:
        """Lease a subscription until the returned context exits."""
        ...
