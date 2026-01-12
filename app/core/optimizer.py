"""
SQLite Optimization Configuration
Turbocharge SQLite for production use (zero cost)
"""

from sqlalchemy import event, create_engine
from sqlalchemy.engine import Engine
import logging

logger = logging.getLogger(__name__)


# SQLite PRAGMA optimizations
SQLITE_PRAGMAS = """
PRAGMA journal_mode = WAL;           -- Write-Ahead Logging (3x faster writes)
PRAGMA synchronous = NORMAL;         -- Balance speed vs safety
PRAGMA cache_size = -64000;          -- 64MB cache
PRAGMA temp_store = MEMORY;          -- Temp tables in RAM
PRAGMA mmap_size = 30000000000;      -- Memory-mapped I/O (30GB)
PRAGMA page_size = 4096;             -- Optimal page size
PRAGMA foreign_keys = ON;            -- Enforce foreign keys
PRAGMA auto_vacuum = INCREMENTAL;    -- Incremental vacuuming
"""


def optimize_sqlite(engine: Engine):
    """
    Apply SQLite optimizations to engine
    
    Args:
        engine: SQLAlchemy engine
    """
    
    @event.listens_for(engine, "connect")
    def set_sqlite_pragma(dbapi_conn, connection_record):
        """Execute PRAGMA statements on each connection"""
        cursor = dbapi_conn.cursor()
        
        for pragma in SQLITE_PRAGMAS.strip().split(';'):
            pragma = pragma.strip()
            if pragma:
                try:
                    cursor.execute(pragma)
                    logger.debug(f"Applied: {pragma}")
                except Exception as e:
                    logger.error(f"Failed to apply pragma: {pragma} - {e}")
        
        cursor.close()
        logger.info("SQLite optimizations applied")


def create_optimized_sqlite_engine(database_url: str):
    """
    Create SQLite engine with optimizations
    
    Args:
        database_url: Database URL (e.g., "sqlite:///./scraper.db")
        
    Returns:
        Optimized SQLAlchemy engine
    """
    engine = create_engine(
        database_url,
        connect_args={
            "check_same_thread": False,  # Allow multi-threading
            "timeout": 30  # 30 second timeout for locks
        },
        pool_size=5,  # Connection pool
        max_overflow=10,
        echo=False  # Set to True for SQL logging
    )
    
    # Apply optimizations
    optimize_sqlite(engine)
    
    logger.info(f"Created optimized SQLite engine: {database_url}")
    
    return engine


# Recommended indexes for better performance
RECOMMENDED_INDEXES = """
-- Video metadata indexes
CREATE INDEX IF NOT EXISTS idx_video_url ON video_metadata(url);
CREATE INDEX IF NOT EXISTS idx_video_platform ON video_metadata(platform);
CREATE INDEX IF NOT EXISTS idx_video_created ON video_metadata(first_scraped);
CREATE INDEX IF NOT EXISTS idx_video_platform_created ON video_metadata(platform, first_scraped DESC);

-- Scrape history indexes
CREATE INDEX IF NOT EXISTS idx_history_user ON scrape_history(user_id);
CREATE INDEX IF NOT EXISTS idx_history_platform ON scrape_history(platform);
CREATE INDEX IF NOT EXISTS idx_history_created ON scrape_history(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_history_success ON scrape_history(success);

-- User indexes
CREATE INDEX IF NOT EXISTS idx_user_email ON users(email);
CREATE INDEX IF NOT EXISTS idx_user_api_key ON users(api_key);
CREATE INDEX IF NOT EXISTS idx_user_active ON users(is_active);

-- Job indexes
CREATE INDEX IF NOT EXISTS idx_job_id ON jobs(job_id);
CREATE INDEX IF NOT EXISTS idx_job_status ON jobs(status);
CREATE INDEX IF NOT EXISTS idx_job_user ON jobs(user_id);
"""


def create_indexes(engine: Engine):
    """
    Create recommended indexes for performance
    
    Args:
        engine: SQLAlchemy engine
    """
    with engine.connect() as conn:
        for statement in RECOMMENDED_INDEXES.strip().split(';'):
            statement = statement.strip()
            if statement:
                try:
                    conn.execute(statement)
                    # Extract index name from CREATE INDEX statement
                    if "CREATE INDEX" in statement:
                        idx_name = statement.split("idx_")[1].split()[0] if "idx_" in statement else "unknown"
                        logger.info(f"Created index: idx_{idx_name}")
                except Exception as e:
                    logger.error(f"Failed to create index: {e}")
        
        conn.commit()
    
    logger.info("All indexes created successfully")


def analyze_database(engine: Engine):
    """
    Run ANALYZE to update SQLite query planner statistics
    
    Args:
        engine: SQLAlchemy engine
    """
    with engine.connect() as conn:
        conn.execute("ANALYZE")
        logger.info("Database analyzed - query planner updated")


def vacuum_database(engine: Engine):
    """
    Vacuum database to reclaim space and optimize
    
    Args:
        engine: SQLAlchemy engine
    """
    with engine.connect() as conn:
        # Get size before
        result = conn.execute("PRAGMA page_count").fetchone()
        pages_before = result[0] if result else 0
        
        conn.execute("VACUUM")
        
        # Get size after
        result = conn.execute("PRAGMA page_count").fetchone()
        pages_after = result[0] if result else 0
        
        pages_freed = pages_before - pages_after
        logger.info(f"Database vacuumed: freed {pages_freed} pages")
