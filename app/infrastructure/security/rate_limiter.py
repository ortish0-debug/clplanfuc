"""Легковесный in-memory rate limiter для защиты от DDoS и brute-force."""
import time


class InMemoryRateLimiter:
    """Rate limiter на базе памяти: {ip: [timestamp, timestamp, ...]}"""

    def __init__(self):
        self.requests: dict[str, list[float]] = {}

    def is_rate_limited(self, key: str, limit: int, window: int) -> bool:
        """
        Проверить, не превышен ли лимит для ключа (ip:endpoint).

        Args:
            key: Уникальный ключ (обычно ip:endpoint_group)
            limit: Максимум запросов
            window: Временное окно в секундах

        Returns:
            True если лимит превышен, иначе False
        """
        now = time.time()
        cutoff = now - window

        if key not in self.requests:
            self.requests[key] = []

        self.requests[key] = [ts for ts in self.requests[key] if ts > cutoff]

        if len(self.requests[key]) >= limit:
            return True

        self.requests[key].append(now)
        return False
