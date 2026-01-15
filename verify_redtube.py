import asyncio
import json
import sys
import os
import logging

# Add backend to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.scrapers import redtube

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("verify_redtube")

async def verify():
    print("--- Verify RedTube ---")
    
    # 1. Categories
    print("\n1. Testing Categories...")
    cats = redtube.get_categories()
    print(f"✅ Found {len(cats)} categories.")
    if len(cats) > 0:
        print(f"Sample: {cats[0]['name']} -> {cats[0]['url']}")

    # 2. List Videos
    print("\n2. Testing List Videos (Base URL)...")
    base_url = "https://www.redtube.com/"
    items = await redtube.list_videos(base_url, page=1, limit=5)
    print(f"Found {len(items)} items from {base_url}")
    
    video_url = None
    if len(items) > 0:
        print("✅ List successful")
        item = items[0]
        print("First Item:", json.dumps(item, indent=2))
        video_url = item.get("url")
    else:
        print("❌ List returned 0 items.")
        
    # 2b. List Videos (Search)
    print("\n2b. Testing List Videos (Search 'asian')...")
    search_url = "https://www.redtube.com/?search=asian"
    search_items = await redtube.list_videos(search_url, page=1, limit=5)
    print(f"Found {len(search_items)} search items")
    if len(search_items) > 0:
        print("✅ Search listing successful")
        if not video_url: video_url = search_items[0]["url"]

    # 3. Scrape Video
    if video_url:
        print(f"\n3. Testing Scrape Video: {video_url}")
        try:
            data = await redtube.scrape(video_url)
            print("✅ Scrape successful")
            print(f"Title: {data.get('title')}")
            print(f"Streams: {len(data.get('video', {}).get('streams', []))}")
            if data.get('video', {}).get('has_video'):
                print("✅ Has video streams")
                # print(json.dumps(data['video'], indent=2))
            else:
                print("❌ No streams found (might be premium or parsing error)")
        except Exception as e:
            print(f"❌ Scrape failed: {e}")
    else:
        print("\nSkipping Scrape (no video URL found)")

if __name__ == "__main__":
    asyncio.run(verify())
