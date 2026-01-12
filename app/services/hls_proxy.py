# HLS Proxy for IP-Restricted Video Streams

import httpx
from fastapi import HTTPException, status
from fastapi.responses import StreamingResponse, Response
from typing import AsyncGenerator, Dict
from app.config.settings import settings
import time
import asyncio

try:
    from curl_cffi.requests import AsyncSession
    HAS_CURL_CFFI = True
except ImportError:
    HAS_CURL_CFFI = False
    print("Warning: curl_cffi not found, falling back to httpx (less robust against 403s)")


class HLSProxy:
    """Proxy HLS video streams to bypass IP restrictions"""
    
    def __init__(self):
        self.timeout = settings.HLS_PROXY_TIMEOUT
        self.client = None
        # Simple in-memory cache for cookies: {referer_url: (cookies_dict, expiry_timestamp)}
        self.cookie_cache: Dict[str, tuple] = {}
        self.COOKIE_TTL = 300  # 5 minutes
    
    async def get_client(self, cookies: dict = None):
        """
        Get HTTP client. 
        Note: We create a NEW session for each request if using curl_cffi with specific cookies, 
        or manage a global one. For best cookie isolation, we might need dynamic sessions.
        But for performance, we'll try to reuse or just update cookies.
        """
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "*/*, application/vnd.t1c.int-27903",
            "Accept-Language": "en-US,en;q=0.9",
            "Sec-CH-UA": '"Google Chrome";v="120", "Chromium";v="120", "Not?A_Brand";v="24"',
            "Sec-CH-UA-Mobile": "?0",
            "Sec-CH-UA-Platform": '"Windows"',
            "Referer": "https://xhamster.com/" 
        }
        
        if HAS_CURL_CFFI:
            # For curl_cffi, it's safer to create a fresh session to ensure clean state and specific cookies
            return AsyncSession(
                timeout=self.timeout,
                headers=headers,
                impersonate="chrome120",
                verify=False,
                cookies=cookies
            )
        else:
            if not self.client:
                self.client = httpx.AsyncClient(
                    timeout=self.timeout,
                    follow_redirects=True,
                    headers=headers,
                    http2=True,
                    verify=False
                )
            if cookies:
                self.client.cookies = cookies
            return self.client
            
    async def _get_valid_cookies(self, referer: str) -> dict:
        """
        Get valid cookies for the given referer, utilizing cache or fetching fresh ones.
        """
        if not referer:
            return {}
            
        now = time.time()
        
        # Check cache
        if referer in self.cookie_cache:
            cookies, expiry = self.cookie_cache[referer]
            if now < expiry:
                return cookies
        
        # Fetch fresh cookies
        print(f"Fetching fresh cookies for: {referer}")
        try:
            # Use a temporary session to fetch the page
            if HAS_CURL_CFFI:
                async with AsyncSession(impersonate="chrome120", verify=False) as s:
                    await s.get(referer)
                    cookies = s.cookies
            else:
                async with httpx.AsyncClient(verify=False, follow_redirects=True) as c:
                    await c.get(referer)
                    cookies = dict(c.cookies)
            
            # Update cache
            if cookies:
                self.cookie_cache[referer] = (cookies, now + self.COOKIE_TTL)
                return cookies
        except Exception as e:
            print(f"Failed to fetch cookies for {referer}: {e}")
            
        return {}
    
    async def close(self):
        """Close HTTP client"""
        if self.client:
            if hasattr(self.client, 'close'):
                await self.client.close()
            elif hasattr(self.client, 'aclose'):
                await self.client.aclose()
            self.client = None
    
    async def proxy_m3u8(self, url: str, base_proxy_url: str, referer: str = None) -> str:
        """
        Proxy M3U8 playlist and rewrite URLs to point to proxy
        """
        # Ensure we have cookies
        cookies = await self._get_valid_cookies(referer)
        
        # Get client with cookies
        client = await self.get_client(cookies)
        
        headers = {}
        if referer:
            headers["Referer"] = referer
        
        try:
            response = await client.get(url, headers=headers)
            
            # handle error checking for both libs
            if hasattr(response, 'raise_for_status'):
                response.raise_for_status()
            elif response.status_code >= 400:
                raise HTTPException(status_code=response.status_code, detail=f"Upstream error: {response.status_code}")
            
            content = response.text
            lines = content.split('\n')
            modified_lines = []
            
            import urllib.parse
            encoded_referer = urllib.parse.quote(referer, safe='') if referer else ""
            referer_param = f"&referer={encoded_referer}" if referer else ""
            
            for line in lines:
                line = line.strip()
                
                # Rewrite segment URLs to point to our proxy
                if line and not line.startswith('#'):
                    # Check if it's a relative or absolute URL
                    if line.startswith('http'):
                        # Absolute URL - proxy it
                        # MUST encode the target URL
                        enc_line = urllib.parse.quote(line, safe='')
                        proxied_url = f"{base_proxy_url}?url={enc_line}{referer_param}"
                        modified_lines.append(proxied_url)
                    else:
                        # Relative URL - make it absolute first
                        base_url = url.rsplit('/', 1)[0]
                        absolute_url = f"{base_url}/{line}"
                        enc_abs = urllib.parse.quote(absolute_url, safe='')
                        proxied_url = f"{base_proxy_url}?url={enc_abs}{referer_param}"
                        modified_lines.append(proxied_url)
                else:
                    modified_lines.append(line)
            
            return '\n'.join(modified_lines)
        
        except Exception as e:
            # Catch generic errors to handle both curl_cffi and httpx exceptions
            status_code = getattr(e, 'response', None) and getattr(e.response, 'status_code', 502) or 502
            raise HTTPException(
                status_code=status_code,
                detail=f"Failed to fetch M3U8: {str(e)}"
            )
        finally:
             if HAS_CURL_CFFI:
                 await client.close()
    
    async def stream_segment(self, url: str, referer: str = None) -> StreamingResponse:
        """
        Stream video segment (TS file)
        """
        cookies = await self._get_valid_cookies(referer)
        client = await self.get_client(cookies)
        
        headers = {}
        if referer:
            headers["Referer"] = referer
        
        try:
            # Different streaming logic for curl_cffi vs httpx
            if HAS_CURL_CFFI and isinstance(client, AsyncSession):
                # curl_cffi streaming
                # We need to Keep the session open during streaming!
                # We can't use 'async with' comfortably if we want to return response
                # But StreamingResponse takes an async_generator.
                
                async def generate():
                    try:
                        async with client.stream("GET", url, headers=headers) as response:
                            if response.status_code >= 400:
                                 # We can't raise HTTPException inside generator easily, 
                                 # but this will stop stream
                                 return 
                            async for chunk in response.aiter_content():
                                 yield chunk
                    finally:
                         # Close session when done
                         await client.close()
                
                # Get content type via HEAD if possible, or default
                content_type = "video/mp2t"
                try:
                    head = await client.head(url, headers=headers)
                    content_type = head.headers.get("content-type", "video/mp2t")
                except:
                    pass
                
                return StreamingResponse(
                    generate(),
                    media_type=content_type,
                    headers={
                        "Accept-Ranges": "bytes",
                        "Cache-Control": "public, max-age=3600",
                        "Access-Control-Allow-Origin": "*",
                        "Access-Control-Allow-Methods": "GET, OPTIONS",
                        "Access-Control-Allow-Headers": "*",
                    }
                )
            else:
                # Fallback httpx streaming
                async def generate() -> AsyncGenerator[bytes, None]:
                    async with client.stream("GET", url, headers=headers) as response:
                        response.raise_for_status()
                        async for chunk in response.aiter_bytes(chunk_size=8192):
                            yield chunk
                
                head_response = await client.head(url, headers=headers)
                content_type = head_response.headers.get("content-type", "video/mp2t")
                
                return StreamingResponse(
                    generate(),
                    media_type=content_type,
                    headers={
                        "Accept-Ranges": "bytes",
                        "Cache-Control": "public, max-age=3600",
                        "Access-Control-Allow-Origin": "*",
                        "Access-Control-Allow-Methods": "GET, OPTIONS",
                        "Access-Control-Allow-Headers": "*",
                    }
                )
        
        except Exception as e:
            if HAS_CURL_CFFI: await client.close()
            raise HTTPException(
                status_code=500,
                detail=f"Failed to stream segment: {str(e)}"
            )
    
    async def proxy_request(self, url: str, referer: str = None) -> Response:
        """
        Generic proxy
        """
        cookies = await self._get_valid_cookies(referer)
        client = await self.get_client(cookies)
        
        headers = {}
        if referer:
            headers["Referer"] = referer
        
        try:
            if url.endswith('.m3u8') or 'm3u8' in url:
                return Response(
                    content="Use /api/hls/playlist endpoint for M3U8 files",
                    status_code=400
                )
            
            response = await client.get(url, headers=headers)
            
            if hasattr(response, 'raise_for_status'):
                response.raise_for_status()
            elif response.status_code >= 400:
                 pass # return error response below
            
            return Response(
                content=response.content,
                status_code=response.status_code,
                headers={
                    "Content-Type": response.headers.get("content-type", "application/octet-stream"),
                    "Cache-Control": "public, max-age=3600",
                    "Access-Control-Allow-Origin": "*",
                    "Access-Control-Allow-Methods": "GET, OPTIONS",
                    "Access-Control-Allow-Headers": "*"
                }
            )
        
        except Exception as e:
            status_code = getattr(e, 'response', None) and getattr(e.response, 'status_code', 502) or 502
            raise HTTPException(
                status_code=status_code,
                detail=f"Proxy request failed: {str(e)}"
            )
        finally:
            if HAS_CURL_CFFI: await client.close()


# Global HLS proxy instance
hls_proxy = HLSProxy()
