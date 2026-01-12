# 📁 New Project Structure

After reorganization, your project will look like this:

```
backend/
├── scrapers/              # 🎯 All scraper modules
│   ├── __init__.py
│   ├── base.py           # Base scraper class (optional)
│   ├── xnxx.py
│   ├── xhamster.py
│   ├── xvideos.py
│   └── masa49.py
│
├── core/                  # ⚡ Core optimization modules
│   ├── __init__.py
│   ├── cache.py          # simple_cache.py
│   ├── pool.py           # connection_pool.py
│   ├── limiter.py        # rate_limiter.py
│   └── optimizer.py      # db_optimizer.py
│
├── services/              # 🔧 Business logic services
│   ├── __init__.py
│   ├── global_search.py
│   └── video_streaming.py
│
├── api/                   # 🌐 API layer (future separation)
│   └── __init__.py
│
├── main.py               # FastAPI app entry point
├── requirements.txt
├── Procfile
├── README.md
└── demo_player.html
```

## Benefits

✅ **Easy to add new scrapers**: Just drop a new file in `scrapers/`
✅ **Clear separation**: Scrapers, core modules, services are organized
✅ **Scalable**: Can add more modules without cluttering root
✅ **Import clarity**: `from scrapers import xnxx` instead of `import xnxx`

## How to Add New Scraper

1. Create `scrapers/newsite.py`:

```python
def can_handle(host: str) -> bool:
    return "newsite.com" in host.lower()

async def scrape(url: str) -> dict:
    # Your scraping logic
    pass

async def list_videos(url: str, page: int = 1) -> list:
    # Your listing logic
    pass
```

2. Import in `scrapers/__init__.py`:

```python
from . import newsite
```

3. Add to dispatcher in `main.py`:

```python
from scrapers import newsite

# In _scrape_dispatch
if newsite.can_handle(host):
    return await newsite.scrape(url)
```

Done! 🎉
