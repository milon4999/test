
from __future__ import annotations

import json
import re
from typing import Any, Optional

import httpx
from bs4 import BeautifulSoup


def can_handle(host: str) -> bool:
    return host.lower().endswith("xhamster.com")


def _best_image_url(img: Any) -> Optional[str]:
    if img is None:
        return None
    for k in ("data-src", "data-original", "data-lazy", "src"):
        v = img.get(k)
        if v and str(v).strip():
            return str(v).strip()
    return None


def _find_duration_like_text(node: Any) -> Optional[str]:
    try:
        text = node.get_text(" ", strip=True)
    except Exception:
        return None
    m = re.search(r"\b(?:\d{1,2}:){1,2}\d{2}\b", text)
    return m.group(0) if m else None


async def fetch_html(url: str) -> str:
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
    }

    async with httpx.AsyncClient(
        follow_redirects=True,
        timeout=httpx.Timeout(20.0, connect=20.0),
        headers=headers,
    ) as client:
        resp = await client.get(url)
        resp.raise_for_status()
        return resp.text


def _first_non_empty(*values: Optional[str]) -> Optional[str]:
    for v in values:
        if v is not None and str(v).strip() != "":
            return str(v).strip()
    return None


def _text(el: Any) -> Optional[str]:
    if el is None:
        return None
    t = getattr(el, "get_text", None)
    if callable(t):
        return t(strip=True) or None
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
        return [x.strip() for x in re.split(r"[,\n]", value) if x.strip()]
    return [str(value).strip()] if str(value).strip() else []


