from __future__ import annotations

import codecs
import json
import os
import re
from typing import Any, Optional
from urllib.parse import quote, urljoin, urlparse, urlunparse

from bs4 import BeautifulSoup

from app.core.pool import fetch_html as pool_fetch_html


_HOST = "pussyspace.com"

_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)

_BASE = f"https://www.{_HOST}"

# Video detail pages look like /vid-6183942-blonde-girlfriend-drops-to-her-knees-and-sucks-his-small-dick/
_VIDEO_HREF_RE = re.compile(r"^/vid-(\d+)-[^/]+/?$", flags=re.IGNORECASE)
_DURATION_TEXT_RE = re.compile(r"\b(?:\d{1,2}:)?\d{1,2}:\d{2}\b")
_RATING_RE = re.compile(r"\b(\d{1,3})\s*%")
_NUMBER_RE = re.compile(r"\d[\d\s,.]*")

# Inline `var vid_array = "id;ext;pref;ROT13-URL;host|..."` block used for lazy thumbs.
_VID_ARRAY_RE = re.compile(r"var\s+vid_array\s*=\s*\"([^\"]*)\"")


def can_handle(host: str) -> bool:
    h = (host or "").lower().split(":")[0]
    if h.startswith("www."):
        h = h[4:]
    return h == _HOST or h.endswith("." + _HOST)


