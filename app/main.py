from __future__ import annotations

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, HttpUrl, field_validator
from typing import Any, Optional

# Scrapers from app package
from app.scrapers import masa49, xhamster, xnxx, xvideos
import json
import os
import asyncio
import logging

# Zero-cost optimizations from app.core
from app.core import cache, cache_cleanup, pool, fetch_html, rate_limit_middleware, rate_limit_cleanup

logging.basicConfig(level=logging.INFO)

# Create FastAPI app
app = FastAPI(
    title="Scraper API - Optimized Version",
    description="🚀 Zero-cost optimized scraper API with caching, connection pooling, and rate limiting",
    version="2.0.0"
)

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Add rate limiting middleware
app.middleware("http")(rate_limit_middleware)


@app.on_event("startup")
async def startup_event():
    """Start background tasks on startup"""
    # Start cache cleanup task
    asyncio.create_task(cache_cleanup())
    # Start rate limiter cleanup task
    asyncio.create_task(rate_limit_cleanup())
    logging.info("✅ Started background cleanup tasks")
    logging.info("✅ Zero-cost optimizations enabled")


@app.on_event("shutdown")
async def shutdown_event():
    """Cleanup on shutdown"""
    # Close HTTP connection pool
    await pool.close()
    logging.info("✅ Closed HTTP connection pool")


class ScrapeRequest(BaseModel):
    url: HttpUrl

    @field_validator("url")
    @classmethod
    def validate_domain(cls, v: HttpUrl) -> HttpUrl:
        host = (v.host or "").lower()
        if (
            host.endswith("xhamster.com")
            or host.endswith("masa49.org")
            or host.endswith("xnxx.com")
            or host.endswith("xvideos.com")
        ):
            return v
        raise ValueError("Only xhamster.com, masa49.org, xnxx.com and xvideos.com URLs are allowed")


class ScrapeResponse(BaseModel):
    url: HttpUrl
    title: str | None = None
    description: str | None = None
    thumbnail_url: str | None = None
    duration: str | None = None
    views: str | None = None
    uploader_name: str | None = None
    category: str | None = None
    tags: list[str] = []
    related_videos: list[dict[str, Any]] = []
    video: dict[str, Any] | None = None
    preview_url: str | None = None # Preview video/sprite URL


class ListItem(BaseModel):
    url: HttpUrl
    title: str | None = None
    thumbnail_url: str | None = None
    duration: str | None = None
    views: str | None = None
    uploader_name: str | None = None
    uploader_url: str | None = None
    uploader_avatar_url: str | None = None
    upload_time: str | None = None
    category: str | None = None
    tags: list[str] = []


class CategoryItem(BaseModel):
    name: str
    url: str
    video_count: int


class ListRequest(BaseModel):
    base_url: HttpUrl

    @field_validator("base_url")
    @classmethod
    def validate_domain(cls, v: HttpUrl) -> HttpUrl:
        host = (v.host or "").lower()
        if (
            host.endswith("xhamster.com")
            or host.endswith("masa49.org")
            or host.endswith("xnxx.com")
            or host.endswith("xvideos.com")
        ):
            return v
        raise ValueError("Only xhamster.com, masa49.org, xnxx.com and xvideos.com base_url are allowed")


async def _scrape_dispatch(url: str, host: str) -> dict[str, object]:
    if xhamster.can_handle(host):
        return await xhamster.scrape(url)
    if masa49.can_handle(host):
        return await masa49.scrape(url)
    if xnxx.can_handle(host):
        return await xnxx.scrape(url)
    if xvideos.can_handle(host):
        return await xvideos.scrape(url)
    raise HTTPException(status_code=400, detail="Unsupported host")


