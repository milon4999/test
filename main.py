from __future__ import annotations

import httpx
from fastapi import FastAPI, HTTPException, BackgroundTasks, Depends
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, HttpUrl, field_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
from models import Job
from background import job_manager

import masa49
import xhamster
import xnxx
import xvideos
from contextlib import asynccontextmanager

# ------------------------------------------------------------------------------
# Pydantic Models
# ------------------------------------------------------------------------------

class ListRequest(BaseModel):
    base_url: HttpUrl

class ListItem(BaseModel):
    title: str | None = None
    thumbnail_url: str | None = None
    duration: str | None = None
    url: str | None = None
    views: str | None = None
    uploader_name: str | None = None
    uploader_pic: str | None = None
    uploader_url: str | None = None
    upload_time: str | None = None
    category: str | None = None
    description: str | None = None
    tags: list[str] | None = None

class SourceItem(BaseModel):
    name: str
    base_url: str
    favicon_url: str

class ScrapeRequest(BaseModel):
    url: HttpUrl

class ScrapeResponse(BaseModel):
    title: str | None = None
    video_url: str | None = None
    thumbnail_url: str | None = None
    duration: str | None = None
    uploader_name: str | None = None
    uploader_pic: str | None = None
    uploader_url: str | None = None
    upload_time: str | None = None
    category: str | None = None
    tags: list[str] | None = None
    description: str | None = None
    views: str | None = None

# ------------------------------------------------------------------------------
# Context Manager & App
# ------------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    await cache_manager.connect()
    yield
    await cache_manager.disconnect()



# Create FastAPI app
app = FastAPI(title="OSINT Scraper API", lifespan=lifespan)

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/categories")
async def get_categories() -> list[str]:
    """Return a list of video categories for filtering"""
    return [
        "Amateur", "Anal", "Asian", "Ass", "Babysitters", "BBW", "Big Tits", "Blond", 
        "Blowjob", "Brunette", "Celebrity", "Creampie", "Cumshots", "Ebony", "Euro", 
        "Hardcore", "Hentai", "Indian", "Interracial", "Japanese", "Latina", "Lesbian", 
        "Mature", "Milf", "Mom", "Old/Young", "Public", "Redhead", "Small Tits", 
        "Squirt", "Teen", "Threesome", "Toys"
    ]


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


@app.post("/jobs/crawl", status_code=202)
async def start_crawl_job(
    req: ListRequest,
    background_tasks: BackgroundTasks,
    max_pages: int = 5,
    max_items: int = 100
):
    """Start a background crawling job"""
    params = {
        "base_url": str(req.base_url),
        "max_pages": max_pages,
        "max_items": max_items
    }
    
    # Create job record
    job_id = await job_manager.create_job(user_id=None, job_type="crawl", params=params)
    
    # Schedule background task
    background_tasks.add_task(job_manager.run_crawl_task, job_id)
    
    return {
        "job_id": job_id,
        "status": "pending",
        "message": "Crawl job started in background"
    }


@app.get("/jobs/{job_id}")
async def get_job_status(job_id: str, db: AsyncSession = Depends(get_db)):
    """Get status of a background job"""
    query = select(Job).where(Job.job_id == job_id)
    result = await db.execute(query)
    job = result.scalar_one_or_none()
    
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
        
    return {
        "job_id": job.job_id,
        "type": job.job_type,
        "status": job.status,
        "progress": job.progress,
        "items_processed": job.items_processed,
        "created_at": job.created_at,
        "completed_at": job.completed_at,
        "error": job.error,
        "result": job.result
    }


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
