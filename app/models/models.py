# Database Models

from datetime import datetime
from typing import Optional
from sqlalchemy import String, Integer, Float, Boolean, DateTime, JSON, Text, ForeignKey
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    """Base class for all models"""
    pass


class User(Base):
    """User/API Key model"""
    __tablename__ = "users"
    
    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    username: Mapped[Optional[str]] = mapped_column(String(100), unique=True, nullable=True)
    hashed_password: Mapped[str] = mapped_column(String(255))
    
    # API Key
    api_key: Mapped[Optional[str]] = mapped_column(String(64), unique=True, index=True, nullable=True)
    
    # Role and Status
    role: Mapped[str] = mapped_column(String(20), default="user")  # admin, user, guest
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    
    # Usage Quota
    daily_quota: Mapped[int] = mapped_column(Integer, default=1000)
    requests_today: Mapped[int] = mapped_column(Integer, default=0)
    total_requests: Mapped[int] = mapped_column(Integer, default=0)
    
    # Timestamps
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    last_login: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    
    # Relationships
    scrape_history = relationship("ScrapeHistory", back_populates="user", cascade="all, delete-orphan")
    jobs = relationship("Job", back_populates="user", cascade="all, delete-orphan")


class ScrapeHistory(Base):
    """Track scraping history"""
    __tablename__ = "scrape_history"
    
    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    user_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True)
    
    # Request Info
    url: Mapped[str] = mapped_column(Text, index=True)
    platform: Mapped[str] = mapped_column(String(50), index=True)  # xhamster, xnxx, etc.
    endpoint_type: Mapped[str] = mapped_column(String(20))  # scrape, list, crawl
    
    # Response Info
    success: Mapped[bool] = mapped_column(Boolean)
    status_code: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    
    # Performance
    response_time: Mapped[Optional[float]] = mapped_column(Float, nullable=True)  # seconds
    cached: Mapped[bool] = mapped_column(Boolean, default=False)
    
    # Metadata
    ip_address: Mapped[Optional[str]] = mapped_column(String(45), nullable=True)
    user_agent: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    
    # Timestamps
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)
    
    # Relationships
    user = relationship("User", back_populates="scrape_history")


class VideoMetadata(Base):
    """Store scraped video metadata"""
    __tablename__ = "video_metadata"
    
    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    
    # Video Info
    url: Mapped[str] = mapped_column(Text, unique=True, index=True)
    platform: Mapped[str] = mapped_column(String(50), index=True)
    video_id: Mapped[Optional[str]] = mapped_column(String(100), index=True, nullable=True)
    
    title: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    thumbnail_url: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    
    # Video Details
    duration: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    views: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    uploader_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    category: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    tags: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)  # Store as JSON array
    
    # Additional metadata
    extra_data: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    
    # Timestamps
    first_scraped: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    last_updated: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    scrape_count: Mapped[int] = mapped_column(Integer, default=1)


class Job(Base):
    """Background job tracking"""
    __tablename__ = "jobs"
    
    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    user_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True)
    
    # Job Info
    job_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)  # Celery task ID
    job_type: Mapped[str] = mapped_column(String(20), index=True)  # scrape, crawl, batch
    
    # Parameters
    parameters: Mapped[dict] = mapped_column(JSON)
    
    # Status
    status: Mapped[str] = mapped_column(String(20), index=True, default="pending")  # pending, running, completed, failed
    progress: Mapped[int] = mapped_column(Integer, default=0)  # 0-100
    
    # Results
    result: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    items_processed: Mapped[int] = mapped_column(Integer, default=0)
    
    # Timing
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    
    # Relationships
    user = relationship("User", back_populates="jobs")


class APIStats(Base):
    """Daily API usage statistics"""
    __tablename__ = "api_stats"
    
    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    date: Mapped[datetime] = mapped_column(DateTime, unique=True, index=True)
    
    # Request counts
    total_requests: Mapped[int] = mapped_column(Integer, default=0)
    successful_requests: Mapped[int] = mapped_column(Integer, default=0)
    failed_requests: Mapped[int] = mapped_column(Integer, default=0)
    
    # By endpoint
    scrape_requests: Mapped[int] = mapped_column(Integer, default=0)
    list_requests: Mapped[int] = mapped_column(Integer, default=0)
    crawl_requests: Mapped[int] = mapped_column(Integer, default=0)
    
    # By platform
    platform_stats: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    
    # Performance
    avg_response_time: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    cache_hit_rate: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    
    # Active users
    unique_users: Mapped[int] = mapped_column(Integer, default=0)
    unique_ips: Mapped[int] = mapped_column(Integer, default=0)
