"""
In-Memory Rate Limiter (Zero Cost - No Redis Needed)
Sliding window rate limiting with automatic cleanup
"""

from collections import defaultdict, deque
from datetime import datetime, timedelta
from typing import Optional
import asyncio
import logging

logger = logging.getLogger(__name__)


class RateLimiter:
    """Sliding window rate limiter"""
    
    def __init__(self):
        """Initialize rate limiter"""
        self.requests = defaultdict(deque)  # user_id -> deque of timestamps
        self._lock = asyncio.Lock()
    
    async def is_allowed(
        self, 
        identifier: str, 
        limit: int = 60, 
        window_seconds: int = 60
    ) -> tuple[bool, dict]:
        """
        Check if request is allowed under rate limit
        
        Args:
            identifier: User ID, IP, or API key
            limit: Max requests allowed
            window_seconds: Time window in seconds
            
        Returns:
            (is_allowed, info_dict)
        """
        async with self._lock:
            now = datetime.utcnow()
            cutoff = now - timedelta(seconds=window_seconds)
            
            # Get request queue for this identifier
            queue = self.requests[identifier]
            
            # Remove old requests outside the window
            while queue and queue[0] < cutoff:
                queue.popleft()
            
            # Check if limit exceeded
            current_count = len(queue)
            is_allowed = current_count < limit
            
            if is_allowed:
                # Add current request
                queue.append(now)
            
            # Calculate reset time
            reset_time = queue[0] + timedelta(seconds=window_seconds) if queue else now
            remaining = max(0, limit - current_count - (1 if is_allowed else 0))
            
            return is_allowed, {
                "limit": limit,
                "remaining": remaining,
                "reset_at": reset_time.isoformat(),
                "retry_after_seconds": (reset_time - now).total_seconds() if not is_allowed else 0
            }
    
    async def cleanup_old_entries(self, max_age_hours: int = 24):
        """
        Remove entries for identifiers with no recent activity
        
        Args:
            max_age_hours: Remove entries older than this many hours
        """
        async with self._lock:
            cutoff = datetime.utcnow() - timedelta(hours=max_age_hours)
            
            # Find identifiers to remove
            to_remove = []
            for identifier, queue in self.requests.items():
                if not queue or queue[-1] < cutoff:
                    to_remove.append(identifier)
            
            # Remove them
            for identifier in to_remove:
                del self.requests[identifier]
            
            if to_remove:
                logger.info(f"Rate limiter cleanup: removed {len(to_remove)} inactive identifiers")
    
    def get_stats(self) -> dict:
        """Get rate limiter statistics"""
        return {
            "active_identifiers": len(self.requests),
            "total_tracked_requests": sum(len(q) for q in self.requests.values())
        }


# Global rate limiter instance
rate_limiter = RateLimiter()


# Background cleanup task
async def cleanup_task():
    """Periodic cleanup of old rate limit data"""
    while True:
        await asyncio.sleep(3600)  # Every hour
        await rate_limiter.cleanup_old_entries()


# Middleware for FastAPI
from fastapi import Request, HTTPException, status
from fastapi.responses import JSONResponse


async def rate_limit_middleware(request: Request, call_next):
    """
    Rate limiting middleware for FastAPI
    
    Usage in main.py:
        app.middleware("http")(rate_limit_middleware)
    """
    # Skip rate limiting for health checks
    if request.url.path in ["/health", "/metrics"]:
        return await call_next(request)
    
    # Use IP address as identifier (or user API key if available)
    identifier = request.client.host
    
    # Check if API key is provided (higher limit)
    api_key = request.headers.get("X-API-Key")
    if api_key:
        identifier = f"key:{api_key}"
        limit = 1000  # Higher limit for authenticated users
    else:
        limit = 60  # Lower limit for unauthenticated
    
    # Check rate limit
    is_allowed, info = await rate_limiter.is_allowed(
        identifier=identifier,
        limit=limit,
        window_seconds=60
    )
    
    if not is_allowed:
        logger.warning(f"Rate limit exceeded for {identifier}")
        return JSONResponse(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            content={
                "error": "Rate limit exceeded",
                "limit": info["limit"],
                "retry_after_seconds": int(info["retry_after_seconds"])
            },
            headers={
                "X-RateLimit-Limit": str(info["limit"]),
                "X-RateLimit-Remaining": "0",
                "X-RateLimit-Reset": info["reset_at"],
                "Retry-After": str(int(info["retry_after_seconds"]))
            }
        )
    
    # Add rate limit headers to response
    response = await call_next(request)
    response.headers["X-RateLimit-Limit"] = str(info["limit"])
    response.headers["X-RateLimit-Remaining"] = str(info["remaining"])
    response.headers["X-RateLimit-Reset"] = info["reset_at"]
    
    return response
