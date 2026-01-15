import asyncio
import sys
import os

# Add backend to path
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from app.scrapers import beeg

async def verify_beeg():
    print("--- Verify Beeg ---")
    
    # 1. Categories
    print("\n1. Testing Categories...")
    cats = beeg.get_categories()
    print(f"Found {len(cats)} categories")
    if len(cats) > 0:
        print(f"Sample: {cats[0]}")
    else:
        print("❌ No categories found")
        
    # 2. List Videos
    print("\n2. Testing List Videos...")
    # Try listing from homepage or a category
    videos = await beeg.list_videos("https://beeg.com/")
    print(f"Found {len(videos)} videos on homepage")
    
    if len(videos) > 0:
        print(f"Sample video: {videos[0]}")
        first_url = videos[0]['url']
        
        # 3. Scrape Video
        print(f"\n3. Testing Scrape Video: {first_url}")
        try:
            details = await beeg.scrape(first_url)
            print("Title:", details.get('title'))
            print("Streams:", len(details.get('video', {}).get('streams', [])))
            print("Preview:", details.get('preview_url'))
            
            if details.get('video', {}).get('has_video'):
                print("✅ Has video streams")
            else:
                print("❌ No video streams found")
        except Exception as e:
            print(f"❌ Scrape failed: {e}")
            
    else:
        print("❌ No videos found in list, skipping scrape test")

if __name__ == "__main__":
    asyncio.run(verify_beeg())
