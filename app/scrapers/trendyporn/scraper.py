from __future__ import annotations

import json
import os
import re
from typing import Any, Optional
from urllib.parse import parse_qsl, quote, unquote, urlencode, urljoin, urlparse, urlunparse

from bs4 import BeautifulSoup

from app.core.pool import fetch_html as pool_fetch_html

_BASE = "https://www.trendyporn.com"
_VIDEO_PATH_RE = re.compile(r"^/video/[^/]+\.html$", flags=re.IGNORECASE)


def can_handle(host: str) -> bool:
    h = (host or "").lower().split(":")[0]
    if h.startswith("www."):
        h = h[4:]
    return h == "trendyporn.com" or h.endswith(".trendyporn.com")


def get_categories() -> list[dict]:
    try:
        current_dir = os.path.dirname(os.path.abspath(__file__))
        json_path = os.path.join(current_dir, "categories.json")
        with open(json_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []


async def fetch_page(url: str, referer: str = f"{_BASE}/") -> str:
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": referer,
    }
    return await pool_fetch_html(url, headers=headers)


def _first_non_empty(*values: Optional[str]) -> Optional[str]:
    for v in values:
        if v is not None and str(v).strip():
            return str(v).strip()
    return None


def _meta(soup: BeautifulSoup, *, prop: str | None = None, name: str | None = None) -> Optional[str]:
    if prop:
        tag = soup.find("meta", attrs={"property": prop})
        if tag and tag.get("content"):
            return str(tag.get("content")).strip()
    if name:
        tag = soup.find("meta", attrs={"name": name})
        if tag and tag.get("content"):
            return str(tag.get("content")).strip()
    return None


def _clean_title(title: str | None) -> Optional[str]:
    if not title:
        return None
    t = str(title).strip()
    for suffix in (
        " - Videos - Trendy Porn Movies Tube",
        " - Trendy Porn Movies Tube",
        " | Trendy Porn Movies Tube",
        " - TrendyPorn Tube",
        " | TrendyPorn Tube",
    ):
        if t.endswith(suffix):
            t = t[: -len(suffix)].strip()
    return t or None


def _clean_list_title(title: str | None) -> Optional[str]:
    t = _clean_title(title)
    if not t:
        return None
    # Remove trailing card metadata accidentally captured in title text.
    t = re.sub(r"\s+\d{1,2}:\d{2}(?::\d{2})?\s+\d{1,3}%\s+\d[\d\.\s]*[kKmMbB]?\s*$", "", t).strip()
    t = re.sub(r"\s+\d{1,2}:\d{2}(?::\d{2})?\s*$", "", t).strip()
    return t or None


def _normalize_numberish(value: str | None) -> Optional[str]:
    if not value:
        return None
    txt = str(value).strip().replace(",", "").replace("\u00a0", " ")
    txt = re.sub(r"\s+", "", txt)
    txt = re.sub(r"[^0-9KMBkmb\.]", "", txt)
    return txt.upper() or None


def _extract_duration(text: str | None) -> Optional[str]:
    if not text:
        return None
    m = re.search(r"\b(?:\d{1,2}:){1,2}\d{2}\b", text)
    return m.group(0) if m else None


def _extract_views(text: str | None) -> Optional[str]:
    if not text:
        return None
    m = re.search(r"(\d[\d,\.]*)\s*views\b", text, flags=re.IGNORECASE)
    if not m:
        return None
    return _normalize_numberish(m.group(1))


def _as_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(x).strip() for x in value if str(x).strip()]
    if isinstance(value, str):
        return [x.strip() for x in re.split(r"[,|\n]", value) if x.strip()]
    return [str(value).strip()] if str(value).strip() else []


def _parse_json_ld(soup: BeautifulSoup) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for script in soup.find_all("script", attrs={"type": "application/ld+json"}):
        raw = script.string or script.get_text(strip=False)
        if not raw:
            continue
        try:
            parsed = json.loads(raw)
        except Exception:
            continue
        if isinstance(parsed, dict):
            if "@graph" in parsed and isinstance(parsed["@graph"], list):
                out.extend([x for x in parsed["@graph"] if isinstance(x, dict)])
            else:
                out.append(parsed)
        elif isinstance(parsed, list):
            out.extend([x for x in parsed if isinstance(x, dict)])
    return out


