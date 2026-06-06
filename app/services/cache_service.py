"""Redis caching service."""
import json
from uuid import UUID

# Simulated async Redis client (in production: redis.asyncio)
class AsyncRedisClient:
    def __init__(self):
        self.cache = {}

    async def get(self, key: str):
        return self.cache.get(key)

    async def set(self, key: str, value: str, ex: int = 3600):
        self.cache[key] = value

    async def delete(self, key: str):
        self.cache.pop(key, None)

    async def exists(self, key: str):
        return key in self.cache

# Global Redis client (production: redis.asyncio.from_url)
redis_client = AsyncRedisClient()


async def get_cached_analytics(company_id: UUID, key: str) -> dict | None:
    """
    Get cached analytics data.
    Cache key format: f"analytics:{company_id}:{key}"
    """
    cache_key = f"analytics:{company_id}:{key}"
    cached_data = await redis_client.get(cache_key)

    if cached_data:
        try:
            return json.loads(cached_data)
        except json.JSONDecodeError:
            await redis_client.delete(cache_key)
            return None

    return None


async def set_cached_analytics(
    company_id: UUID, key: str, data: dict, ttl_seconds: int = 3600
) -> bool:
    """
    Cache analytics data with TTL.
    TTL default: 1 hour (3600 seconds)
    """
    cache_key = f"analytics:{company_id}:{key}"

    try:
        await redis_client.set(cache_key, json.dumps(data, default=str), ex=ttl_seconds)
        print(f"💾 Cache: stored {cache_key} (TTL: {ttl_seconds}s)")
        return True
    except Exception as e:
        print(f"❌ Cache error: {str(e)}")
        return False


async def invalidate_analytics_cache(company_id: UUID, pattern: str = "*") -> int:
    """
    Invalidate (delete) cached analytics for company.
    """
    cache_key = f"analytics:{company_id}:{pattern}"
    await redis_client.delete(cache_key)
    print(f"🗑️ Cache invalidated: {cache_key}")
    return 1