async def _list_dispatch(base_url: str, host: str, page: int, limit: int) -> list[dict[str, object]]:
    if xhamster.can_handle(host):
        return await xhamster.list_videos(base_url=base_url, page=page, limit=limit)
    if masa49.can_handle(host):
        return await masa49.list_videos(base_url=base_url, page=page, limit=limit)
    if xnxx.can_handle(host):
        return await xnxx.list_videos(base_url=base_url, page=page, limit=limit)
    if xvideos.can_handle(host):
        return await xvideos.list_videos(base_url=base_url, page=page, limit=limit)
    raise HTTPException(status_code=400, detail="Unsupported host")


async def _crawl_dispatch(
    base_url: str,
    host: str,
    start_page: int,
    max_pages: int,
    per_page_limit: int,
    max_items: int,
) -> list[dict[str, object]]:
    if xhamster.can_handle(host):
        return await xhamster.crawl_videos(
            base_url=base_url,
            start_page=start_page,
            max_pages=max_pages,
            per_page_limit=per_page_limit,
            max_items=max_items,
        )
    raise HTTPException(status_code=400, detail="Unsupported host")


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/scrape", response_model=ScrapeResponse)
async def scrape(url: str) -> ScrapeResponse:
    req = ScrapeRequest(url=url)
    
    # Check cache first (ZERO COST OPTIMIZATION)
    cache_key = f"scrape:{str(req.url)}"
    cached_result = await cache.get(cache_key)
    if cached_result:
        logging.info(f"⚡ Cache HIT for {url}")
        return ScrapeResponse(**cached_result)
    
    # Cache miss - scrape the URL
    try:
        data = await _scrape_dispatch(str(req.url), req.url.host or "")
    except httpx.HTTPStatusError as e:
        raise HTTPException(status_code=e.response.status_code, detail="Upstream returned error") from e
    except Exception as e:
        raise HTTPException(status_code=502, detail="Failed to fetch url") from e
    
    # Store in cache for 1 hour (3600 seconds)
    await cache.set(cache_key, data, ttl_seconds=3600)
    logging.info(f"💾 Cached result for {url}")
    
    return ScrapeResponse(**data)


@app.get("/list", response_model=list[ListItem], response_model_exclude_unset=True)
async def list_videos(base_url: str, page: int = 1, limit: int = 20) -> list[ListItem]:
    req = ListRequest(base_url=base_url)
    if page < 1:
        page = 1
    if limit < 1:
        limit = 1
    if limit > 60:
        limit = 60

    # Check cache (ZERO COST OPTIMIZATION)
    cache_key = f"list:{str(req.base_url)}:p{page}:l{limit}"
    cached_items = await cache.get(cache_key)
    if cached_items:
        logging.info(f"⚡ Cache HIT for list {base_url} page {page}")
        return [ListItem(**it) for it in cached_items]

    try:
        items = await _list_dispatch(str(req.base_url), req.base_url.host or "", page, limit)
    except httpx.HTTPStatusError as e:
        raise HTTPException(status_code=e.response.status_code, detail="Upstream returned error") from e
    except Exception as e:
        raise HTTPException(status_code=502, detail="Failed to fetch url") from e
    
    # Cache for 15 minutes (900 seconds)
    await cache.set(cache_key, items, ttl_seconds=900)
    logging.info(f"💾 Cached list for {base_url} page {page}")
    
    return [ListItem(**it) for it in items]
    
    # We must explicitly construct ListItem without defaults for missing keys to ensure exclude_unset works?
    # Actually, Pydantic v2 (or v1) behavior:
    # If we pass a dict to **it:
    # If the dict lacks a key, and the model has a default, the default IS set.
    # So exclude_unset=True will NOT exclude it if it has a default.
    # We need to manually construct the list of dicts or modify how we return.
    
    # However, if we return the list of dicts directly and change response_model to use exclude_unset?
    # No, FastAPI converts return value to Pydantic models.
    
    # Wait, if I simply return List[dict] instead of List[ListItem], I lose validation but gain total control.
    # BUT the user asked to remove them.
    
    # Alternative: Modify ListItem defaults? No, other scrapers need defaults.
    # Alternative: construct ListItem with only present keys?
    # If I do `ListItem(title="foo")`, `tags` becomes `[]` (default). It is considered "set" by default values?
    # In Pydantic v1: `exclude_unset` excludes fields that were NOT passed to __init__? 
    # Yes: "fields which were not explicitly set when creating the model".
    # So if I do `ListItem(**{'title': 'foo'})`, `tags` takes default `[]`.
    # Is that "set"? No, it's default. So `exclude_unset=True` SHOULD work.
    
    return [ListItem(**it) for it in items]


