"""
Core utilities package

Modules:
- cache: In-memory LRU caching  
- pool: HTTP connection pooling
- limiter: Rate limiting
"""

from .cache import cache, cleanup_task as cache_cleanup
from .pool import pool, fetch_html
from .limiter import rate_limit_middleware, cleanup_task as rate_limit_cleanup

__all__ = [
    'cache', 'cache_cleanup',
    'pool', 'fetch_html',
    'rate_limit_middleware', 'rate_limit_cleanup'
]
