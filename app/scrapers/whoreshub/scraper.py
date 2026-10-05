from __future__ import annotations

import json
import os
import re
from typing import Any, Optional
from urllib.parse import urljoin, urlparse, urlunparse

from bs4 import BeautifulSoup
import httpx

from app.core.pool import fetch_html as pool_fetch_html


_HOST = "whoreshub.com"

_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)

_VIDEO_HREF_RE = re.compile(r"^/videos/\d+/[^/]+/?$", flags=re.IGNORECASE)
_PAGE_SEGMENT_RE = re.compile(r"/(\d+)/?$")
_DURATION_TEXT_RE = re.compile(r"\b(?:\d{1,2}:)?\d{1,2}:\d{2}\b")
_ISO_DURATION_RE = re.compile(
    r"^P(?:(\d+)D)?T?(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?$", flags=re.IGNORECASE
)
_NUMBER_TEXT_RE = re.compile(r"[\d][\d\s,\.]*")
_FLASHVARS_RE = re.compile(r"var\s+flashvars\s*=\s*\{([\s\S]*?)\};")
_JS_PAIR_RE = re.compile(
    r"((?:video_alt_url\d*|video_url\d*)(?:_text)?)\s*:\s*('(?:[^'\\]|\\.)*'|\"(?:[^\"\\]|\\.)*\")"
)
_PROTO_REL_RE = re.compile(r"^//")


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


async def fetch_page(url: str, referer: str = f"https://www.{_HOST}/") -> str:
    headers = {
        "User-Agent": _USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": referer,
    }
    try:
        return await pool_fetch_html(url, headers=headers)
    except Exception:
        # The site's edge occasionally times out or serves a stale/expired
        # certificate; retry directly, dropping verification only as a last
        # resort for TLS errors on this host.
        import logging

        import httpx

        logger = logging.getLogger(__name__)
        last_error: Exception | None = None
        for verify in (True, False):
            try:
                async with httpx.AsyncClient(
                    timeout=httpx.Timeout(25.0),
                    follow_redirects=True,
                    verify=verify,
                    headers=headers,
                ) as client:
                    res = await client.get(url)
                    res.raise_for_status()
                    return res.text
            except Exception as e:
                last_error = e
                if not verify:
                    logger.warning(f"whoreshub: fallback fetch with verify=False used for {url}: {e}")
        raise last_error or Exception(f"Failed to fetch {url}")


def _first_non_empty(*values: Optional[str]) -> Optional[str]:
    for v in values:
        if v is not None and str(v).strip():
            return str(v).strip()
    return None


def _absolute_url(url: str | None, base_url: str) -> Optional[str]:
    if not url:
        return None
    u = str(url).strip()
    if not u or u.startswith("data:"):
        return None
    if u.startswith("//"):
        u = f"https:{u}"
    elif u.startswith("/"):
        u = urljoin(f"https://www.{_HOST}/", u)
    if not u.startswith("http"):
        return None
    return u


def _clean_title(title: str | None) -> Optional[str]:
    if not title:
        return None
    t = str(title).strip()
    for suffix in (" - WhoresHub", " | WhoresHub"):
        if t.endswith(suffix):
            t = t[: -len(suffix)].strip()
    return t or None


def _extract_duration(text: str | None) -> Optional[str]:
    if not text:
        return None
    m = _DURATION_TEXT_RE.search(text)
    return m.group(0) if m else None


def _duration_from_iso(value: str | None) -> Optional[str]:
    if not value:
        return None
    m = _ISO_DURATION_RE.match(value.strip())
    if not m:
        return _extract_duration(value)
    days, hours, minutes, seconds = (int(g) if g else 0 for g in m.groups())
    total = days * 86400 + hours * 3600 + minutes * 60 + seconds
    if total <= 0:
        return None
    h, rem = divmod(total, 3600)
    mi, s = divmod(rem, 60)
    return f"{h}:{mi:02d}:{s:02d}" if h else f"{mi}:{s:02d}"


def _extract_views(text: str | None) -> Optional[str]:
    if not text:
        return None
    m = _NUMBER_TEXT_RE.search(text)
    if not m:
        return None
    return m.group(0).strip().replace(" ", "").replace(",", "") or None


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