def _best_image_url(img: Any) -> Optional[str]:
    if img is None:
        return None
    for key in ("data-original", "data-src", "data-lazy-src", "srcset", "src"):
        v = img.get(key)
        if not v:
            continue
        url = str(v).strip()
        if not url:
            continue
        if key == "srcset" and " " in url:
            url = url.split(" ", 1)[0].strip()
        if url.startswith("//"):
            url = f"https:{url}"
        low = url.lower()
        if "placeholder" in low:
            continue
        return url
    return None


def _normalize_video_href(href: str) -> Optional[str]:
    href = (href or "").strip()
    if not href:
        return None
    if href.startswith("//"):
        href = f"https:{href}"
    elif href.startswith("/"):
        href = f"{_BASE}{href}"
    if not href.startswith("http"):
        return None

    parsed = urlparse(href)
    if "trendyporn.com" not in parsed.netloc.lower():
        return None
    if not _VIDEO_PATH_RE.match(parsed.path or "", ):
        return None
    if parsed.query:
        return None

    return urlunparse(("https", "www.trendyporn.com", parsed.path, "", "", ""))


def _detect_media_format(url: str) -> Optional[str]:
    low = (url or "").lower()
    path = urlparse(url).path.lower() if url else ""
    if "/get_file/" in low:
        return "mp4"
    if path.endswith(".m3u8"):
        return "hls"
    if path.endswith(".mp4"):
        return "mp4"
    return None


def _extract_inline_urls(html: str) -> list[str]:
    unescaped = html.replace("\\/", "/").replace("\\u0026", "&")
    urls: list[str] = []
    for m in re.finditer(r"https?://[^\s\"'<>]+", unescaped, flags=re.IGNORECASE):
        u = m.group(0).strip().rstrip("),'\"")
        if u and "trendyporn.com" in u.lower() and _detect_media_format(u):
            urls.append(u)
    return list(dict.fromkeys(urls))


def _is_probable_ad_iframe(src: str) -> bool:
    s = (src or "").lower()
    ad_hosts_or_markers = (
        "abbnfo.info",
        "ptwmemd.com",
        "camworms",
        "ctwmsg.com",
        "vcmdiawe.com",
        "bngdin.com",
        "bongacams",
        "spyglass",
        "reklon.net",
        "doubleclick",
        "googlesyndication",
        "adservice",
        "exoclick",
        "trafficjunky",
        "/promo.php",
        "dynamic_banner",
    )
    return any(marker in s for marker in ad_hosts_or_markers)


def _extract_video_id(url: str) -> Optional[str]:
    m = re.search(r"/video/[^/]+?-(\d+)\.html", url or "", flags=re.IGNORECASE)
    return m.group(1) if m else None


def _extract_native_embed_url(html: str, video_url: str) -> Optional[str]:
    m = re.search(r"https?://(?:www\.)?trendyporn\.com/embed/(\d+)\b", html, flags=re.IGNORECASE)
    if m:
        return f"https://www.trendyporn.com/embed/{m.group(1)}"
    vid = _extract_video_id(video_url)
    if vid:
        return f"https://www.trendyporn.com/embed/{vid}"
    return None


