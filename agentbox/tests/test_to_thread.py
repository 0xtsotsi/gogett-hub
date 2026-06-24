from __future__ import annotations

import warnings

import uvloop

from agentbox.to_thread import run_sync


def test_run_sync_does_not_emit_asyncio_iscoroutinefunction_warning() -> None:
    async def main() -> str:
        return await run_sync(lambda: "ok")

    loop = uvloop.new_event_loop()
    loop.set_debug(True)
    try:
        with warnings.catch_warnings():
            warnings.filterwarnings(
                "error",
                message=".*asyncio\\.iscoroutinefunction.*",
                category=DeprecationWarning,
            )
            result = loop.run_until_complete(main())
    finally:
        loop.close()

    assert result == "ok"
