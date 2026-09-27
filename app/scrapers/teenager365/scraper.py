from __future__ import annotations

import json
import os
import re
from typing import Any, Optional
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from bs4 import BeautifulSoup

from app.core.pool import fetch_html as pool_fetch_html


def can_handle(host: str) -> bool:
    h = (host or "").lower().split(":")[0]
    if h.startswith("www."):
        h = h[4:]
    return h in {"teenager365.to", "teenager365.com"} or h.endswith((".teenager365.to", ".teenager365.com"))


def get_categories() -> list[dict]:
    try:
        current_dir = os.path.dirname(os.path.abspath(__file__))
        json_path = os.path.join(current_dir, "categories.json")
        with open(json_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []


async def fetch_page(url: str, *, referer: str | None = None) -> str:
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": referer or "https://teenager365.to/",
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


def _as_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(x).strip() for x in value if str(x).strip()]
    if isinstance(value, str):
        return [x.strip() for x in re.split(r"[,|\n]", value) if x.strip()]
    return [str(value).strip()] if str(value).strip() else []


def _best_image_url(img: Any) -> Optional[str]:
    if img is None:
        return None
    for key in ("data-src", "data-lazy-src", "data-original", "srcset", "src"):
        v = img.get(key)
        if not v:
            continue
        url = str(v).strip()
        if not url:
            continue
        if key == "srcset" and " " in url:
            url = url.split(" ", 1)[0].strip()
        if url.startswith("data:"):
            continue
        if url.startswith("//"):
            return f"https:{url}"
        return url
    return None


def _clean_title(title: str | None) -> Optional[str]:
    if not title:
        return None
    t = title.strip()
    for suffix in (" - Teenager365", " | Teenager365", " - Leaked", " leaked"):
        if t.lower().endswith(suffix.lower()):
            t = t[: -len(suffix)].strip()
    t = re.sub(r"\s*-\s*\d{4}$", "", t)
    return t or None


def _is_probable_ad_iframe(src: str) -> bool:
    s = (src or "").lower()
    return any(
        x in s
        for x in (
            "googlesyndication",
            "doubleclick",
            "adservice",
            "trudigo",
            "ronracepub",
            "vast",
            "ads.",
            "adsbygoogle",
            "datacorex9",
            "track.datacorex9.com",
            "sadbaguette.com",
            "engine.sadbaguette.com",
            "ag_custom_vlmcp",
            "ag_custom_vlmspot",
        )
    )


def _collect_embed_iframes(soup: BeautifulSoup) -> list[str]:
    seen: set[str] = set()
    embeds: list[str] = []

    for iframe in soup.select("iframe[src]"):
        src = (iframe.get("src") or "").strip()
        if not src:
            continue
        if src.startswith("//"):
            src = f"https:{src}"
        if not src.startswith("http"):
            continue
        if src in seen:
            continue
        if _is_probable_ad_iframe(src):
            continue
        seen.add(src)
        embeds.append(src)

    video_container = soup.select_one(".video-player, .video_embed, .player-container, .tab-content")
    if video_container:
        for iframe in video_container.select("iframe[src]"):
            src = (iframe.get("src") or "").strip()
            if not src:
                continue
            if src.startswith("//"):
                src = f"https:{src}"
            if not src.startswith("http"):
                continue
            if src in seen:
                continue
            if _is_probable_ad_iframe(src):
                continue
            seen.add(src)
            embeds.append(src)

    return embeds


def _canonical_embed_url(url: str) -> Optional[str]:
    parsed = urlparse(url)
    host = parsed.netloc.lower()
    match = re.match(r"^/video/(\d+)(?:/|$)", parsed.path, re.IGNORECASE)
    if match and host:
        return urlunparse((parsed.scheme or "https", host, f"/embed/{match.group(1)}", "", "", ""))
    match = re.match(r"^/embed/(\d+)(?:/|$)", parsed.path, re.IGNORECASE)
    if match and host:
        return urlunparse((parsed.scheme or "https", host, f"/embed/{match.group(1)}", "", "", ""))
    return None


