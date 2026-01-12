# Pydantic Schemas for Request/Response Validation

from pydantic import BaseModel, EmailStr, Field, HttpUrl, field_validator
from typing import Optional
from datetime import datetime


# ===== User Schemas =====

class UserBase(BaseModel):
    email: EmailStr
    username: Optional[str] = None


class UserCreate(UserBase):
    password: str = Field(..., min_length=8, max_length=100)


class UserLogin(BaseModel):
    email: EmailStr
    password: str


class UserResponse(UserBase):
    id: int
    role: str
    is_active: bool
    api_key: Optional[str] = None
    daily_quota: int
    requests_today: int
    total_requests: int
    created_at: datetime
    last_login: Optional[datetime] = None
    
    class Config:
        from_attributes = True


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: Optional[str] = None
    token_type: str = "bearer"
    expires_in: int


# ===== Scraping Schemas =====

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
    title: Optional[str] = None
    description: Optional[str] = None
    thumbnail_url: Optional[str] = None
    duration: Optional[str] = None
    views: Optional[str] = None
    uploader_name: Optional[str] = None
    category: Optional[str] = None
    tags: list[str] = []
    cached: bool = False  # Indicates if result came from cache


class ListItem(BaseModel):
    url: HttpUrl
    title: Optional[str] = None
    thumbnail_url: Optional[str] = None
    duration: Optional[str] = None
    views: Optional[str] = None
    uploader_name: Optional[str] = None
    category: Optional[str] = None
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


# ===== Job Schemas =====

class JobCreate(BaseModel):
    job_type: str = Field(..., pattern="^(scrape|crawl|batch)$")
    parameters: dict


class JobResponse(BaseModel):
    id: int
    job_id: str
    job_type: str
    status: str
    progress: int
    parameters: dict
    result: Optional[dict] = None
    error: Optional[str] = None
    items_processed: int
    created_at: datetime
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    
    class Config:
        from_attributes = True


class JobStatus(BaseModel):
    job_id: str
    status: str
    progress: int
    items_processed: int
    error: Optional[str] = None


# ===== Stats Schemas =====

class UsageStats(BaseModel):
    total_requests: int
    successful_requests: int
    failed_requests: int
    scrape_requests: int
    list_requests: int
    crawl_requests: int
    unique_users: int
    cache_hit_rate: Optional[float] = None
    avg_response_time: Optional[float] = None


class HealthResponse(BaseModel):
    status: str
    version: str
    timestamp: datetime
    uptime: Optional[float] = None


class DetailedHealthResponse(HealthResponse):
    database: bool
    redis: bool
    celery: bool
    dependencies: dict


# ===== Admin Schemas =====

class UpdateQuota(BaseModel):
    daily_quota: int = Field(..., ge=0, le=100000)


class ClearCacheRequest(BaseModel):
    pattern: Optional[str] = None  # Clear specific pattern or all
