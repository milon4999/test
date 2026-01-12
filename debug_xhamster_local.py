import asyncio
import httpx
import re
import json

URL = "https://xhamster.com/videos/my-stepbrother-came-in-my-bedroom-and-fuck-my-pussy-hardly-shopna25-xh8YYPL"

async def fetch_html_and_cookies(url: str):
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": "https://xhamster.com/"
    }
    async with httpx.AsyncClient(follow_redirects=True, headers=headers) as client:
        resp = await client.get(url)
        resp = await client.get(url)
        return resp.text, client.cookies

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
            if "hls" in sources:
                print(f"HLS Value: {sources['hls']}")
        else:
            print("❌ No sources found in JSON")

        return sources

    except Exception as e:
        print(f"❌ Error parsing JSON: {e}")
        return None

async def test_stream(url: str, cookies: httpx.Cookies, description: str):
    print(f"\n--- Testing {description} ---")
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36",
        "Origin": "https://xhamster.com",
        "Referer": "https://xhamster.com/videos/my-stepbrother-came-in-my-bedroom-and-fuck-my-pussy-hardly-shopna25-xh8YYPL"
    }
    
    try:
        async with httpx.AsyncClient(headers=headers, cookies=cookies, verify=False) as client:
            resp = await client.head(url)
            print(f"Status: {resp.status_code}")
            if resp.status_code == 200:
                print("✅ SUCCESS")
            else:
                print("❌ FAILED")
    except Exception as e:
        print(f"❌ Error: {e}")

async def main():
    print(f"Fetching {URL}...")
    html, cookies = await fetch_html_and_cookies(URL)
    print(f"Fetched {len(html)} bytes")
    print(f"Cookies captured: {list(cookies.keys())}")
    
    sources = extract_video_data(html)
    
    hls_url = None
    if sources and "hls" in sources:
        hls_val = sources["hls"]
        if isinstance(hls_val, str):
             hls_url = hls_val
        elif isinstance(hls_val, dict):
             hls_url = hls_val.get("url")

    # Fallback Regex
    if not hls_url:
        print("Falling back to Regex extraction for HLS...")
        m = re.search(r'["\'](https:[^"\']+\.m3u8[^"\']*)["\']', html)
        if m:
            hls_url = m.group(1).replace("\\/", "/")
            print(f"Found via Regex: {hls_url}")

    if hls_url:
        print(f"\nTesting HLS URL: {hls_url[:60]}...")
        # 1. Test WITHOUT Cookies
        await test_stream(hls_url, httpx.Cookies(), "Without Cookies")
        
        # 2. Test WITH Cookies
        await test_stream(hls_url, cookies, "With Cookies")
    else:
        print("❌ No HLS URL found to test")

if __name__ == "__main__":
    asyncio.run(main())