def _normalize_duration(seconds_or_iso: Any) -> Optional[str]:
    if seconds_or_iso is None:
        return None

    if isinstance(seconds_or_iso, (int, float)):
        total = int(seconds_or_iso)
        h = total // 3600
        m = (total % 3600) // 60
        s = total % 60
        if h > 0:
            return f"{h}:{m:02d}:{s:02d}"
        return f"{m}:{s:02d}"

    if isinstance(seconds_or_iso, str):
        v = seconds_or_iso.strip()
        m = re.fullmatch(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", v)
        if m:
            h = int(m.group(1) or 0)
            mm = int(m.group(2) or 0)
            s = int(m.group(3) or 0)
            if h > 0:
                return f"{h}:{mm:02d}:{s:02d}"
            return f"{mm}:{s:02d}"
        return v or None

    return str(seconds_or_iso).strip() or None


def _extract_views(video_obj: Optional[dict[str, Any]], html: str, soup: BeautifulSoup) -> Optional[str]:
    if video_obj:
        for key in ("interactionCount", "viewCount", "views"):
            v = video_obj.get(key)
            if v is not None and str(v).strip():
                return str(v).strip()

        stats = video_obj.get("interactionStatistic")
        if isinstance(stats, dict):
            v = stats.get("userInteractionCount") or stats.get("interactionCount")
            if v is not None and str(v).strip():
                return str(v).strip()
        elif isinstance(stats, list):
            for s in stats:
                if not isinstance(s, dict):
                    continue
                v = s.get("userInteractionCount") or s.get("interactionCount")
                if v is not None and str(v).strip():
                    return str(v).strip()

    for pattern in (
        r'"userInteractionCount"\s*:\s*"?([0-9][0-9,\.]*(?:\s*[KMB])?)"?',
        r'"interactionCount"\s*:\s*"?([0-9][0-9,\.]*(?:\s*[KMB])?)"?',
        r'"viewCount"\s*:\s*"?([0-9][0-9,\.]*(?:\s*[KMB])?)"?',
        r'"views"\s*:\s*"?([0-9][0-9,\.]*(?:\s*[KMB])?)"?',
    ):
        m = re.search(pattern, html, re.IGNORECASE)
        if m:
            v = m.group(1).replace(" ", "").upper()
            v = re.sub(r"[^0-9KMB\.]", "", v)
            v = v.rstrip(".")
            return v or None

    text = soup.get_text(" ", strip=True)
    m = re.search(r"(\d+(?:\.\d+)?)\s*([KMB])?\s*(?:views|view)\b", text, re.IGNORECASE)
    if m:
        num = m.group(1)
        suffix = (m.group(2) or "").upper()
        return f"{num}{suffix}" if suffix else num

    m = re.search(r"([0-9][0-9,\.\s]*)\s*(?:views|view)", text, re.IGNORECASE)
    if m:
        v = m.group(1).strip().replace(" ", "")
        v = v.rstrip(",")
        return v or None

    return None


def parse_page(html: str, url: str) -> dict[str, Any]:
    soup = BeautifulSoup(html, "lxml")

    og_title = _meta(soup, prop="og:title")
    og_desc = _meta(soup, prop="og:description")
    og_image = _meta(soup, prop="og:image")
    meta_desc = _meta(soup, name="description")

    title = _first_non_empty(og_title, _text(soup.find("title")))
    description = _first_non_empty(og_desc, meta_desc)
    thumbnail = _first_non_empty(og_image)

    json_ld = _parse_json_ld(soup)
    video_obj: Optional[dict[str, Any]] = None
    for obj in json_ld:
        t = obj.get("@type")
        if isinstance(t, list):
            if any(str(x).lower() == "videoobject" for x in t):
                video_obj = obj
                break
        if isinstance(t, str) and t.lower() == "videoobject":
            video_obj = obj
            break

    duration = None
    uploader = None
    category = None
    tags: list[str] = []

    if video_obj:
        title = _first_non_empty(title, video_obj.get("name"))
        description = _first_non_empty(description, video_obj.get("description"))

        thumb = video_obj.get("thumbnailUrl") or video_obj.get("thumbnail")
        if isinstance(thumb, list):
            thumb = next((x for x in thumb if isinstance(x, str) and x.strip()), None)
        thumbnail = _first_non_empty(thumbnail, thumb)

        duration = _normalize_duration(video_obj.get("duration"))

        author = video_obj.get("author")
        if isinstance(author, dict):
            uploader = _first_non_empty(author.get("name"), author.get("alternateName"))
        elif isinstance(author, str):
            uploader = author.strip() or None

        genre = video_obj.get("genre")
        if isinstance(genre, str):
            category = genre.strip() or None
        elif isinstance(genre, list) and genre:
            category = str(genre[0]).strip() or None

        tags = _as_list(video_obj.get("keywords"))

    if not tags:
        for a in soup.select('a[href*="/tags/"]'):
            t = _text(a)
            if t:
                tags.append(t)
    tags = list(dict.fromkeys([t for t in tags if t]))

    if not category:
        for a in soup.select('a[href*="/categories/"]'):
            t = _text(a)
            if t:
                category = t
                break

    if not uploader:
        for a in soup.select('a[href*="/users/"]'):
            t = _text(a)
            if t:
                uploader = t
                break

    views = _extract_views(video_obj, html, soup)

    if not duration:
        m = re.search(r"\b(\d{1,2}:\d{2}(?::\d{2})?)\b", soup.get_text(" ", strip=True))
        if m:
            duration = m.group(1)

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
    }


async def scrape(url: str) -> dict[str, Any]:
    html = await fetch_html(url)
    return parse_page(html, url)


