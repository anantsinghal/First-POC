from __future__ import annotations

import asyncio
from typing import Awaitable, Callable, TypeVar

T = TypeVar("T")


async def run_offloaded(coro_factory: Callable[[], Awaitable[T]]) -> T:
    """Give a sync-work-heavy scan coroutine its own event loop on a worker thread.

    Scans over CSV/SQLite/demo/REST-Salesforce sources are almost entirely synchronous CPU/SQLite
    work; running them directly on the request-handling loop starves every other coroutine (health
    checks, dashboard polls) for the scan's whole duration. asyncio.to_thread hands the work to a
    worker thread, and asyncio.run gives that thread its own fresh loop to drive the coroutine on,
    so the server's main loop stays live for the rest of the scan.
    """
    return await asyncio.to_thread(lambda: asyncio.run(coro_factory()))
