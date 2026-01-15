"""
Scrapers package - Contains all site-specific scraper modules

Each scraper is in its own folder with:
- scraper.py: Main scraping logic
- __init__.py: Package exports

Each scraper implements:
- can_handle(host: str) -> bool
- scrape(url: str) -> dict
- list_videos(url: str, page: int, limit: int) -> list[dict]
"""

from . import xnxx
from . import xhamster
from . import xvideos
from . import masa49
from . import pornhub

__all__ = ['xnxx', 'xhamster', 'xvideos', 'masa49', 'pornhub']
