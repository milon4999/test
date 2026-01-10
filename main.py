from __future__ import annotations

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, HttpUrl, field_validator

import masa49
import xhamster
import xnxx
import xvideos
import json
import os

# Create FastAPI app
app = FastAPI(title="Scraper API - Simple Version")

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


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
    try:
        data = await _scrape_dispatch(str(req.url), req.url.host or "")
    except httpx.HTTPStatusError as e:
        raise HTTPException(status_code=e.response.status_code, detail="Upstream returned error") from e
    except Exception as e:
        raise HTTPException(status_code=502, detail="Failed to fetch url") from e
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

    try:
        items = await _list_dispatch(str(req.base_url), req.base_url.host or "", page, limit)
    except httpx.HTTPStatusError as e:
        raise HTTPException(status_code=e.response.status_code, detail="Upstream returned error") from e
    except Exception as e:
        raise HTTPException(status_code=502, detail="Failed to fetch url") from e
    
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


@app.get("/xnxx/categories", response_model=list[CategoryItem])
async def get_xnxx_categories() -> list[CategoryItem]:
    """Get list of XNXX categories"""
    try:
        # Load categories from JSON file
        json_path = os.path.join(os.path.dirname(__file__), "xnxx_categories.json")
        with open(json_path, 'r', encoding='utf-8') as f:
            categories = json.load(f)
        return [CategoryItem(**cat) for cat in categories]
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Categories file not found")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to load categories: {str(e)}")


@app.get("/masa/categories", response_model=list[CategoryItem])
async def get_masa_categories() -> list[CategoryItem]:
    """Get list of Masa categories"""
    try:
        # Load categories from JSON file
        json_path = os.path.join(os.path.dirname(__file__), "masa_categories.json")
        with open(json_path, 'r', encoding='utf-8') as f:
            categories = json.load(f)
        return [CategoryItem(**cat) for cat in categories]
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Categories file not found")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to load categories: {str(e)}")


@app.get("/xvideos/categories", response_model=list[CategoryItem])
async def get_xvideos_categories() -> list[CategoryItem]:
    """Get list of XVideos categories"""
    try:
        # Load categories from JSON file
        json_path = os.path.join(os.path.dirname(__file__), "xvideos_categories.json")
        with open(json_path, 'r', encoding='utf-8') as f:
            categories = json.load(f)
        return [CategoryItem(**cat) for cat in categories]
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Categories file not found")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to load categories: {str(e)}")


@app.get("/xhamster/categories", response_model=list[CategoryItem])
async def get_xhamster_categories() -> list[CategoryItem]:
    """Get list of xHamster categories"""
    try:
        # Load categories from JSON file
        json_path = os.path.join(os.path.dirname(__file__), "xhamster_categories.json")
        with open(json_path, 'r', encoding='utf-8') as f:
            categories = json.load(f)
        return [CategoryItem(**cat) for cat in categories]
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Categories file not found")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to load categories: {str(e)}")
