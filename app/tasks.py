import logging
from app.core.celery_app import celery_app
import asyncio
from app.core import cache

logger = logging.getLogger(__name__)

@celery_app.task(acks_late=True)
def test_celery(word: str) -> str:
    return f"test task return {word}"

@celery_app.task
def optimize_cache(key: str):
    """
    Example background task to optimize or warm up cache
    """
    logger.info(f"Optimizing cache for key: {key}")
    # Simulate processing
    return f"Optimized {key}"
