import asyncio
import httpx
import re
import json

URL = "https://xhamster.com/videos/my-stepbrother-came-in-my-bedroom-and-fuck-my-pussy-hardly-shopna25-xh8YYPL"

async def fetch_html(url: str) -> str:
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": "https://xhamster.com/"
    }
    async with httpx.AsyncClient(follow_redirects=True, headers=headers) as client:
        resp = await client.get(url)
        return resp.text

def extract_video_data(html: str):
    print("Extracting video data...")
    # Regex to find window.initials = { ... }
    match = re.search(r'window\.initials\s*=\s*({.+?});', html, re.DOTALL)
    if not match:
        print("❌ Could not find window.initials")
        return None

    try:
        data = json.loads(match.group(1))
        # Navigate to video model
        xplayer = data.get("xplayerSettings")
        sources = None
        if xplayer:
            sources = xplayer.get("sources")
        
        if not sources:
            video_model = data.get("videoModel")
            if video_model:
                sources = video_model.get("sources")

        if sources:
            print(f"\n✅ Sources found with keys: {list(sources.keys())}")
            print(json.dumps(sources, indent=2))
        else:
            print("❌ No sources found in JSON")

        return sources

    except Exception as e:
        print(f"❌ Error parsing JSON: {e}")
        return None

async def main():
    print(f"Fetching {URL}...")
    html = await fetch_html(URL)
    print(f"Fetched {len(html)} bytes")
    extract_video_data(html)

if __name__ == "__main__":
    asyncio.run(main())
