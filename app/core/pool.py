"""
HTTP Connection Pooling for Scrapers
Reuse connections for 50-100ms performance boost
"""

import aiohttp
import logging
from typing import Optional

logger = logging.getLogger(__name__)


class ConnectionPool:
    """Singleton connection pool for all HTTP requests"""
    
    _instance: Optional['ConnectionPool'] = None
    _session: Optional[aiohttp.ClientSession] = None
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance
    
    async def get_session(self) -> aiohttp.ClientSession:
        """
        Get or create aiohttp session with connection pooling
        
        Returns:
            Configured ClientSession
        """
        if self._session is None or self._session.closed:
            # Connection pooling configuration
            connector = aiohttp.TCPConnector(
                limit=100,  # Max 100 concurrent connections total
                limit_per_host=10,  # Max 10 per host
                ttl_dns_cache=300,  # Cache DNS for 5 minutes
                enable_cleanup_closed=True,
                force_close=False  # Keep connections alive
            )
            
            # Connection timeout configuration
            timeout = aiohttp.ClientTimeout(
                total=30,  # Total timeout
                connect=10,  # Connection timeout
                sock_read=20  # Read timeout
            )
            
            # Create session
            self._session = aiohttp.ClientSession(
                connector=connector,
                timeout=timeout,
                headers={
                    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
                }
            )
            
            logger.info("Created HTTP connection pool with 100 connections")
        
        return self._session
    
    async def close(self):
        """Close the session and cleanup connections"""
        if self._session and not self._session.closed:
            await self._session.close()
            logger.info("Closed HTTP connection pool")


# Global pool instance
pool = ConnectionPool()


async def fetch_html(url: str, **kwargs) -> str:
    """
    Fetch HTML using connection pool
    
    Args:
        url: URL to fetch
        **kwargs: Additional arguments for session.get()
        
    Returns:
        HTML content
    """
    session = await pool.get_session()
    
    async with session.get(url, **kwargs) as response:
        response.raise_for_status()
        return await response.text()


async def fetch_json(url: str, **kwargs) -> dict:
    """
    Fetch JSON using connection pool
    
    Args:
        url: URL to fetch
        **kwargs: Additional arguments for session.get()
        
    Returns:
        JSON data
    """
    session = await pool.get_session()
    
    async with session.get(url, **kwargs) as response:
        response.raise_for_status()
        return await response.json()


async def post_json(url: str, data: dict, **kwargs) -> dict:
    """
    POST JSON using connection pool
    
    Args:
        url: URL to post to
        data: JSON data to send
        **kwargs: Additional arguments for session.post()
        
    Returns:
        JSON response
    """
    session = await pool.get_session()
    
    async with session.post(url, json=data, **kwargs) as response:
        response.raise_for_status()
        return await response.json()
