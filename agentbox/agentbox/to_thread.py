from __future__ import annotations

from collections.abc import Callable
from functools import partial
from typing import ParamSpec, TypeVar

from anyio import to_thread

P = ParamSpec("P")
T = TypeVar("T")


async def run_sync(func: Callable[P, T], /, *args: P.args, **kwargs: P.kwargs) -> T:
    if kwargs:
        return await to_thread.run_sync(partial(func, *args, **kwargs))
    return await to_thread.run_sync(func, *args)
