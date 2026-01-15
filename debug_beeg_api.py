import httpx
import asyncio
import json

async def probe_beeg_api():
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36",
        "Referer": "https://beeg.com/",
        "Accept": "application/json, text/plain, */*",
    }
    
    # Potential endpoints
    endpoints = [
        "https://beeg.com/api/v6/index/index/0/pc", # Home page 1?
        "https://beeg.com/api/v6/video/listing",
        "https://beeg.com/api/v1/index",
    ]
    
    async with httpx.AsyncClient(headers=headers, follow_redirects=True) as client:
        for url in endpoints:
            print(f"Trying {url}...")
            try:
                resp = await client.get(url)
                print(f"Status: {resp.status_code}")
                if resp.status_code == 200:
                    try:
                        data = resp.json()
                        print("SUCCESS! JSON received.")
                        # Print first item keys
                        if isinstance(data, list) and len(data) > 0:
                            print("First item keys:", data[0].keys())
                        elif isinstance(data, dict):
                            print("Keys:", data.keys())
                            if 'videos' in data:
                                print("Videos found:", len(data['videos']))
                        
                        # Save sample
                        with open("beeg_api_sample.json", "w") as f:
                            json.dump(data, f, indent=2)
                        return
                    except:
                        print("Not JSON content")
                        print(resp.text[:200])
            except Exception as e:
                print(f"Error: {e}")

if __name__ == "__main__":
    asyncio.run(probe_beeg_api())