@app.get("/crawl", response_model=list[ListItem])
async def crawl_videos(
    base_url: str,
    start_page: int = 1,
    max_pages: int = 5,
    per_page_limit: int = 0,
    max_items: int = 500,
) -> list[ListItem]:
    req = ListRequest(base_url=base_url)

    if start_page < 1:
        start_page = 1
    if max_pages < 1:
        max_pages = 1
    if max_pages > 20:
        max_pages = 20
    if per_page_limit < 0:
        per_page_limit = 0
    if per_page_limit > 200:
        per_page_limit = 200
    if max_items < 1:
        max_items = 1
    if max_items > 1000:
        max_items = 1000

    try:
        items = await _crawl_dispatch(
            str(req.base_url),
            req.base_url.host or "",
            start_page,
            max_pages,
            per_page_limit,
            max_items,
        )
    except httpx.HTTPStatusError as e:
        raise HTTPException(status_code=e.response.status_code, detail="Upstream returned error") from e
    except Exception as e:
        raise HTTPException(status_code=502, detail="Failed to fetch url") from e

    return [ListItem(**it) for it in items]


@app.post("/scrape", response_model=ScrapeResponse)
async def scrape_post(body: ScrapeRequest) -> ScrapeResponse:
    try:
        data = await _scrape_dispatch(str(body.url), body.url.host or "")
    except httpx.HTTPStatusError as e:
        raise HTTPException(status_code=e.response.status_code, detail="Upstream returned error") from e
    except Exception as e:
        raise HTTPException(status_code=502, detail="Failed to fetch url") from e
    return ScrapeResponse(**data)


# ===== GLOBAL MULTI-SITE SEARCH (Porn-App Pro Feature) =====

from app.services.global_search import global_search as _global_search, global_trending
from fastapi import Query
from typing import Optional

@app.get("/api/v1/search/global")
async def global_search_endpoint(
    query: str = Query(..., description="Search keyword (e.g., 'blonde', 'asian')"),
    sites: Optional[list[str]] = Query(None, description="Sites to search (default: all)"),
    limit_per_site: int = Query(10, ge=1, le=50, description="Results per site"),
    max_sites: int = Query(30, ge=1, le=50, description="Max sites to search")
):
    """
    🔥 KILLER FEATURE: Search across multiple sites simultaneously!
    
    This is porn-app.com's $3.99/mo Pro feature - yours for FREE!
    
    Examples:
    - /api/v1/search/global?query=blonde
    - /api/v1/search/global?query=asian&sites=xhamster&sites=xnxx&limit_per_site=20
    
    Returns results from all sites in ONE request instead of making 4+ separate calls.
    """
    return await _global_search(query, sites, limit_per_site, max_sites)


@app.get("/api/v1/trending/global")
async def global_trending_endpoint(
    sites: Optional[list[str]] = Query(None, description="Sites to fetch from (default: all)"),
    limit_per_site: int = Query(10, ge=1, le=50, description="Results per site")
):
    """
    Get trending/popular videos from all sites at once
    
    Example:
    - /api/v1/trending/global?limit_per_site=20
    """
    return await global_trending(sites, limit_per_site)


# ===== VIDEO STREAMING URLs =====

from app.services.video_streaming import get_video_info, get_stream_url
from app.api.endpoints import recommendations # Import hls router

app.include_router(recommendations.router, prefix="/api/v1/recommendations", tags=["AI Recommendations"])

