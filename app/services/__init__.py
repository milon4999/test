"""
Services package

Modules:
- global_search: Multi-site search
- video_streaming: Video URL extraction
"""

from .global_search import global_search, global_trending
from .video_streaming import get_video_info, get_stream_url

__all__ = ['global_search', 'global_trending', 'get_video_info', 'get_stream_url']
