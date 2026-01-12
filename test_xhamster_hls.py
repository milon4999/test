import asyncio
import sys
import os

# Add parent directory to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'app'))

from scrapers.xhamster import scraper

async def test_xhamster_hls():
    url = "https://xhamster.com/videos/my-stepbrother-came-in-my-bedroom-and-fuck-my-pussy-hardly-shopna25-xh8YYPL"
    
    print(f"Testing: {url}\n")
    
    try:
        result = await scraper.scrape(url)
        
        print("=" * 60)
        print("VIDEO DATA:")
        print("=" * 60)
        
        video = result.get("video", {})
        print(f"\nhas_video: {video.get('has_video')}")
        print(f"hls: {video.get('hls')}")
        print(f"default: {video.get('default')}")
        
        print(f"\n\nSTREAMS ({len(video.get('streams', []))}):")
        print("-" * 60)
        for stream in video.get("streams", []):
            print(f"  - Quality: {stream.get('quality'):10s} | Format: {stream.get('format'):5s} | URL: {stream.get('url')[:80]}...")
        
        print("\n" + "=" * 60)
        print("RESULT:")
        print("=" * 60)
        
        hls_stream = [s for s in video.get("streams", []) if s.get("format") == "hls"]
        if hls_stream:
            print("[SUCCESS] HLS stream found in streams array with format='hls'")
            print(f"   Quality: {hls_stream[0].get('quality')}")
            print(f"   URL: {hls_stream[0].get('url')[:100]}...")
        else:
            print("[FAILED] HLS stream NOT found in streams array!")
            
        if video.get("hls"):
            print(f"[OK] HLS URL also available at video['hls']")
        else:
            print(f"[WARNING] No HLS URL at video['hls']")
            
    except Exception as e:
        print(f"❌ ERROR: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    asyncio.run(test_xhamster_hls())
