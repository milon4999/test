# 🎯 How to Add a New Scraper

Your scrapers are now perfectly organized! Each website has its own folder.

## 📁 Current Structure

```
backend/
└── scrapers/
    ├── __init__.py
    ├── xnxx/
    │   ├── __init__.py
    │   └── scraper.py
    ├── xhamster/
    │   ├── __init__.py
    │   └── scraper.py
    ├── xvideos/
    │   ├── __init__.py
    │   └── scraper.py
    └── masa49/
        ├── __init__.py
        └── scraper.py
```

---

## ✅ How to Add New Scraper (3 Steps)

### Step 1: Create New Folder

Create `scrapers/pornhub/` (or any site name)

### Step 2: Create Files

**`scrapers/pornhub/__init__.py`**:

```python
"""Pornhub scraper module"""
from .scraper import can_handle, scrape, list_videos

__all__ = ['can_handle', 'scrape', 'list_videos']
```

**`scrapers/pornhub/scraper.py`**:

```python
import httpx
from bs4 import BeautifulSoup

def can_handle(host: str) -> bool:
    """Check if this scraper can handle the given host"""
    return "pornhub.com" in host.lower()

async def scrape(url: str) -> dict:
    """Scrape single video metadata"""
    async with httpx.AsyncClient() as client:
        response = await client.get(url)
        html = response.text

    soup = BeautifulSoup(html, 'lxml')

    return {
        "url": url,
        "title": soup.find('h1').get_text(strip=True),
        "thumbnail_url": soup.find('meta', property='og:image')['content'],
        "duration": None,  # Extract from page
        "views": None,  # Extract from page
        "uploader_name": None,  # Extract from page
        "video": {  # Video streaming URLs
            "streams": [],
            "hls": None,
            "default": None,
            "has_video": False
        }
    }

async def list_videos(base_url: str, page: int = 1, limit: int = 20) -> list[dict]:
    """List videos from a page"""
    async with httpx.AsyncClient() as client:
        response = await client.get(f"{base_url}?page={page}")
        html = response.text

    soup = BeautifulSoup(html, 'lxml')
    videos = []

    # Extract video cards from the page
    # Implementation here...

    return videos
```

### Step 3: Register in `scrapers/__init__.py`

```python
from . import xnxx
from . import xhamster
from . import xvideos
from . import masa49
from . import pornhub  # ✅ Add this

__all__ = ['xnxx', 'xhamster', 'xvideos', 'masa49', 'pornhub']
```

### Step 4: Add to `main.py` Dispatcher

```python
from scrapers import masa49, xhamster, xnxx, xvideos, pornhub  # Add pornhub

# In _scrape_dispatch function:
async def _scrape_dispatch(url: str, host: str) -> dict[str, object]:
    if xhamster.can_handle(host):
        return await xhamster.scrape(url)
    if masa49.can_handle(host):
        return await masa49.scrape(url)
    if xnxx.can_handle(host):
        return await xnxx.scrape(url)
    if xvideos.can_handle(host):
        return await xvideos.scrape(url)
    if pornhub.can_handle(host):  # ✅ Add this
        return await pornhub.scrape(url)
    raise HTTPException(status_code=400, detail="Unsupported host")
```

**Done!** 🎉

---

## 🚀 Benefits of This Structure

✅ **Easy Recognition**: Each site in its own folder
✅ **Scalable**: Add 100+ scrapers without clutter
✅ **Organized**: Related files stay together
✅ **Auto-imports**: Python package system handles it
✅ **Clear**: Easy to find and modify any scraper

---

## 📊 Add 10 Sites in 10 Minutes

```bash
# Copy template
cp -r scrapers/xnxx scrapers/pornhub
# Rename and edit scraper.py
# Add to __init__.py and main.py
# Done!
```

With this structure, you can easily scale to 50+ sites! 🎯