@app.get("/api/v1/video/info")
async def video_info_endpoint(request: Request, url: str = Query(..., description="Video page URL")):
    """
    🎬 Get video streaming information
    
    Returns playable video stream URLs (MP4, HLS) for direct playback.
    Your API returns URLs - clients decide how to play them (HTML5, mobile apps, etc.)
    
    Example:
        /api/v1/video/info?url=https://www.xnxx.com/video-abc123/sample
        
    Response:
        {
            "title": "Video Title",
            "video": {
                "streams": [
                    {"quality": "1080p", "url": "https://...mp4", "format": "mp4"},
                    {"quality": "480p", "url": "https://...mp4", "format": "mp4"}
                ],
                "hls": "https://.../master.m3u8",
                "default": "https://...1080p.mp4",
                "has_video": true
            },
            "playable": true
        }
    """
    try:
        # Determine API base URL for proxy links
        # Priority: 1. Env Var/Settings (BASE_URL) 2. Request Host (if behind proxy) 3. Localhost
        from app.config.settings import settings
        
        if settings.BASE_URL:
             api_base = settings.BASE_URL
        else:
             api_base = str(request.base_url)

        return await get_video_info(url, api_base_url=api_base)
    except HTTPException as e:
        raise e
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch video info: {str(e)}")


@app.get("/api/v1/video/stream")
async def direct_stream_endpoint(
    request: Request,
    url: str = Query(..., description="Video page URL"),
    quality: str = Query("default", description="Quality: 1080p, 720p, 480p, or default")
):
    """
    Get direct video stream URL
    
    Simpler endpoint that just returns the video URL for a specific quality.
    
    Example:
        /api/v1/video/stream?url=https://xnxx.com/video-123&quality=1080p
        
    Response:
        {
            "stream_url": "https://...video.mp4",
            "quality": "1080p",
            "format": "mp4"
        }
    """
    try:
        # Determine API base URL for proxy links
        from app.config.settings import settings
        
        if settings.BASE_URL:
             api_base = settings.BASE_URL
        else:
             api_base = str(request.base_url)

        return await get_stream_url(url, quality, api_base_url=api_base)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch stream URL: {str(e)}")


# ===== Monitoring Endpoints (FREE) =====

@app.get("/cache/stats")
async def get_cache_stats():
    """Get cache performance statistics"""
    return cache.get_stats()


@app.post("/cache/clear")
async def clear_cache():
    """Clear all cache entries"""
    await cache.clear()
    return {"status": "cache cleared", "message": "All cached items removed"}


@app.get("/rate-limit/stats")
async def get_rate_limit_stats():
    """Get rate limiter statistics"""
    return rate_limiter.get_stats()


# ===== Category Endpoints =====


@app.get("/xnxx/categories", response_model=list[CategoryItem])
async def get_xnxx_categories() -> list[CategoryItem]:
    """Get list of XNXX categories"""
    try:
        categories = xnxx.get_categories()
        return [CategoryItem(**cat) for cat in categories]
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to load categories: {str(e)}")


@app.get("/masa/categories", response_model=list[CategoryItem])
async def get_masa_categories() -> list[CategoryItem]:
    """Get list of Masa categories"""
    try:
        categories = masa49.get_categories()
        return [CategoryItem(**cat) for cat in categories]
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to load categories: {str(e)}")


@app.get("/xvideos/categories", response_model=list[CategoryItem])
async def get_xvideos_categories() -> list[CategoryItem]:
    """Get list of XVideos categories"""
    try:
        categories = xvideos.get_categories()
        return [CategoryItem(**cat) for cat in categories]
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to load categories: {str(e)}")


@app.get("/xhamster/categories", response_model=list[CategoryItem])
async def get_xhamster_categories() -> list[CategoryItem]:
    """Get list of xHamster categories"""
    try:
        categories = xhamster.get_categories()
        return [CategoryItem(**cat) for cat in categories]
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to load categories: {str(e)}")
