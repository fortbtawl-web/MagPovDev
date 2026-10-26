from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import TypeVar

from app.vpn.base import VPNUnavailableError

T = TypeVar("T")
log = logging.getLogger("spirittvpn.vpn")


async def with_retries(
    op: Callable[[], Awaitable[T]], *, attempts: int = 3, base_delay: float = 0.5, op_name: str = "vpn_op"
) -> T:
    """Retries only transient VPN errors, with exponential backoff and a hard attempt limit."""
    for attempt in range(1, attempts + 1):
        try:
            return await op()
        except VPNUnavailableError as exc:
            if attempt == attempts:
                log.warning("%s failed after %d attempts: %s", op_name, attempts, type(exc).__name__)
                raise
            delay = base_delay * 2 ** (attempt - 1)
            log.info("%s transient failure (attempt %d/%d), retry in %.1fs", op_name, attempt, attempts, delay)
            await asyncio.sleep(delay)
    raise AssertionError("unreachable")
