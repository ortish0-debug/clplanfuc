"""Легковесный in-memory rate limiter для защиты от DDoS и brute-force."""
import time


class InMemoryRateLimiter:
    """Rate limiter на базе памяти: {ip: [timestamp, timestamp, ...]}"""

    def __init__(self):
        self.requests: dict[str, list[float]] = {}

    def is_rate_limited(self, client_ip: str, limit: int, window: int) -> bool:
        """
        Проверить, не превышен ли лимит для клиента.

        Args:
            client_ip: IP адрес клиента
            limit: Максимум запросов
            window: Временное окно в секундах

        Returns:
            True если лимит превышен, иначе False
        """
        now = time.time()
        cutoff = now - window

        if client_ip not in self.requests:
            self.requests[client_ip] = []

        self.requests[client_ip] = [ts for ts in self.requests[client_ip] if ts > cutoff]

        if len(self.requests[client_ip]) >= limit:
            return True

        self.requests[client_ip].append(now)
        return False
