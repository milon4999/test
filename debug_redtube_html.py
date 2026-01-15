import asyncio
import httpx

async def fetch():
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36",
    }
    url = "https://www.redtube.com/"
    print(f"Fetching {url}...")
    async with httpx.AsyncClient(follow_redirects=True, headers=headers) as client:
        resp = await client.get(url)
        print(f"Status: {resp.status_code}")
        with open("debug_redtube.html", "w", encoding="utf-8") as f:
            f.write(resp.text)
        print("Saved to debug_redtube.html")

if __name__ == "__main__":
    asyncio.run(fetch())
