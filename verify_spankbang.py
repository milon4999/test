import asyncio
import sys
import os

# Add backend to path
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from app.scrapers import spankbang

async def verify_spankbang():
    print("--- Verify SpankBang ---")
    
    # 1. Categories
    print("\n1. Testing Categories...")
    cats = spankbang.get_categories()
    print(f"Found {len(cats)} categories")
    if len(cats) > 0:
        print(f"Sample: {cats[0]}")
    else:
        print("❌ No categories found")
        
    # 2. List Videos
    print("\n2. Testing List Videos...")
    # Trending
    videos = await spankbang.list_videos("https://spankbang.com/trending_videos")
    print(f"Found {len(videos)} videos in Trending")
    
    if len(videos) > 0:
        print(f"Sample video: {videos[0]}")
        first_url = videos[0]['url']
        
        # 3. Scrape Video
        print(f"\n3. Testing Scrape Video: {first_url}")
        try:
            details = await spankbang.scrape(first_url)
            print("Title:", details.get('title'))
            print("Streams:", len(details.get('video', {}).get('streams', [])))
            
            if details.get('video', {}).get('has_video'):
                print("✅ Has video streams")
                for s in details['video']['streams']:
                    print(f" - {s['quality']}p: {s['url'][:50]}...")
            else:
                print("❌ No video streams found")
        except Exception as e:
            print(f"❌ Scrape failed: {e}")
            
    else:
        print("❌ No videos found in list, skipping scrape test")

if __name__ == "__main__":
    asyncio.run(verify_spankbang())