def _parse_flashvars(html: str) -> dict[str, str]:
    m = _FLASHVARS_RE.search(html)
    if not m:
        return {}
    body = m.group(1)
    pairs: dict[str, str] = {}
    for km, vm in _JS_PAIR_RE.findall(body):
        key, raw = km, vm
        val = raw[1:-1]
        val = val.replace("\\/", "/").replace("\\u0026", "&").replace("\\'", "'").replace('\\"', '"')
        pairs[key] = val.strip()
    return pairs


def _stream_quality_from_url(url: str) -> str:
    low = (url or "").lower()
    m = re.search(r"([1-9]\d{2,3})p", low)
    if m:
        return f"{m.group(1)}p"
    if low.endswith(".m3u8") or ".m3u8?" in low:
        return "adaptive"
    return "source"


def _normalize_quality_label(label: str | None, url: str) -> str:
    if label:
        m = re.search(r"(\d{3,4})p", label, flags=re.IGNORECASE)
        if m:
            return f"{m.group(1)}p"
    return _stream_quality_from_url(url)


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
        "gsrv.dev",
        "banner.go",
        "spaceid=",
        "/promo.php",
        "dynamic_banner",
    )
    return any(marker in s for marker in markers)


def _extract_video_id(html: str, video_url: str) -> Optional[str]:
    flashvars = _parse_flashvars(html)
    vid = flashvars.get("video_id")
    if vid and vid.isdigit():
        return vid
    m = re.search(r"/videos/(\d+)/", video_url)
    if m:
        return m.group(1)
    return None


async def _resolve_get_file(get_file_url: str, *, referer: str) -> Optional[str]:
    """
    WhoresHub get_file URLs 302-redirect to a signed CDN
    `origin*-direct.cdntrex.com/remote_control.php?...` link. Resolve and
    return the final playable URL.
    """
    ref = referer if referer.strip().startswith("http") else f"https://www.{_HOST}/"
    headers = {
        "User-Agent": _USER_AGENT,
        "Referer": ref,
        "Accept": "*/*",
        "Accept-Language": "en-US,en;q=0.9",
    }
    try:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(20.0),
            follow_redirects=False,
            verify=False,
            headers=headers,
        ) as client:
            resp = await client.get(get_file_url)
    except Exception:
        return None
    if resp.status_code in (301, 302, 303, 307, 308):
        loc = resp.headers.get("Location")
        if loc and "remote_control.php" in loc:
            return loc
    return None


async def _extract_streams(soup: BeautifulSoup, html: str, video_url: str) -> dict[str, Any]:
    streams: list[dict[str, str]] = []
    seen: set[str] = set()

    flashvars = _parse_flashvars(html)
    # KVS exposes qualities as video_url (+video_url_text) and numbered
    # video_alt_url{,2,3,...} (+ *_text). Only plain http(s) URLs are usable;
    # license-protected `function/...` values are skipped.
    url_keys = sorted(
        [k for k in flashvars if re.fullmatch(r"video_alt_url\d*|video_url\d*", k)],
        key=lambda k: (len(k), k),
    )
    for k in url_keys:
        url = _absolute_url(flashvars.get(k), video_url)
        if not url:
            continue
        text_key = f"{k}_text"
        quality = _normalize_quality_label(flashvars.get(text_key), url)
        fmt = "hls" if url.lower().split("?")[0].endswith(".m3u8") else "mp4"
        if url in seen:
            continue
        seen.add(url)
        streams.append({"url": url, "quality": quality, "format": fmt})

    for source in soup.select("video source[src]"):
        src = _absolute_url(source.get("src"), video_url)
        if not src or src in seen:
            continue
        seen.add(src)
        fmt = "hls" if src.lower().split("?")[0].endswith(".m3u8") else "mp4"
        streams.append({"url": src, "quality": _stream_quality_from_url(src), "format": fmt})

    for iframe in soup.select("iframe[src]"):
        src = _absolute_url(iframe.get("src"), video_url)
        if not src or src in seen or _is_probable_ad_iframe(src):
            continue
        seen.add(src)
        streams.append({"url": src, "quality": "embed", "format": "embed"})

    # get_file URLs must be resolved to their signed CDN remote_control.php
    # target, otherwise they 302 in the client and do not play.
    get_file_mp4 = [s for s in streams if s.get("format") == "mp4" and "get_file" in (s.get("url") or "")]
    if get_file_mp4:
        import asyncio

        async def _resolve_one(stream: dict[str, str]) -> tuple[dict[str, str], Optional[str]]:
            resolved = await _resolve_get_file(stream["url"], referer=video_url)
            return stream, resolved

        resolved_pairs = await asyncio.gather(*[_resolve_one(s) for s in get_file_mp4])
        for stream, resolved in resolved_pairs:
            if resolved:
                stream["url"] = resolved
            else:
                streams.remove(stream)

    # Native embed page as a fallback stream (site's own KVS player).
    video_id = _extract_video_id(html, video_url)
    native_embed = f"https://www.{_HOST}/embed/{video_id}/" if video_id else None
    if native_embed and native_embed not in seen:
        seen.add(native_embed)
        streams.append({"url": native_embed, "quality": "whoreshub", "format": "embed"})

    def _score(item: dict[str, str]) -> tuple[int, int]:
        fmt = (item.get("format") or "").lower()
        q = re.search(r"(\d{3,4})", item.get("quality") or "")
        qnum = int(q.group(1)) if q else 0
        if fmt == "mp4":
            return (3, qnum)
        if fmt == "hls":
            return (2, qnum)
        if fmt == "embed" and native_embed and (item.get("url") or "") == native_embed:
            return (1, 1)
        return (1, 0)

    uniq = list(dict.fromkeys((json.dumps(s, sort_keys=True) for s in streams)))
    materialized = [json.loads(s) for s in uniq]
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


