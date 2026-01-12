import asyncio
import httpx

URL = "https://video-nss.xhcdn.com/0707dffed4ed4614e135747e376c06cbab937231ab371472c3d96ad068,1768269600/media=hls4/multi=256x144:144p,426x240:240p,854x480:480p/024/717/008/_TPL_.h264.mp4.m3u8"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "*/*",
    "Accept-Language": "en-US,en;q=0.9",
    "Origin": "https://xhamster.com",
    "Referer": "https://xhamster.com/videos/my-stepbrother-came-in-my-bedroom-and-fuck-my-pussy-hardly-shopna25-xh8YYPL"
}

async def test():
    print(f"Testing URL: {URL}")
    print("Headers:", HEADERS)
    
    async with httpx.AsyncClient(verify=False) as client:
        resp = await client.get(URL, headers=HEADERS)
        print(f"Status: {resp.status_code}")
        print(f"Response: {resp.text[:200]}")

if __name__ == "__main__":
    asyncio.run(test())
