"""
In-Memory Cache with TTL (Zero Cost - No Redis Needed)
Provides LRU eviction and automatic expiration
"""

from collections import OrderedDict
from datetime import datetime, timedelta
from typing import Any, Optional
import asyncio
import logging

logger = logging.getLogger(__name__)


class SimpleCache:
    """Thread-safe in-memory cache with TTL and LRU eviction"""
    
    def __init__(self, max_size: int = 10000):
        """
        Initialize cache
        
        Args:
            max_size: Maximum number of items to store
        """
        self.cache = OrderedDict()
        self.max_size = max_size
        self._lock = asyncio.Lock()
        self._hits = 0
        self._misses = 0
    
    async def set(self, key: str, value: Any, ttl_seconds: int = 3600):
        """
        Set a value in cache with TTL
        
        Args:
            key: Cache key
            value: Value to store
            ttl_seconds: Time to live in seconds (default: 1 hour)
        """
        async with self._lock:
            # Remove oldest item if at capacity
            if len(self.cache) >= self.max_size and key not in self.cache:
                self.cache.popitem(last=False)
            
            # Store with expiration
            self.cache[key] = {
                "value": value,
                "expires_at": datetime.utcnow() + timedelta(seconds=ttl_seconds),
                "created_at": datetime.utcnow()
            }
            
            # Move to end (most recently used)
            self.cache.move_to_end(key)
            
            logger.debug(f"Cache SET: {key} (TTL: {ttl_seconds}s)")
    
    async def get(self, key: str) -> Optional[Any]:
        """
        Get a value from cache
        
        Args:
            key: Cache key
            
        Returns:
            Cached value or None if not found/expired
        """
        async with self._lock:
            if key not in self.cache:
                self._misses += 1
                logger.debug(f"Cache MISS: {key}")
                return None
            
            item = self.cache[key]
            
            # Check expiration
            if datetime.utcnow() > item["expires_at"]:
                del self.cache[key]
                self._misses += 1
                logger.debug(f"Cache EXPIRED: {key}")
                return None
            
            # Move to end (most recently used)
            self.cache.move_to_end(key)
            
            self._hits += 1
            logger.debug(f"Cache HIT: {key}")
            return item["value"]
    
    async def delete(self, key: str):
        """Delete a key from cache"""
        async with self._lock:
            if key in self.cache:
                del self.cache[key]
                logger.debug(f"Cache DELETE: {key}")
    
    async def clear(self):
        """Clear all cache entries"""
        async with self._lock:
            self.cache.clear()
            self._hits = 0
            self._misses = 0
            logger.info("Cache CLEARED")
    
    async def cleanup_expired(self):
        """Remove all expired entries"""
        async with self._lock:
            now = datetime.utcnow()
            expired_keys = [
                key for key, item in self.cache.items()
                if now > item["expires_at"]
            ]
            
            for key in expired_keys:
                del self.cache[key]
            
            if expired_keys:
                logger.info(f"Cache cleanup: removed {len(expired_keys)} expired entries")
    
    def get_stats(self) -> dict:
        """Get cache statistics"""
        total_requests = self._hits + self._misses
        hit_rate = (self._hits / total_requests * 100) if total_requests > 0 else 0
        
        return {
            "size": len(self.cache),
            "max_size": self.max_size,
            "hits": self._hits,
            "misses": self._misses,
            "hit_rate_percent": round(hit_rate, 2),
            "total_requests": total_requests
        }
    
    async def get_or_set(
        self, 
        key: str, 
        factory, 
        ttl_seconds: int = 3600
    ) -> Any:
        """
        Get value from cache, or compute and cache it if not found
        
        Args:
            key: Cache key
            factory: Async function to compute value if not cached
            ttl_seconds: TTL for newly cached value
            
        Returns:
            Cached or computed value
        """
        # Try to get from cache
        value = await self.get(key)
        if value is not None:
            return value
        
        # Compute value
        value = await factory() if asyncio.iscoroutinefunction(factory) else factory()
        
        # Cache it
        await self.set(key, value, ttl_seconds)
        
        return value


# Global cache instance
cache = SimpleCache(max_size=50000)


# Background task to cleanup expired entries
async def cleanup_task():
    """Periodic cleanup of expired cache entries"""
    while True:
        await asyncio.sleep(300)  # Every 5 minutes
        await cache.cleanup_expired()