async def parse_video_page(html: str, url: str) -> dict[str, Any]:
    soup = BeautifulSoup(html, "lxml")
    json_ld = _parse_json_ld(soup)
    flashvars = _parse_flashvars(html)

    video_obj = next(
        (
            obj
            for obj in json_ld
            if str(obj.get("@type", "")).lower() == "videoobject"
        ),
        {},
    )

    title = _clean_title(
        _first_non_empty(
            _meta(soup, prop="og:title"),
            video_obj.get("name"),
            soup.select_one(".video-info h1").get_text(" ", strip=True)
            if soup.select_one(".video-info h1")
            else None,
            soup.h1.get_text(" ", strip=True) if soup.h1 else None,
            flashvars.get("video_title"),
            soup.title.get_text(strip=True) if soup.title else None,
        )
    ) or "Unknown Video"

    description = _first_non_empty(
        _meta(soup, prop="og:description"),
        video_obj.get("description"),
        soup.select_one(".info-wrap .text-description").get_text(" ", strip=True)
        if soup.select_one(".info-wrap .text-description")
        else None,
        _meta(soup, name="description"),
    )

    thumbnail = _absolute_url(
        _first_non_empty(_meta(soup, prop="og:image"), video_obj.get("thumbnailUrl")),
        url,
    )

    duration = _first_non_empty(
        _duration_from_iso(video_obj.get("duration")),
        _extract_duration(
            soup.select_one("#tab1 .list-info li.wrap .value").get_text(" ", strip=True)
            if soup.select_one("#tab1 .list-info li.wrap .value")
            else None
        ),
    )
    duration = _first_non_empty(
        _duration_from_iso(video_obj.get("duration")),
        _extract_duration(
            soup.select_one("#tab1 .list-info li.wrap .value").get_text(" ", strip=True)
            if soup.select_one("#tab1 .list-info li.wrap .value")
            else None
        ),
    )
    views = _first_non_empty(
        _extract_views(str(video_obj.get("interactionCount")) if video_obj.get("interactionCount") else None),
    )
    if views is None:
        # KVS nests the count under interactionStatistic[].userInteractionCount.
        stats = video_obj.get("interactionStatistic")
        stat_list = stats if isinstance(stats, list) else [stats] if stats else []
        for stat in stat_list:
            if isinstance(stat, dict) and str(stat.get("interactionType", "")).endswith("WatchAction"):
                views = _extract_views(str(stat.get("userInteractionCount")) if stat.get("userInteractionCount") else None)
                if views:
                    break
    if views is None:
        li_items = soup.select("#tab1 .list-info li.wrap")
        for li in li_items:
            use_el = li.select_one("svg use")
            if use_el and "#icon-view" in (use_el.get("xlink:href") or use_el.get("href") or ""):
                views = _extract_views(li.select_one(".value").get_text(" ", strip=True) if li.select_one(".value") else None)
                break
    upload_date = _first_non_empty(video_obj.get("uploadDate"))

    uploader_name = None
    username_el = soup.select_one(".info-top a.username")
    if username_el:
        uploader_name = username_el.get_text(" ", strip=True) or None

    tags: list[str] = []
    fv_tags = flashvars.get("video_tags")
    if fv_tags:
        tags.extend(t.strip() for t in fv_tags.split(",") if t.strip())
    fv_cats = flashvars.get("video_categories")
    if fv_cats:
        tags.extend(t.strip() for t in fv_cats.split(",") if t.strip())
    if not tags:
        for a in soup.select(".info-wrap .tags-list a.btn[href]"):
            label = a.get_text(" ", strip=True)
            if label:
                tags.append(label)
    tags = list(dict.fromkeys(tags))

    video = await _extract_streams(soup, html, url)

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
        "upload_date": upload_date,
        "video": video,
        "related_videos": [],
        "preview_url": None,
    }