def parse_video_page(html: str, url: str) -> dict[str, Any]:
    soup = BeautifulSoup(html, "lxml")
    json_ld = _parse_json_ld(soup)

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

    upload_date = _first_non_empty(
        _meta(soup, prop="article:published_time"),
        _meta(soup, prop="article:modified_time"),
    )
    category = _meta(soup, prop="article:section")

    tags: list[str] = []
    for tag in soup.find_all("meta", attrs={"property": "article:tag"}):
        content = (tag.get("content") or "").strip()
        if content:
            tags.append(content)

    duration = None
    uploader = None
    views = None

    text_blob = soup.get_text(" ", strip=True)
    dm = re.search(r"Duration:\s*(\d{1,3}:\d{2}(?::\d{2})?)", text_blob)
    if dm:
        duration = dm.group(1)
    else:
        dm2 = re.search(r"(\d{1,2}:\d{2}(?::\d{2})?)", text_blob)
        if dm2:
            duration = dm2.group(1)

    views_match = re.search(r"Views:\s*([\d,\.]+[KMBkmb]?|/?\d+)", text_blob, re.IGNORECASE)
    if views_match:
        views = views_match.group(1).replace(",", "").strip()

    for obj in json_ld:
        types = obj.get("@type")
        type_names = [str(x).lower() for x in types] if isinstance(types, list) else [str(types).lower()]
        if "videoobject" in type_names or "blogposting" in type_names:
            title = _clean_title(_first_non_empty(title, obj.get("name"), obj.get("headline"))) or title
            description = _first_non_empty(description, obj.get("description"))
            thumb = obj.get("thumbnailUrl") or obj.get("thumbnail")
            if isinstance(thumb, list) and thumb:
                thumb = next((x for x in thumb if isinstance(x, str) and x.strip()), None)
            thumbnail = _first_non_empty(thumbnail, thumb)
            upload_date = _first_non_empty(upload_date, obj.get("datePublished"), obj.get("dateModified"))
            author = obj.get("author")
            if isinstance(author, dict):
                uploader = _first_non_empty(author.get("name"), author.get("alternateName"))
            elif isinstance(author, str):
                uploader = author.strip() or None
            category = _first_non_empty(category, obj.get("articleSection"))
            tags.extend(_as_list(obj.get("keywords")))

    tags = list(dict.fromkeys([t for t in tags if t]))

    embed_urls = _collect_embed_iframes(soup)
    streams: list[dict[str, str]] = []
    canonical_embed = _canonical_embed_url(url)
    if canonical_embed:
        embed_urls.insert(0, canonical_embed)
    server_idx = 1
    for e in embed_urls:
        if any(s.get("url") == e for s in streams):
            continue
        streams.append({"url": e, "quality": f"Server {server_idx}", "format": "embed"})
        server_idx += 1

    default_url = None
    if streams:
        default_url = streams[0].get("url")

    return {
        "url": url,
        "title": title,
        "description": description,
        "thumbnail_url": thumbnail,
        "duration": duration,
        "views": views,
        "uploader_name": uploader,
        "category": category,
        "tags": tags,
        "upload_date": upload_date,
        "video": {
            "streams": streams,
            "hls": None,
            "default": default_url,
            "has_video": bool(streams),
        },
        "related_videos": [],
        "preview_url": None,
    }


async def scrape(url: str) -> dict[str, Any]:
    html = await fetch_page(url)
    return parse_video_page(html, url)


def _build_list_page_url(base_url: str, page: int) -> str:
    raw = (base_url or "").strip()
    if not raw.startswith("http"):
        raw = "https://" + raw.lstrip("/")
    parsed = urlparse(raw)
    scheme = parsed.scheme or "https"
    netloc = parsed.netloc or "teenager365.to"
    path = parsed.path or "/"
    query_items = dict(parse_qsl(parsed.query, keep_blank_values=True))

    if page <= 1:
        return urlunparse((scheme, netloc, path, "", urlencode(query_items), ""))

    cleaned_path = re.sub(r"/(?:page/)?\d+/?$", "/", path)
    if query_items.get("s"):
        query_items["page"] = str(page)
        return urlunparse((scheme, netloc, cleaned_path or "/", "", urlencode(query_items), ""))

    page_path = cleaned_path.rstrip("/") + f"/{page}/"
    return f"{scheme}://{netloc}{page_path}"


async def list_videos(base_url: str, page: int = 1, limit: int = 100) -> list[dict[str, Any]]:
    page_url = _build_list_page_url(base_url, page)
    try:
        html = await fetch_page(page_url)
    except Exception:
        return []

    soup = BeautifulSoup(html, "lxml")
    items: list[dict[str, Any]] = []
    seen: set[str] = set()

    for article in soup.select("article, div.post, div.video"):
        if len(items) >= limit:
            break
        a = article.select_one("h2 a, h3 a, .title a, .video-title a")
        if a is None:
            continue
        href = a.get("href") or ""
        if not href or href in seen:
            continue

        if not href.startswith("http"):
            if href.startswith("/"):
                href = f"https://teenager365.to{href}"
            else:
                continue

        if "teenager365" not in href.lower():
            continue

        if "/video/" not in href:
            continue

        img = article.select_one("img") or article.select_one("img.thumbnail")
        thumb = _best_image_url(img)

        title = a.get("title") or a.get_text(" ", strip=True)
        title = _clean_title(title) or "Unknown Video"

        ctext = article.get_text(" ", strip=True)
        duration = None
        views = None

        dm = re.search(r"(\d{1,2}:\d{2}(?::\d{2})?)", ctext)
        if dm:
            duration = dm.group(1)

        views_m = re.search(r"(\d[\d,\.]*\s*[KMBkmb]?)", ctext.replace(",", ""))
        if views_m:
            views = views_m.group(1).strip()

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

    if not items:
        for a in soup.select("a[href]"):
            if len(items) >= limit:
                break
            href = a.get("href") or ""
            if not href or href in seen:
                continue
            if not href.startswith("http"):
                if href.startswith("/"):
                    href = f"https://teenager365.to{href}"
                else:
                    continue
            if "teenager365" not in href.lower():
                continue
            if "/video/" not in href:
                continue

            thumb = _best_image_url(a.find("img"))
            if not thumb:
                continue

            title = a.get("title") or a.get_text(" ", strip=True)
            title = _clean_title(title) or "Unknown Video"

            container = a.find_parent(["article", "div", "li"]) or a
            ctext = container.get_text(" ", strip=True) if container else ""

            duration = None
            dm = re.search(r"(\d{1,2}:\d{2}(?::\d{2})?)", ctext)
            if dm:
                duration = dm.group(1)

            seen.add(href)
            items.append(
                {
                    "url": href,
                    "title": title,
                    "thumbnail_url": thumb,
                    "duration": duration,
                    "views": None,
                    "uploader_name": None,
                }
            )

    return items[:limit]