def _extract_streams(soup: BeautifulSoup, html: str, video_url: str) -> dict[str, Any]:
    streams: list[dict[str, str]] = []
    seen: set[str] = set()

    # KVS videojs player: <video><source src="https://videos.trendyporn.com/...mp4?md5=..&expires=.."></video>
    for video in soup.select("video"):
        direct = (video.get("src") or "").strip()
        if direct:
            if direct.startswith("//"):
                direct = f"https:{direct}"
            elif direct.startswith("/"):
                direct = urljoin(video_url, direct)
            fmt = _detect_media_format(direct)
            if direct.startswith("http") and fmt and direct not in seen:
                seen.add(direct)
                streams.append({"url": direct, "quality": "source", "format": fmt})
        for source in video.select("source[src]"):
            src = (source.get("src") or "").strip()
            if not src:
                continue
            if src.startswith("//"):
                src = f"https:{src}"
            elif src.startswith("/"):
                src = urljoin(video_url, src)
            fmt = _detect_media_format(src)
            if not src.startswith("http") or src in seen or not fmt:
                continue
            seen.add(src)
            streams.append({"url": src, "quality": "source", "format": fmt})

    # Inline script / download-modal MP4 references (signed CDN URLs).
    for src in _extract_inline_urls(html):
        if src in seen:
            continue
        fmt = _detect_media_format(src)
        if not fmt:
            continue
        seen.add(src)
        streams.append({"url": src, "quality": "source", "format": fmt})

    for iframe in soup.select("iframe[src]"):
        src = (iframe.get("src") or "").strip()
        if not src:
            continue
        if src.startswith("//"):
            src = f"https:{src}"
        elif src.startswith("/"):
            src = urljoin(video_url, src)
        if not src.startswith("http") or src in seen or _is_probable_ad_iframe(src):
            continue
        seen.add(src)
        streams.append({"url": src, "quality": "embed", "format": "embed"})

    native_embed = _extract_native_embed_url(html, video_url)
    if native_embed and native_embed not in seen:
        seen.add(native_embed)
        streams.append({"url": native_embed, "quality": "trendyporn", "format": "embed"})

    uniq = list(dict.fromkeys((json.dumps(s, sort_keys=True) for s in streams)))
    materialized = [json.loads(s) for s in uniq]

    def _score(item: dict[str, str]) -> tuple[int, int]:
        fmt = (item.get("format") or "").lower()
        if fmt == "mp4":
            return (3, 1)
        if fmt == "hls":
            return (2, 0)
        if fmt == "embed" and "trendyporn.com/embed/" in (item.get("url") or "").lower():
            return (1, 1)
        return (1, 0)

    materialized.sort(key=_score, reverse=True)

    default_url = None
    for preferred in ("mp4", "hls", "embed"):
        m = next((s for s in materialized if s.get("format") == preferred), None)
        if m:
            default_url = m.get("url")
            break

    hls_url = next((s.get("url") for s in materialized if s.get("format") == "hls"), None)
    return {
        "streams": materialized,
        "hls": hls_url,
        "default": default_url,
        "has_video": bool(materialized),
    }


def parse_video_page(html: str, url: str) -> dict[str, Any]:
    soup = BeautifulSoup(html, "lxml")
    json_ld = _parse_json_ld(soup)
    text_blob = soup.get_text(" ", strip=True)

    title = _clean_title(
        _first_non_empty(
            _meta(soup, prop="og:title"),
            _meta(soup, name="twitter:title"),
            soup.select_one("h1").get_text(" ", strip=True) if soup.select_one("h1") else None,
            soup.title.get_text(strip=True) if soup.title else None,
        )
    ) or "Unknown Video"

    description = _first_non_empty(
        _meta(soup, prop="og:description"),
        _meta(soup, name="twitter:description"),
        _meta(soup, name="description"),
    )
    thumbnail = _first_non_empty(_meta(soup, prop="og:image"), _meta(soup, name="twitter:image"))
    if thumbnail and thumbnail.startswith("//"):
        thumbnail = f"https:{thumbnail}"

    duration = None
    views = None
    upload_date = _first_non_empty(
        _meta(soup, prop="article:published_time"),
        _meta(soup, prop="article:modified_time"),
    )
    tags: list[str] = []
    category = None
    uploader_name = None

    dm = re.search(r"Runtime:\s*\**\s*(\d{1,2}:\d{2}(?::\d{2})?)", text_blob, flags=re.IGNORECASE)
    if dm:
        duration = dm.group(1)
    big_views = soup.select_one(".big-views span") or soup.select_one(".big-views-xs span")
    if big_views is not None:
        views = _extract_views(big_views.get_text(" ", strip=True))
    if views is None:
        views = _extract_views(text_blob)
    dm2 = re.search(r"Added Date:\s*\**\s*(\d{4}-\d{2}-\d{2})", text_blob, flags=re.IGNORECASE)
    if dm2:
        upload_date = _first_non_empty(upload_date, dm2.group(1))

    for tag_div in soup.select("div.video-tags"):
        div_text = tag_div.get_text(" ", strip=True)
        if re.match(r"^Tags:", div_text, flags=re.IGNORECASE):
            tags.extend(a.get_text(" ", strip=True) for a in tag_div.select('a[href*="/tag/"]'))
        elif re.match(r"^Categories:", div_text, flags=re.IGNORECASE):
            for anchor in tag_div.select('a[href*="/channels/"]'):
                c = anchor.get_text(" ", strip=True)
                if c:
                    category = c
                    break
    if not tags:
        for anchor in soup.select('a[href*="/tag/"]'):
            t = anchor.get_text(" ", strip=True)
            if t:
                tags.append(t)
    keywords = _meta(soup, name="keywords")
    if keywords:
        tags.extend(_as_list(keywords))
    tags = list(dict.fromkeys([t for t in tags if t]))

    for obj in json_ld:
        types = obj.get("@type")
        tnames = [str(x).lower() for x in types] if isinstance(types, list) else [str(types).lower()]
        if "videoobject" not in tnames:
            continue
        title = _clean_title(_first_non_empty(obj.get("name"), title)) or title
        description = _first_non_empty(description, obj.get("description"))
        thumb = obj.get("thumbnailUrl") or obj.get("thumbnail")
        if isinstance(thumb, list):
            thumb = next((x for x in thumb if isinstance(x, str) and x.strip()), None)
        thumbnail = _first_non_empty(thumbnail, thumb)
        duration = _first_non_empty(duration, str(obj.get("duration")) if obj.get("duration") else None)
        upload_date = _first_non_empty(upload_date, obj.get("datePublished"), obj.get("dateModified"))
        author = obj.get("author")
        if isinstance(author, dict):
            uploader_name = _first_non_empty(author.get("name"), uploader_name)
        elif isinstance(author, str):
            uploader_name = _first_non_empty(author, uploader_name)

    video = _extract_streams(soup, html, url)

    return {
        "url": url,
        "title": title,
        "description": description,
        "thumbnail_url": thumbnail,
        "duration": duration,
        "views": views,
        "uploader_name": uploader_name,
        "category": category,
        "tags": tags,
        "upload_date": upload_date,
        "video": video,
        "related_videos": [],
        "preview_url": None,
    }