def get_categories() -> list[dict]:
    try:
        current_dir = os.path.dirname(os.path.abspath(__file__))
        json_path = os.path.join(current_dir, "categories.json")
        with open(json_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []


async def fetch_page(url: str, referer: str = _BASE + "/") -> str:
    headers = {
        "User-Agent": _USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": referer,
    }
    import httpx

    async with httpx.AsyncClient(
        timeout=httpx.Timeout(25.0),
        follow_redirects=True,
        headers=headers,
    ) as client:
        res = await client.get(url)
        res.raise_for_status()
        return res.text


# The canonical host is www.pussyspace.com; rewrite bare/other hosts so every
# request (including video/1-10min/ or /letter/<x>/ pages) goes to the live HTML.
def _canonical_url(url: str) -> str:
    try:
        p = urlparse(url if "//" in url else "https://" + url)
    except Exception:
        return url
    host = p.netloc.lower().split(":")[0]
    if host and (host != _HOST or not p.netloc.lower().startswith("www.")):
        return urlunparse(("https", f"www.{_HOST}", p.path or "/", "", p.query, ""))
    return url


def _first_non_empty(*values: Optional[str]) -> Optional[str]:
    for v in values:
        if v is not None and str(v).strip():
            return str(v).strip()
    return None


def _clean_title(title: str | None) -> Optional[str]:
    if not title:
        return None
    t = str(title).strip()
    # Meta titles look like: "Title HD (6 min) Fetish Video | PussySpace"
    t = re.sub(r"\s*\|\s*PussySpace.*$", "", t, flags=re.IGNORECASE)
    t = re.sub(r"\s*\(\d+\s*min\)\s*$", "", t, flags=re.IGNORECASE)
    t = re.sub(r"\s+HD$", "", t, flags=re.IGNORECASE)
    return t or None


def _duration_to_mmss(seconds: int | None) -> Optional[str]:
    if not seconds or seconds <= 0:
        return None
    h, rem = divmod(int(seconds), 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def _extract_duration(text: str | None) -> Optional[str]:
    if not text:
        return None
    m = _DURATION_TEXT_RE.search(text)
    return m.group(0) if m else None


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


def _parse_vid_array(html: str) -> dict[str, str]:
    """
    Parse the inline `vid_array` obfuscated thumb map.

    Format: "{id};{ext};{prefix};{ROT13 url}[;{host}]|..."
    Example: "6183945;.jpg;xv;uggcf://guhzo-pqa77.kivqrbf-pqa.pbz/.../xv_15_g.wct;xv7.pussyspace.com"
             (ROT13 -> "https://thumb-cdn77.xvideos-cdn.com/.../xv_15_t.jpg")
    Returns {video_id: https_thumb_url}.
    """
    thumbs: dict[str, str] = {}
    matches = _VID_ARRAY_RE.findall(html or "")
    if not matches:
        return thumbs
    for blob in matches:
        for entry in blob.split("|"):
            parts = entry.split(";")
            if len(parts) < 4:
                continue
            vid, _, _, enc_url = parts[0], parts[1], parts[2], parts[3]
            vid = vid.strip()
            enc_url = enc_url.strip()
            if not vid or not enc_url or not enc_url.lower().startswith("uggcf"):
                continue
            try:
                decoded = codecs.decode(enc_url, "rot13")
            except Exception:
                continue
            if decoded.startswith("http"):
                thumbs[vid] = decoded
    return thumbs


def _is_hd_video(soup: BeautifulSoup) -> bool:
    hd_span = soup.select_one("h1 .text_hd_type")
    if not hd_span:
        return False
    return hd_span.get_text(" ", strip=True).upper() == "HD"


def _extract_categories_from_url(video_url: str) -> list[str]:
    """Derive category paths from a listing base_url (not used on detail pages)."""
    return []


def _extract_tags(soup: BeautifulSoup) -> list[str]:
    tags: list[str] = []
    # Card img alt looks like: "[Scarlit Scandal,Chris Scandal],babe"
    for img in soup.select("div.video_div img[alt], span.video_img img[alt]"):
        alt = (img.get("alt") or "").strip()
        if not alt:
            continue
        for chunk in alt.split(","):
            c = chunk.strip().strip("[]")
            if c:
                tags.append(c)
    return list(dict.fromkeys(tags))


def _is_probable_ad_iframe(src: str) -> bool:
    s = (src or "").lower()
    markers = (
        "doubleclick",
        "googlesyndication",
        "adservice",
        "exoclick",
        "exosrv",
        "tsyndicate",
        "propellerads",
        "realsrv",
        "juicyads",
        "ero-advertising",
        "hilltopads",
        "clickadu",
        "popads",
        "tscprts.com",
        "rollxxxone",
        "banner.go",
        "/ads/",
        "idzone",
        "ad_tags",
    )
    return any(marker in s for marker in markers)


def _canon_video_url(href: str) -> Optional[str]:
    href = (href or "").strip()
    if not href:
        return None
    if href.startswith("//"):
        href = f"https:{href}"
    elif href.startswith("/"):
        href = _BASE + href
    if not href.startswith("http"):
        return None
    p = urlparse(href)
    host = p.netloc.lower().split(":")[0]
    if host != f"www.{_HOST}" and host != _HOST:
        return None
    if not _VIDEO_HREF_RE.match(p.path or ""):
        return None
    return urlunparse(("https", f"www.{_HOST}", p.path.rstrip("/") + "/", "", "", ""))


# ---------------------------------------------------------------------------
# scrape(): detail page
# ---------------------------------------------------------------------------

async def parse_video_page(html: str, url: str) -> dict[str, Any]:
    soup = BeautifulSoup(html, "lxml")

    title = _clean_title(
        _first_non_empty(
            _meta(soup, prop="og:title"),
            soup.title.get_text(strip=True) if soup.title else None,
            soup.h1.get_text(" ", strip=True) if soup.h1 else None,
        )
    ) or "Unknown Video"

    description = _first_non_empty(
        _meta(soup, prop="og:description"),
        _meta(soup, name="description"),
    )

    thumbnail = _first_non_empty(
        _meta(soup, prop="og:image"),
        _meta(soup, name="twitter:image"),
        soup.find("link", rel="image_src") and soup.find("link", rel="image_src").get("href"),
    )

    duration = _duration_to_mmss(
        int(_meta(soup, prop="video:duration") or 0) or None
    )
    if duration is None:
        duration = _extract_duration(
            soup.h1.get_text(" ", strip=True) if soup.h1 else None
        )

    # Rating as a pseudo-views field: PussySpace cards expose a like % badge.
    rating_el = soup.select_one("#ratingInBar, #ratingBarGood .numInBar, .numInBar")
    rating = None
    if rating_el:
        m = _RATING_RE.search(rating_el.get_text(" ", strip=True))
        if m:
            rating = m.group(1) + "%"

    views = _first_non_empty(
        (soup.select_one("#ratingTotal").get_text(" ", strip=True)
         if soup.select_one("#ratingTotal") else None),
    )

    uploader_name = None

    tags: list[str] = []
    kw = _meta(soup, name="keywords")
    if kw:
        tags.extend(t.strip() for t in kw.split(",") if t.strip())
    tags.extend(_extract_tags(soup))
    tags = list(dict.fromkeys(t for t in tags if t))

    return {
        "url": url,
        "title": title,
        "description": description,
        "thumbnail_url": thumbnail,
        "duration": duration,
        "views": views,
        "uploader_name": uploader_name,
        "category": None,
        "tags": tags,
        "upload_date": None,
        "related_videos": [],
        "preview_url": None,
        "hd": _is_hd_video(soup),
        "video": await _extract_streams(soup, html, url),
    }


async def _extract_streams(soup: BeautifulSoup, html: str, video_url: str) -> dict[str, Any]:
    streams: list[dict[str, str]] = []
    seen: set[str] = set()

    direct_file: Optional[str] = None
    m = re.search(
        r"https?://[^\s\"'<>]+?\.mp4(?:[?#][^\s\"'<>]*)?",
        html or "",
        flags=re.IGNORECASE,
    )
    if m:
        candidate = m.group(0)
        if not any(mark in candidate.lower() for mark in ("google", "ads", "doubleclick")):
            direct_file = candidate.replace("\\/", "/")

    if direct_file and direct_file not in seen:
        seen.add(direct_file)
        streams.append({"url": direct_file, "quality": "source", "format": "mp4"})

    for iframe in soup.select("iframe[src]") or []:
        src = (iframe.get("src") or "").strip()
        if not src:
            continue
        if src.startswith("//"):
            src = f"https:{src}"
        if not src.startswith("http"):
            continue
        host_l = urlparse(src).netloc.lower()
        if "pussyspace.com" not in host_l and _is_probable_ad_iframe(src):
            continue
        if src in seen:
            continue
        seen.add(src)
        streams.append({"url": src, "quality": "embed", "format": "embed"})

    # Direct link block as a last playable source.
    dl_link = soup.select_one("a.get_button a[href], a.btn[href*='/dl/']") if soup else None
    # PussySpace marks the direct file with og:video:url == page URL + "#file.mp4".
    og_video = _meta(soup, prop="og:video:url") if soup else None
    if (not direct_file) and og_video and "#file" in og_video:
        streams.append(
            {"url": video_url.rstrip("/") + "/#file.mp4", "quality": "page", "format": "page"}
        )
        if dl_link:
            _ = dl_link  # no-op keeps lints quiet; /dl/{id}/ needs an extra signed request
    del dl_link

    preferred = None
    for s in streams:
        if s.get("format") == "mp4":
            preferred = s
            break
    default_url = preferred or next(iter(streams), None)

    return {
        "streams": streams,
        "hls": None,
        "default": default_url,
        "has_video": bool(streams),
    }


async def scrape(url: str) -> dict[str, Any]:
    page_url = _canonical_url(url)
    html = await fetch_page(page_url, referer=_BASE + "/")
    return await parse_video_page(html, page_url)


# ---------------------------------------------------------------------------
# list_videos(): card grids (home, /page={n}, categories, search)
# ---------------------------------------------------------------------------

def _build_list_page_url(base_url: str, page: int) -> str:
    raw = (base_url or "").strip()
    if not raw.startswith("http"):
        raw = "https://" + raw.lstrip("/")
    raw = _canonical_url(raw)

    p = urlparse(raw)
    path = p.path or "/"
    query = p.query

    # Preserve query-based search pages (?q=term) and only page with &page=
    if page > 1:
        if query:
            params = [kv for kv in query.split("&") if kv]
            prefix = "page" if not any(kv.startswith("page=") for kv in params) else ""
            return urlunparse(
                ("https", f"www.{_HOST}", path, "", query + ("&" if not prefix else "") + f"page={page}", "")
            ) if prefix else urlunparse(
                ("https", f"www.{_HOST}", path, "", query + f"&page={page}", "")
            )
        # Most list sections use /page=2; category archives also accept /page=2
        path = path.rstrip("/")
        if path and _PAGED_PATH_SUFFIX_RE.search(path):
            path = _PAGED_PATH_SUFFIX_RE.sub("", path)
        return urlunparse(("https", f"www.{_HOST}", (path or "") + f"/page={page}", "", "", ""))

    return urlunparse(("https", f"www.{_HOST}", path, "", query, ""))


_PAGED_PATH_SUFFIX_RE = re.compile(r"/page=\d+/?$")


def _best_image_url(img: Any, thumbs_map: dict[str, str]) -> Optional[str]:
    if img is None:
        return None
    for key in ("data-src", "data-original", "src"):
        v = img.get(key)
        if not v:
            continue
        url = str(v).strip()
        if not url or url.startswith("data:"):
            continue
        return urljoin(_BASE + "/", url)
    # Lazy placeholder: fall back to the vid_array ROT13 map via img id.
    img_id = (img.get("id") or "").strip()
    if img_id and img_id in thumbs_map:
        return thumbs_map[img_id]
    return None


async def list_videos(base_url: str, page: int = 1, limit: int = 100) -> list[dict[str, Any]]:
    page_url = _build_list_page_url(base_url or _BASE + "/", page)
    try:
        html = await fetch_page(page_url, referer=_BASE + "/")
    except Exception:
        return []

    soup = BeautifulSoup(html, "lxml")
    thumbs_map = _parse_vid_array(html)

    items: list[dict[str, Any]] = []
    seen: set[str] = set()

    for div in soup.select("div.video_div"):
        if len(items) >= limit:
            break
        a = div.select_one("a.video_img[href]")
        if a is None:
            continue
        href = _canon_video_url(a.get("href") or "")
        if not href or href in seen:
            continue

        title_el = div.select_one(".ts3 a[title], .ts3 a")
        title = _clean_title(
            _first_non_empty(
                title_el.get("title") if title_el else None,
                title_el.get_text(" ", strip=True) if title_el else None,
                a.get("title"),
            )
        ) or "Unknown Video"

        thumb = _best_image_url(a.find("img"), thumbs_map)
        if not thumb:
            continue

        duration = _extract_duration(
            div.select_one("span.video_duration").get_text(" ", strip=True)
            if div.select_one("span.video_duration")
            else None
        )

        rating = None
        rating_el = div.select_one(".rating_count")
        if rating_el:
            m = _RATING_RE.search(rating_el.get_text(" ", strip=True))
            if m:
                rating = m.group(1) + "%"

        seen.add(href)
        items.append(
            {
                "url": href,
                "title": title,
                "thumbnail_url": thumb,
                "duration": duration,
                "views": rating,
                "uploader_name": None,
            }
        )

    return items[:limit]


def search_url(query: str) -> str:
    return f"{_BASE}/search?q={quote(query)}"
