import httpx
import asyncio
import json

async def probe_externulls_api():
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36",
        "Origin": "https://beeg.com",
        "Referer": "https://beeg.com/",
        "Accept": "application/json, text/plain, */*",
    }
    
    # 1. Homepage (Featured)
    url_home = "https://store.externulls.com/facts/tag?id=27173&limit=5&offset=0"
    
    # 2. Category (Asian)
    url_cat = "https://store.externulls.com/facts/tag?slug=Asian&limit=5&offset=0"
    
    async with httpx.AsyncClient(headers=headers, follow_redirects=True) as client:
        print(f"Fetching Home: {url_home}...")
        resp = await client.get(url_home)
        print(f"Status: {resp.status_code}")
        if resp.status_code == 200:
            data = resp.json()
            with open("beeg_api_response.json", "w") as f:
                json.dump(data, f, indent=2)
            print("Saved beeg_api_response.json")
            
        print(f"Fetching Cat: {url_cat}...")
        resp = await client.get(url_cat)
        print(f"Status: {resp.status_code}")

if __name__ == "__main__":
    asyncio.run(probe_externulls_api())
