from __future__ import annotations

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, HttpUrl, field_validator

import masa49
import xhamster
import xnxx
import xvideos

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
    category: str | None = None
    tags: list[str] = []


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


@app.get("/list", response_model=list[ListItem])
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