async def scrape(url: str) -> dict[str, Any]:
    html = await fetch_page(url, referer=url)
    return await parse_video_page(html, url)


def _normalize_video_href(href: str) -> Optional[str]:
    href = (href or "").strip()
    if not href:
        return None
    if href.startswith("//"):
        href = f"https:{href}"
    elif href.startswith("/"):
        href = f"https://www.{_HOST}{href}"
    if not href.startswith("http"):
        return None

    parsed = urlparse(href)
    host = parsed.netloc.lower().split(":")[0]
    if host != f"www.{_HOST}" and host != _HOST:
        return None
    if not _VIDEO_HREF_RE.match(parsed.path or ""):
        return None

    return urlunparse(("https", f"www.{_HOST}", parsed.path.rstrip("/") + "/", "", "", ""))


def _best_image_url(img: Any) -> Optional[str]:
    if img is None:
        return None
    for key in ("data-src", "data-original", "data-lazy-src", "srcset", "src"):
        v = img.get(key)
        if not v:
            continue
        url = str(v).strip()
        if not url or url.startswith("data:"):
            continue
        if key == "srcset" and " " in url:
            url = url.split(" ", 1)[0].strip()
        url = _absolute_url(url, f"https://www.{_HOST}/")
        if url:
            return url
    return None


def _build_list_page_url(base_url: str, page: int) -> str:
    raw = (base_url or "").strip()
    if not raw.startswith("http"):
        raw = "https://" + raw.lstrip("/")
    p = urlparse(raw)
    scheme = p.scheme or "https"
    netloc = p.netloc or f"www.{_HOST}"
    path = p.path or "/"
    query = p.query

    if page <= 1:
        return urlunparse((scheme, netloc, path, "", query, ""))

    host = netloc.lower().split(":")[0].removeprefix("www.")
    # The bare root's pager points at /latest-updates/{n}/ (KVS convention).
    if host == _HOST and path in ("", "/") and not query:
        path = "/latest-updates/"

    clean_path = _PAGE_SEGMENT_RE.sub("/", path)
    clean_path = clean_path if clean_path.endswith("/") else clean_path + "/"
    paged_path = clean_path.rstrip("/") + f"/{page}/"
    return urlunparse((scheme, netloc, paged_path, "", query, ""))


async def list_videos(base_url: str, page: int = 1, limit: int = 100) -> list[dict[str, Any]]:
    page_url = _build_list_page_url(base_url, page)
    try:
        html = await fetch_page(page_url, referer=base_url or f"https://www.{_HOST}/")
    except Exception:
        return []

    soup = BeautifulSoup(html, "lxml")
    items: list[dict[str, Any]] = []
    seen: set[str] = set()

    for a in soup.select("a.item[href]"):
        if len(items) >= limit:
            break
        href = _normalize_video_href(a.get("href") or "")
        if not href or href in seen:
            continue

        img = a.find("img")
        thumb = _best_image_url(img)
        if not thumb:
            continue

        title = _clean_title(
            _first_non_empty(
                a.get("title"),
                a.select_one(".description").get_text(" ", strip=True)
                if a.select_one(".description")
                else None,
                img.get("alt") if img else None,
                a.get_text(" ", strip=True),
            )
        ) or "Unknown Video"

        duration = _extract_duration(
            a.select_one("span.duration").get_text(" ", strip=True)
            if a.select_one("span.duration")
            else None
        )

        views = None
        box = a.find_parent("div", class_="box")
        info_ul = box.find("ul", class_="info") if box else None
        if info_ul:
            first_li = info_ul.find("li", class_="item")
            if first_li:
                # Views live in the li's direct span.text; the rating % is
                # nested inside span.rating and must not be picked up.
                direct_texts = [
                    s.get_text(" ", strip=True)
                    for s in first_li.find_all("span", class_="text", recursive=False)
                ]
                if direct_texts:
                    views = _extract_views(direct_texts[0])

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
