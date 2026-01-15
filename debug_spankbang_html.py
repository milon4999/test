import httpx
import asyncio

async def get_sb_html():
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
        "Cookie": "age_verified=1; sb_theme=dark",
    }
    url = "https://spankbang.com/"
    print(f"Fetching {url}...")
    async with httpx.AsyncClient(headers=headers, follow_redirects=True) as client:
        resp = await client.get(url)
        print(f"Status: {resp.status_code}")
        with open("sb_debug.html", "w", encoding="utf-8") as f:
            f.write(resp.text)
        print("Saved to sb_debug.html")

if __name__ == "__main__":
    asyncio.run(get_sb_html())
