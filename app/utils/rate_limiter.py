"""
Simple token-bucket rate limiter for controlling scrape request concurrency.
"""

from __future__ import annotations

import asyncio
import time
from collections import defaultdict


class RateLimiter:
    """
    Token-bucket based rate limiter.

    Usage:
        limiter = RateLimiter(rate=10, burst=5)
        async with limiter:
            await make_request()
    """

    def __init__(self, rate: float = 10.0, burst: int = 5) -> None:
        """
        Args:
            rate: Maximum sustained requests per second.
            burst: Maximum instantaneous burst size.
        """
        self._rate = rate
        self._burst = burst
        self._tokens: dict[str, float] = defaultdict(lambda: burst)
        self._last_refill: dict[str, float] = defaultdict(time.monotonic)
        self._lock = asyncio.Lock()

    async def acquire(self, key: str = "default") -> None:
        """Wait until a token is available for the given key."""
        while True:
            async with self._lock:
                now = time.monotonic()
                elapsed = now - self._last_refill[key]
                self._tokens[key] = min(self._burst, self._tokens[key] + elapsed * self._rate)
                self._last_refill[key] = now

                if self._tokens[key] >= 1.0:
                    self._tokens[key] -= 1.0
                    return

                # Calculate wait time for next token
                wait = (1.0 - self._tokens[key]) / self._rate
            await asyncio.sleep(wait)

    async def __aenter__(self) -> "RateLimiter":
        await self.acquire()
        return self

    async def __aexit__(self, *args: object) -> None:
        pass  # token already consumed in acquire


# Singleton instance for global use
global_rate_limiter = RateLimiter(rate=10.0, burst=5)