async def scrape(url: str) -> dict[str, Any]:
    html = await fetch_page(url, referer=url)
    return parse_video_page(html, url)


def _build_list_page_url(base_url: str, page: int) -> str:
    raw = (base_url or "").strip()
    if not raw.startswith("http"):
        raw = "https://" + raw.lstrip("/")
    p = urlparse(raw)
    scheme = p.scheme or "https"
    netloc = p.netloc or "www.trendyporn.com"
    path = p.path or "/"
    query_items = dict(parse_qsl(p.query, keep_blank_values=True))

    # searchgate.php ignores `page`; the canonical searchable route is /search/{term}/page{N}.html
    if "searchgate.php" in path:
        term = (query_items.get("q") or "").strip()
        if not term:
            m = re.search(r"[?&]q=([^&]+)", raw, flags=re.IGNORECASE)
            term = unquote(m.group(1)) if m else ""
        if term:
            path = "/search/" + quote(term) + "/"
            query_items = {}

    if page <= 1:
        return urlunparse((scheme, netloc, path, "", urlencode(query_items), ""))

    # KVS style pages live next to the last segment: /most-recent/page2.html
    path = re.sub(r"page\d+\.html$", "", path)
    paged_path = path.rstrip("/") + f"/page{page}.html"
    if not paged_path.startswith("/"):
        paged_path = "/" + paged_path
    return urlunparse((scheme, netloc, paged_path, "", urlencode(query_items), ""))


async def list_videos(base_url: str, page: int = 1, limit: int = 100) -> list[dict[str, Any]]:
    page_url = _build_list_page_url(base_url, page)
    try:
        html = await fetch_page(page_url, referer=base_url or f"{_BASE}/")
    except Exception:
        return []

    soup = BeautifulSoup(html, "lxml")
    items: list[dict[str, Any]] = []
    seen: set[str] = set()

    for a in soup.select("a[href]"):
        if len(items) >= limit:
            break
        href = _normalize_video_href(a.get("href") or "")
        if not href or href in seen:
            continue

        container = (
            a.find_parent("div", class_="vid")
            or a.find_parent("div", class_="well-sm")
            or a.find_parent(["article", "li", "div"])
            or a
        )
        img = a.find("img") or (container.find("img") if container else None)
        thumb = _best_image_url(img)
        if not thumb:
            continue

        title_el = container.select_one("span.video-title") if container else None
        duration_el = container.select_one(".duration") if container else None
        views_el = container.select_one(".video-views") if container else None

        title = _first_non_empty(
            title_el.get_text(" ", strip=True) if title_el else None,
            a.get("title"),
            (img.get("alt") if img and img.get("alt") else None),
            a.get_text(" ", strip=True),
        )
        title = _clean_list_title(title) or "Unknown Video"

        duration = _extract_duration(duration_el.get_text(" ", strip=True) if duration_el else None)
        views = _extract_views(views_el.get_text(" ", strip=True) if views_el else (container.get_text(" ", strip=True) if container else None))

        seen.add(href)
        items.append(
            {
                "url": href,
                "title": title,
                "thumbnail_url": thumb,
                "duration": duration,
                "views": views,
                "uploader_name": None,
            }
        )

    return items[:limit]
