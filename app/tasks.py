import logging
from app.core.celery_app import celery_app
import asyncio
from app.core import cache

logger = logging.getLogger(__name__)

@celery_app.task(acks_late=True)
def test_celery(word: str) -> str:
    return f"test task return {word}"

@celery_app.task(acks_late=True)
def probe_sites() -> str:
    """Probe all Explore sources and record health (dead/moved/blocked detection)."""
    import asyncio
    from app.core.site_monitor import run_all_checks, get_unhealthy_source_ids
    asyncio.run(run_all_checks())
    unhealthy = get_unhealthy_source_ids()
    return f"probed sources; unhealthy={sorted(unhealthy)}"

@celery_app.task
def optimize_cache(key: str):
    """
    Example background task to optimize or warm up cache
    """
    logger.info(f"Optimizing cache for key: {key}")
    # Simulate processing
    return f"Optimized {key}"
