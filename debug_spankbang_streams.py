import asyncio
from curl_cffi.requests import AsyncSession
from bs4 import BeautifulSoup
import re
import json

async def debug_sb():
    url = "https://spankbang.com/5pfhh/video/porn"
    
    async with AsyncSession(
        impersonate="chrome120",
        headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Cookie": "age_verified=1",
        },
        timeout=20.0
    ) as client:
        resp = await client.get(url)
        html = resp.text
    
    print("=== Searching for stream_data ===")
    pattern = r'stream_data\s*=\s*(\{.*?\});'
    match = re.search(pattern, html, re.DOTALL)
    if match:
        print("Found stream_data!")
        try:
            data = json.loads(match.group(1))
            print(f"Keys: {data.keys()}")
            if isinstance(data, dict):
                for key in list(data.keys())[:10]:
                    print(f"  {key}: {str(data[key])[:100]}")
        except Exception as e:
            print(f"Error parsing: {e}")
    else:
        print("stream_data not found")
    
    print("\n=== Searching for video sources ===")
    pattern2 = r'<source\s+src="([^"]+)"'
    matches = re.findall(pattern2, html)
    print(f"Found {len(matches)} <source> tags")
    for m in matches[:3]:
        print(f"  {m[:100]}")
    
    # Save for manual inspection
    with open("sb_debug.html", "w", encoding="utf-8") as f:
        f.write(html)
    print("\n=== Saved HTML to sb_debug.html ===")

if __name__ == "__main__":
    asyncio.run(debug_sb())
