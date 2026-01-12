"""
Global Multi-Site Search
Search across multiple porn sites simultaneously
Killer feature from porn-app.com's Pro tier ($3.99/mo)
"""

from fastapi import Query
from typing import Optional
from app.core.exceptions import ScraperException
import asyncio
import logging

logger = logging.getLogger(__name__)


async def global_search(
    query: str,
    sites: Optional[list[str]] = None,
    limit_per_site: int = 10,
    max_sites: int = 30
) -> dict:
    """
    Search keyword across multiple sites simultaneously
    
    This is the killer feature from porn-app.com's Pro tier!
    Instead of making 30 API calls, users make ONE.
    
    Args:
        query: Search keyword (e.g., "blonde", "asian", "amateur")
        sites: List of site names to search (default: all available)
        limit_per_site: Max results per site (default: 10)
        max_sites: Maximum sites to search at once (default: 30)
        
    Returns:
        {
            "query": "blonde",
            "sites_searched": 4,
            "total_results": 38,
            "search_time_seconds": 2.4,
            "results": [
                {
                    "title": "...",
                    "url": "...",
                    "source_site": "xhamster",
                    "thumbnail_url": "...",
                    ...
                }
            ]
        }
    
    Example:
        /api/v1/search/global?query=blonde&sites=xhamster&sites=xnxx&limit_per_site=20
    """
    from time import time
    start_time = time()
    
    # Import scraper modules
    from app.scrapers import xhamster, xnxx, xvideos, masa49
    
    # Build scraper registry (until we have real registry)
    available_scrapers = {
        'xhamster': xhamster,
        'xnxx': xnxx,
        'xvideos': xvideos,
        'masa49': masa49
    }
    
    # Determine which sites to search
    if not sites:
        sites_to_search = list(available_scrapers.keys())
    else:
        sites_to_search = [s.lower() for s in sites if s.lower() in available_scrapers]
    
    # Limit to max_sites
    sites_to_search = sites_to_search[:max_sites]
    
    if not sites_to_search:
        return {
            "error": "No valid sites specified",
            "available_sites": list(available_scrapers.keys())
        }
    
    logger.info(f"Global search: '{query}' across {len(sites_to_search)} sites")
    
    # Build search tasks
    tasks = []
    for site_name in sites_to_search:
        scraper_module = available_scrapers[site_name]
        
        # Build search URL for each site
        search_url = _build_search_url(site_name, query, scraper_module)
        
        if search_url:
            task = _search_site(
                site_name=site_name,
                scraper_module=scraper_module,
                search_url=search_url,
                limit=limit_per_site
            )
            tasks.append(task)
    
    # Execute all searches concurrently (ZERO-COST POWER!)
    results_by_site = await asyncio.gather(*tasks, return_exceptions=True)
    
    # Combine results
    combined_results = []
    successful_sites = 0
    
    for site_name, site_results in zip(sites_to_search, results_by_site):
        if isinstance(site_results, Exception):
            logger.error(f"Search failed for {site_name}: {site_results}")
            continue
        
        if isinstance(site_results, list):
            successful_sites += 1
            for item in site_results:
                # Add source site to each result
                item["source_site"] = site_name
                combined_results.append(item)
    
    search_time = time() - start_time
    
    logger.info(
        f"Global search complete: {len(combined_results)} results "
        f"from {successful_sites}/{len(sites_to_search)} sites in {search_time:.2f}s"
    )
    
    return {
        "query": query,
        "sites_searched": successful_sites,
        "sites_requested": len(sites_to_search),
        "total_results": len(combined_results),
        "search_time_seconds": round(search_time, 2),
        "results": combined_results
    }


def _build_search_url(site_name: str, query: str, scraper_module) -> str:
    """
    Build search URL for each site
    Each site has different search URL patterns
    """
    query_encoded = query.replace(" ", "+")
    
    # Site-specific search URL patterns
    search_patterns = {
        "xhamster": f"https://xhamster.com/search/{query_encoded}",
        "xnxx": f"https://www.xnxx.com/search/{query_encoded}",
        "xvideos": f"https://www.xvideos.com/?k={query_encoded}",
        "masa49": f"https://masa49.org/search/{query_encoded}/"
    }
    
    return search_patterns.get(site_name)


async def _search_site(
    site_name: str,
    scraper_module,
    search_url: str,
    limit: int
) -> list:
    """
    Search a single site and return results
    
    Uses existing list_videos() function from each scraper
    """
    try:
        # Check cache first (ZERO-COST OPTIMIZATION)
        from simple_cache import cache
        cache_key = f"search:{site_name}:{search_url}:{limit}"
        
        cached = await cache.get(cache_key)
        if cached:
            logger.info(f"⚡ Cache HIT for search on {site_name}")
            return cached
        
        # Use scraper's list_videos function
        results = await scraper_module.list_videos(
            base_url=search_url,
            page=1,
            limit=limit
        )
        
        # Cache search results for 10 minutes
        await cache.set(cache_key, results, ttl_seconds=600)
        
        return results
        
    except Exception as e:
        logger.error(f"Search error for {site_name}: {e}")
        return []


# Additional helper: Get trending across all sites
async def global_trending(
    sites: Optional[list[str]] = None,
    limit_per_site: int = 10
) -> dict:
    """
    Get trending/popular videos from all sites
    
    Similar to global search but uses trending pages
    """
    from app.scrapers import xhamster, xnxx, xvideos, masa49
    
    available_scrapers = {
        'xhamster': (xhamster, "https://xhamster.com/trending"),
        'xnxx': (xnxx, "https://www.xnxx.com/hits"),
        'xvideos': (xvideos, "https://www.xvideos.com/"),
        'masa49': (masa49, "https://masa49.org/")
    }
    
    if not sites:
        sites = list(available_scrapers.keys())
    
    tasks = []
    for site_name in sites:
        if site_name in available_scrapers:
            scraper, trending_url = available_scrapers[site_name]
            task = _search_site(site_name, scraper, trending_url, limit_per_site)
            tasks.append((site_name, task))
    
    results = await asyncio.gather(*[t for _, t in tasks], return_exceptions=True)
    
    combined = []
    for (site_name, _), site_results in zip(tasks, results):
        if isinstance(site_results, list):
            for item in site_results:
                item["source_site"] = site_name
                combined.append(item)
    
    return {
        "type": "trending",
        "sites": len([r for r in results if isinstance(r, list)]),
        "total_results": len(combined),
        "results": combined
    }
