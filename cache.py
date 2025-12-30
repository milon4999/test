# Redis Cache Implementation

import json
import hashlib
from typing import Optional, Any
from redis.asyncio import Redis, ConnectionPool
from redis.exceptions import RedisError
from config import settings


class CacheManager:
    """Redis cache manager"""
    
    def __init__(self):
        self.redis: Optional[Redis] = None
        self.enabled = settings.REDIS_ENABLED
        self.pool: Optional[ConnectionPool] = None
    
    async def connect(self):
        """Connect to Redis"""
        if not self.enabled:
            return
        
        try:
            self.pool = ConnectionPool.from_url(
                settings.REDIS_URL,
                encoding="utf-8",
                decode_responses=True,
                max_connections=10
            )
            self.redis = Redis(connection_pool=self.pool)
            # Test connection
            await self.redis.ping()
            print("✅ Redis connected successfully")
        except RedisError as e:
            print(f"❌ Redis connection failed: {e}")
            self.enabled = False
    
    async def disconnect(self):
        """Disconnect from Redis"""
        if self.redis:
            await self.redis.aclose()
            if self.pool:
                await self.pool.disconnect()
    
    def _generate_key(self, prefix: str, data: str) -> str:
        """Generate cache key from data"""
        hash_obj = hashlib.md5(data.encode())
        return f"{prefix}:{hash_obj.hexdigest()}"
    
    async def get(self, key: str) -> Optional[Any]:
        """Get value from cache"""
        if not self.enabled or not self.redis:
            return None
        
        try:
            value = await self.redis.get(key)
            if value:
                return json.loads(value)
        except (RedisError, json.JSONDecodeError) as e:
            print(f"Cache get error: {e}")
        return None
    
    async def set(self, key: str, value: Any, ttl: int = 3600):
        """Set value in cache with TTL"""
        if not self.enabled or not self.redis:
            return False
        
        try:
            serialized = json.dumps(value, default=str)
            await self.redis.setex(key, ttl, serialized)
            return True
        except (RedisError, TypeError) as e:
            print(f"Cache set error: {e}")
            return False
    
    async def delete(self, key: str):
        """Delete key from cache"""
        if not self.enabled or not self.redis:
            return
        
        try:
            await self.redis.delete(key)
        except RedisError as e:
            print(f"Cache delete error: {e}")
    
    async def clear(self, pattern: str = "*"):
        """Clear cache keys matching pattern"""
        if not self.enabled or not self.redis:
            return 0
        
        try:
            keys = []
            async for key in self.redis.scan_iter(match=pattern):
                keys.append(key)
            
            if keys:
                return await self.redis.delete(*keys)
            return 0
        except RedisError as e:
            print(f"Cache clear error: {e}")
            return 0
    
    async def get_scrape_cache(self, url: str) -> Optional[dict]:
        """Get cached scrape result"""
        key = self._generate_key("scrape", url)
        return await self.get(key)
    
    async def set_scrape_cache(self, url: str, data: dict):
        """Cache scrape result"""
        key = self._generate_key("scrape", url)
        await self.set(key, data, settings.CACHE_TTL_SCRAPE)
    
    async def get_list_cache(self, base_url: str, page: int, limit: int) -> Optional[list]:
        """Get cached list result"""
        cache_key = f"{base_url}:p{page}:l{limit}"
        key = self._generate_key("list", cache_key)
        return await self.get(key)
    
    async def set_list_cache(self, base_url: str, page: int, limit: int, data: list):
        """Cache list result"""
        cache_key = f"{base_url}:p{page}:l{limit}"
        key = self._generate_key("list", cache_key)
        await self.set(key, data, settings.CACHE_TTL_LIST)
    
    async def increment(self, key: str, amount: int = 1, ttl: Optional[int] = None) -> int:
        """Increment a counter"""
        if not self.enabled or not self.redis:
            return 0
        
        try:
            value = await self.redis.incrby(key, amount)
            if ttl and value == amount:  # First increment, set TTL
                await self.redis.expire(key, ttl)
            return value
        except RedisError as e:
            print(f"Cache increment error: {e}")
            return 0
    
    async def get_stats(self) -> dict:
        """Get cache statistics"""
        if not self.enabled or not self.redis:
            return {"enabled": False}
        
        try:
            info = await self.redis.info("stats")
            return {
                "enabled": True,
                "connected": True,
                "keyspace_hits": info.get("keyspace_hits", 0),
                "keyspace_misses": info.get("keyspace_misses", 0),
                "used_memory": info.get("used_memory_human", "N/A"),
            }
        except RedisError:
            return {"enabled": True, "connected": False}


# Global cache instance
cache = CacheManager()
