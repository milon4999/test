# HLS Proxy for IP-Restricted Video Streams

import httpx
from fastapi import HTTPException, status
from fastapi.responses import StreamingResponse, Response
from typing import AsyncGenerator
from config import settings


class HLSProxy:
    """Proxy HLS video streams to bypass IP restrictions"""
    
    def __init__(self):
        self.timeout = settings.HLS_PROXY_TIMEOUT
        self.client = None
    
    async def get_client(self) -> httpx.AsyncClient:
        """Get or create HTTP client"""
        if not self.client:
            self.client = httpx.AsyncClient(
                timeout=self.timeout,
                follow_redirects=True,
                headers={
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                    "Accept": "*/*",
                    "Accept-Language": "en-US,en;q=0.9",
                    "Origin": "https://xhamster.com",
                    "Referer": "https://xhamster.com/"
                }
            )
        return self.client
    
    async def close(self):
        """Close HTTP client"""
        if self.client:
            await self.client.aclose()
            self.client = None
    
    async def proxy_m3u8(self, url: str, base_proxy_url: str) -> str:
        """
        Proxy M3U8 playlist and rewrite URLs to point to proxy
        
        Args:
            url: Original M3U8 URL
            base_proxy_url: Base URL of our proxy endpoint
        
        Returns:
            Modified M3U8 content with proxied URLs
        """
        client = await self.get_client()
        
        try:
            response = await client.get(url)
            response.raise_for_status()
            
            content = response.text
            lines = content.split('\n')
            modified_lines = []
            
            for line in lines:
                line = line.strip()
                
                # Rewrite segment URLs to point to our proxy
                if line and not line.startswith('#'):
                    # Check if it's a relative or absolute URL
                    if line.startswith('http'):
                        # Absolute URL - proxy it
                        proxied_url = f"{base_proxy_url}?url={line}"
                        modified_lines.append(proxied_url)
                    else:
                        # Relative URL - make it absolute first
                        base_url = url.rsplit('/', 1)[0]
                        absolute_url = f"{base_url}/{line}"
                        proxied_url = f"{base_proxy_url}?url={absolute_url}"
                        modified_lines.append(proxied_url)
                else:
                    modified_lines.append(line)
            
            return '\n'.join(modified_lines)
        
        except httpx.HTTPStatusError as e:
            raise HTTPException(
                status_code=e.response.status_code,
                detail=f"Failed to fetch M3U8: {e.response.status_code}"
            )
        except httpx.RequestError as e:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"Failed to fetch M3U8: {str(e)}"
            )
    
    async def stream_segment(self, url: str) -> StreamingResponse:
        """
        Stream video segment (TS file)
        
        Args:
            url: URL of the video segment
        
        Returns:
            Streaming response with video data
        """
        client = await self.get_client()
        
        try:
            async def generate() -> AsyncGenerator[bytes, None]:
                async with client.stream("GET", url) as response:
                    response.raise_for_status()
                    async for chunk in response.aiter_bytes(chunk_size=8192):
                        yield chunk
            
            # Get content type from original response
            head_response = await client.head(url)
            content_type = head_response.headers.get("content-type", "video/mp2t")
            
            return StreamingResponse(
                generate(),
                media_type=content_type,
                headers={
                    "Accept-Ranges": "bytes",
                    "Cache-Control": "public, max-age=3600",
                }
            )
        
        except httpx.HTTPStatusError as e:
            raise HTTPException(
                status_code=e.response.status_code,
                detail=f"Failed to stream segment: {e.response.status_code}"
            )
        except httpx.RequestError as e:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"Failed to stream segment: {str(e)}"
            )
    
    async def proxy_request(self, url: str) -> Response:
        """
        Generic proxy for any URL
        
        Args:
            url: URL to proxy
        
        Returns:
            Response with proxied content
        """
        client = await self.get_client()
        
        try:
            # Determine if it's an M3U8 playlist
            if url.endswith('.m3u8') or 'm3u8' in url:
                # This is a playlist - we need to rewrite URLs
                # Note: base_proxy_url should be passed from the endpoint
                return Response(
                    content="Use /api/hls/playlist endpoint for M3U8 files",
                    status_code=400
                )
            
            # Stream the content
            response = await client.get(url)
            response.raise_for_status()
            
            return Response(
                content=response.content,
                status_code=response.status_code,
                headers={
                    "Content-Type": response.headers.get("content-type", "application/octet-stream"),
                    "Cache-Control": "public, max-age=3600",
                }
            )
        
        except httpx.HTTPStatusError as e:
            raise HTTPException(
                status_code=e.response.status_code,
                detail=f"Proxy request failed: {e.response.status_code}"
            )
        except httpx.RequestError as e:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"Proxy request failed: {str(e)}"
            )


# Global HLS proxy instance
hls_proxy = HLSProxy()