async def list_videos(base_url: str, page: int = 1, limit: int = 20) -> list[dict[str, Any]]:
    root = base_url if base_url.endswith("/") else base_url + "/"

    effective_limit: int | None = None
    if limit is not None and limit > 0:
        effective_limit = limit

    candidates: list[str] = []
    if page <= 1:
        candidates.append(root)
    else:
        candidates.extend(
            [
                f"{root}?page={page}",
                f"{root}newest/{page}/",
                f"{root}newest/{page}",
                f"{root}videos?page={page}",
            ]
        )

    html = ""
    used = ""
    last_exc: Exception | None = None
    for c in candidates:
        try:
            html = await fetch_html(c)
            used = c
            if html:
                break
        except Exception as e:
            last_exc = e
            continue

    if not html:
        if last_exc:
            raise last_exc
        return []

    soup = BeautifulSoup(html, "lxml")
    base_uri = httpx.URL(used)

    items: list[dict[str, Any]] = []
    seen: set[str] = set()

    for a in soup.select('a[href*="/videos/"]'):
        href = a.get("href")
        if not href:
            continue

        if "/videos/" not in href:
            continue

        try:
            abs_url = str(base_uri.join(href))
        except Exception:
            continue

        if abs_url in seen:
            continue

        img = a.find("img")
        thumb = _best_image_url(img)

        # Try to find a specific title element first
        title_el = a.find(class_=re.compile(r"video-thumb-info__name"))
        title = _first_non_empty(
            _text(title_el),
            img.get("alt") if img else None,
            a.get("title"),
            _text(a),  # Fallback to the original broad search
        )

        duration = _find_duration_like_text(a)

        # Extract metadata from the video card or its parent container
        # Be conservative - only look at the anchor and its immediate parent/siblings
        # to avoid matching page-level elements
        card = a.parent if hasattr(a, 'parent') and a.parent else a

        # Extract views
        views = None
        views_el = card.find(class_=re.compile(r"video-thumb-views|video-thumb-info__views|entity-views-container__value"))
        if views_el:
            views_text = _text(views_el)
            if views_text:
                # Clean up the views text (e.g., "1.2M views" -> "1.2M")
                views = re.sub(r"\s*views?\s*$", "", views_text, flags=re.IGNORECASE).strip()

        # Extract uploader name with avatar
        uploader_name = None
        uploader_avatar_url = None
        
        uploader_el = card.find(class_=re.compile(r"video-uploader__name|video-thumb-uploader__name|video-user-info__name"))
        if not uploader_el:
             # Try finding uploader link within the card only
            uploader_link = card.find('a', href=re.compile(r"/users/|/channels/"))
            if uploader_link:
                uploader_name = _text(uploader_link)
        else:
            uploader_name = _text(uploader_el)
            
        # Extract uploader logo/avatar
        # Typical classes: video-uploader-logo, video-thumb-uploader__logo, etc.
        logo_el = card.find(class_=re.compile(r"video-uploader-logo|video-thumb-uploader__logo|video-user-info__avatar"))
        if logo_el:
            # Check for data-background-image first (often used for avatars)
            bg_img = logo_el.get("data-background-image")
            if bg_img:
                uploader_avatar_url = str(bg_img).strip()
            elif logo_el.name == 'img':
                uploader_avatar_url = _best_image_url(logo_el)
            else:
                img_in_logo = logo_el.find('img')
                if img_in_logo:
                     uploader_avatar_url = _best_image_url(img_in_logo)
        
        # If still no avatar, try checking the uploader link for an image
        if not uploader_avatar_url:
             uploader_link = card.find('a', href=re.compile(r"/users/|/channels/"))
             if uploader_link:
                 img = uploader_link.find('img')
                 if img:
                     # Check if it looks like an avatar (often small or specific class)
                     if "avatar" in str(img.get("class", "")) or "logo" in str(img.get("class", "")):
                         uploader_avatar_url = _best_image_url(img)

        # If no thumbnail, skip (usually not a card)
        if not thumb:
            continue

        seen.add(abs_url)
        items.append(
            {
                "url": abs_url,
                "title": title,
                "thumbnail_url": thumb,
                "duration": duration,
                "views": views,
                "uploader_name": uploader_name,
                "uploader_avatar_url": uploader_avatar_url,
            }
        )



    return items


async def crawl_videos(
    base_url: str,
    start_page: int = 1,
    max_pages: int = 5,
    per_page_limit: int = 0,
    max_items: int = 500,
) -> list[dict[str, Any]]:
    if start_page < 1:
        start_page = 1
    if max_pages < 1:
        max_pages = 1
    if per_page_limit < 0:
        per_page_limit = 0
    if max_items < 1:
        max_items = 1

    results: list[dict[str, Any]] = []
    seen: set[str] = set()

    # If per_page_limit==0, we try to return "all cards on the page" by using no limit.
    for page in range(start_page, start_page + max_pages):
        page_items = await list_videos(
            base_url=base_url,
            page=page,
            limit=per_page_limit,
        )

        if not page_items:
            break

        for it in page_items:
            url = str(it.get("url") or "").strip()
            if not url or url in seen:
                continue
            seen.add(url)
            results.append(it)
            if len(results) >= max_items:
                return results

    return results
