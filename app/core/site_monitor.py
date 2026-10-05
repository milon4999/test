"""
Website monitoring: periodically probe each Explore source's base URL to detect
dead, moved, or blocked sites, and record per-source health in a shared JSON file
so the /explore/config endpoint can swap in a maintenance favicon.

Only stdlib imports at module top level: `app.api.endpoints.explore` is imported
lazily inside run_all_checks() to avoid a circular import (explore.py imports this
module at the top).
"""

from __future__ import annotations

import asyncio
import json
import os
import ssl
import tempfile
from datetime import datetime, timezone
from typing import Any, Optional

import aiohttp

from app.config.settings import settings

MAINTENANCE_FAVICON = (
    "https://media.tenor.com/K5wSW-CGK9wAAAAj/maintenance-under-maintenance.gif"
)

_BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)


def _state_path() -> str:
    return settings.SITE_MONITOR_STATE_FILE


def _ensure_state_file_dir() -> None:
    d = os.path.dirname(_state_path())
    if d:
        os.makedirs(d, exist_ok=True)


def load_state() -> dict[str, dict[str, Any]]:
    try:
        with open(_state_path(), "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            return data
    except Exception:
        pass
    return {}


def save_state(state: dict[str, dict[str, Any]]) -> None:
    _ensure_state_file_dir()
    path = _state_path()
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), prefix=".site_monitor_", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except Exception:
            pass
        raise


async def probe_source(
    session: aiohttp.ClientSession,
    base_url: str,
    *,
    timeout: int,
) -> tuple[bool, Optional[int], str]:
    """Probe a base URL. Returns (ok, final_status, reason)."""
    headers = {"User-Agent": _BROWSER_UA, "Accept": "text/html,*/*;q=0.8"}
    timeout_ctx = aiohttp.ClientTimeout(total=timeout, connect=timeout, sock_read=timeout)

    attempts = [
        {"ssl": None},  # normal verification first
        {"ssl": _no_verify_ssl_context()},  # fallback for expired-cert hosts
    ]
    last_error = "unknown error"
    for attempt in attempts:
        try:
            async with session.get(
                base_url,
                headers=headers,
                timeout=timeout_ctx,
                allow_redirects=True,
                ssl=attempt["ssl"],
            ) as resp:
                status = resp.status
                ok = 200 <= status < 400
                reason = "" if ok else f"HTTP {status}"
                return ok, status, reason
        except (aiohttp.ClientSSLError, aiohttp.ServerDisconnectedError, aiohttp.ClientConnectionError,
                asyncio.TimeoutError, aiohttp.ClientError) as e:
            last_error = type(e).__name__ + ": " + str(e)
            if isinstance(e, aiohttp.ClientSSLError):
                # Retry once without verification for expired certs
                continue
            break
    return False, None, last_error


def _no_verify_ssl_context() -> ssl.SSLContext:
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


def update_state(
    state: dict[str, dict[str, Any]],
    source_id: str,
    *,
    ok: bool,
    status: Optional[int],
    reason: str,
) -> None:
    strikes = max(1, int(settings.SITE_MONITOR_STRIKES))
    entry = state.get(source_id) or {
        "fail_count": 0,
        "healthy": True,
        "last_status": None,
        "last_checked": None,
        "reason": "",
    }
    entry["last_status"] = status
    entry["last_checked"] = datetime.now(timezone.utc).isoformat()
    entry["reason"] = reason or entry.get("reason", "")

    if ok:
        entry["fail_count"] = 0
        entry["healthy"] = True
        entry["reason"] = ""
    else:
        entry["fail_count"] = int(entry.get("fail_count") or 0) + 1
        if entry["fail_count"] >= strikes:
            entry["healthy"] = False

    state[source_id] = entry


async def run_all_checks() -> None:
    from app.api.endpoints.explore import EXPLORE_SOURCES

    state = load_state()
    timeout = int(settings.SITE_MONITOR_TIMEOUT)
    concurrency = max(1, int(settings.SITE_MONITOR_CONCURRENCY))
    semaphore = asyncio.Semaphore(concurrency)

    connector = aiohttp.TCPConnector(limit=concurrency, limit_per_host=2, force_close=False)
    async with aiohttp.ClientSession(connector=connector) as session:

        async def _check(source) -> None:
            async with semaphore:
                ok, status, reason = await probe_source(
                    session, source.baseUrl, timeout=timeout
                )
            update_state(state, source.sourceId, ok=ok, status=status, reason=reason)

        sources = [s for s in EXPLORE_SOURCES if not s.disable]
        await asyncio.gather(*(_check(s) for s in sources))

    save_state(state)


def get_unhealthy_source_ids() -> set[str]:
    state = load_state()
    return {
        sid for sid, entry in state.items()
        if not entry.get("healthy", True)
    }
