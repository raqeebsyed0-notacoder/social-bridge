"""Hybrid Social-Bridge — FastAPI router mounted at /api/plugins/social-bridge/.

Permanent plugin like hermes-newswire: SQLite cache + persistent browser_data +
ctx.storage prefs mirrored. Hybrid = same code runs on Local (browser for X/LinkedIn)
and Euphoria (API for IG/FB/YT + 24/7 cron). Install on BOTH hosts, enable in
plugins.enabled, UI merges.

Run inside Hermes gateway process — may import hermes_agent internals if needed.
"""
from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import sqlite3
import time
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, Body, HTTPException
from pydantic import BaseModel, Field

# --- Sibling imports, working under BOTH load modes -------------------------
# The dashboard mounts this file as a *top-level* module via
# ``importlib.util.spec_from_file_location`` (no package parent), so plain
# relative imports (``from .browser_bridge import ...``) raise ImportError at
# call time and every browser endpoint breaks. Import the siblings by absolute
# module name instead, putting this directory on sys.path first.
_DASHBOARD_DIR = Path(__file__).parent
if str(_DASHBOARD_DIR) not in __import__("sys").path:
    __import__("sys").path.insert(0, str(_DASHBOARD_DIR))

import api_clients  # noqa: E402
import browser_bridge  # noqa: E402
import db_schema  # noqa: E402
import scrapers  # noqa: E402

router = APIRouter()

# --- Paths: DATA lives beside this file (dashboard/) ---
DATA_DIR = Path(__file__).parent
BROWSER_DATA = DATA_DIR / "browser_data"
DB_PATH = DATA_DIR / "data.db"

# Background poll task (created on first request, survives gateway lifetime)
_poll_task: Optional[asyncio.Task] = None


def _get_db() -> sqlite3.Connection:
    """Per-request connection — no shared state, WAL safe."""
    conn = sqlite3.connect(str(DB_PATH), check_same_thread=False, timeout=10)
    conn.row_factory = sqlite3.Row
    db_schema.bootstrap(conn)
    return conn


def _hash(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()[:12]


async def _ensure_poll_loop():
    """Start background poll loop once per gateway lifetime (Newswire parity)."""

    global _poll_task
    if _poll_task and not _poll_task.done():
        return

    # Lazy import avoids gateway startup cost when plugin disabled
    async def _loop():
        while True:
            try:
                # read interval from DB (allows PATCH /settings to change it live)
                with contextlib.closing(_get_db()) as conn:
                    row = conn.execute(
                        "SELECT value FROM settings WHERE key='poll_interval'"
                    ).fetchone()
                    interval = int(row["value"]) if row else 900
                    interval = max(300, min(interval, 3600))
                    wl = conn.execute("SELECT * FROM watchlist").fetchall()
                if not wl:
                    await asyncio.sleep(interval)
                    continue
                # best-effort poll via same logic as /refresh-all
                for w in wl:
                    try:
                        if w["platform"] in ("x", "linkedin"):
                            posts = await browser_bridge.search_platform(
                                w["platform"], w["query"], 8, BROWSER_DATA, DB_PATH
                            )
                        else:
                            def _dbf():
                                c = _get_db()
                                return c

                            posts = await api_clients.search_api(
                                w["platform"], w["query"], 8, _dbf
                            )
                            # close factory-conn inside loop if api_clients didn't
                            if posts:
                                with contextlib.closing(_get_db()) as c2:
                                    for p in posts:
                                        try:
                                            c2.execute(
                                                "INSERT OR IGNORE INTO posts"
                                                "(id,platform,author,text,url,ts,hash) "
                                                "VALUES(?,?,?,?,?,?,?)",
                                                (
                                                    p["id"],
                                                    p["platform"],
                                                    p.get("author", ""),
                                                    p.get("text", ""),
                                                    p["url"],
                                                    p.get("ts", ""),
                                                    _hash(p["url"] + p.get("text", "")),
                                                ),
                                            )
                                        except Exception:
                                            pass
                                    c2.commit()
                    except Exception as e:
                        print(f"[social-bridge poll] {w['platform']}:{w['query']} {e}")
                        await asyncio.sleep(2)
                await asyncio.sleep(interval)
            except asyncio.CancelledError:
                break
            except Exception as e:
                print(f"[social-bridge poll loop] {e}")
                await asyncio.sleep(60)

    _poll_task = asyncio.create_task(_loop())


# --- Models ---

class SearchBody(BaseModel):
    platform: str = Field(description="x | linkedin | instagram | youtube | facebook | tiktok")
    query: str = Field(min_length=1, max_length=200)
    limit: int = Field(default=12, ge=1, le=50)


class ReadBody(BaseModel):
    url: str = Field(min_length=8, max_length=2048)


class PostBody(BaseModel):
    platform: str = Field(default="x", description="x only today")
    text: str = Field(min_length=1, max_length=2800)


class ScrapeBody(BaseModel):
    site: str = Field(description="linkedin | expatriates | maps")
    query: str = Field(min_length=1, max_length=200)
    country: str = Field(default="saudi-arabia")
    category: str = Field(default="jobs")
    city: str = Field(default="")
    limit: int = Field(default=20, ge=1, le=50)


# --- Auth status (browser platforms) ---

@router.get("/auth/status")
async def auth_status(platform: str):
    """Check if persistent session is logged in (probes avatar selector)."""
    await _ensure_poll_loop()
    plat = platform.lower()
    if plat in ("x", "linkedin"):
        try:
            ok = await browser_bridge.is_logged_in(plat, BROWSER_DATA)
            return {
                "platform": plat,
                "logged_in": ok,
                "browser_data": str(BROWSER_DATA),
            }
        except Exception as e:
            return {
                "platform": plat,
                "logged_in": False,
                "error": str(e)[:300],
                "browser_data": str(BROWSER_DATA),
            }
    # API platforms: check token presence (context manager)
    with contextlib.closing(_get_db()) as conn:
        row = conn.execute(
            "SELECT value FROM settings WHERE key=?", (f"token_{plat}",)
        ).fetchone()
        has_token = bool(row and row["value"])
    return {"platform": plat, "logged_in": has_token, "via": "api_token"}


@router.post("/auth/login")
async def auth_login(platform: str):
    """Open headed persistent window for one-time login (local only). Returns opened flag."""
    plat = platform.lower()
    if plat not in ("x", "linkedin"):
        raise HTTPException(status_code=400, detail="login only for browser platforms")
    try:
        await browser_bridge.open_login_window(plat, BROWSER_DATA)
        return {
            "opened": True,
            "browser_data": str(BROWSER_DATA),
            "hint": "Log in in the opened window, then close it. Cookies persist.",
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"login window failed: {e}")


@router.post("/preview")
async def open_in_preview(payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    """Open a URL in the desktop's in-app preview rail.

    Same mechanism as newswire: emit the ``preview.open`` gateway event the
    app's own open_preview tool uses (tools/desktop_ui.py emitter), broadcast
    to live transports so it works from a REST context with no turn-bound
    session. NOT server-side playwright — the server is headless.
    """
    from urllib.parse import urlsplit

    url = str((payload or {}).get("url") or "").strip()
    label = str((payload or {}).get("label") or "").strip()
    if not url.startswith(("http://", "https://")):
        raise HTTPException(status_code=400, detail="body.url must start with http(s)://")
    host = (urlsplit(url).hostname or "").lower()
    if host in ("localhost", "127.0.0.1", "::1") or host.startswith("169.254."):
        raise HTTPException(status_code=400, detail="unsafe_url: loopback/link-local blocked")
    try:
        from tui_gateway.server import _broadcast_global_event
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"preview bus unavailable: {e}")
    _broadcast_global_event(
        "preview.open", {"url": url, "label": label or url}
    )
    return {"opened": url}


# --- Search (live) ---

@router.post("/search")
async def search(body: SearchBody):
    await _ensure_poll_loop()
    plat = body.platform.lower()
    if plat not in ("x", "linkedin", "instagram", "youtube", "facebook", "tiktok"):
        raise HTTPException(status_code=400, detail="unsupported platform")
    if plat in ("x", "linkedin"):
        posts = await browser_bridge.search_platform(plat, body.query, body.limit, BROWSER_DATA, DB_PATH)
        return {"posts": posts, "via": "browser", "cached": False}

    def _db_factory():
        return _get_db()

    posts = await api_clients.search_api(plat, body.query, body.limit, _db_factory)
    # cache via per-request connection
    with contextlib.closing(_get_db()) as conn:
        for p in posts:
            try:
                conn.execute(
                    "INSERT OR IGNORE INTO posts"
                    "(id,platform,author,text,url,ts,hash) VALUES(?,?,?,?,?,?,?)",
                    (
                        p["id"],
                        p["platform"],
                        p.get("author", ""),
                        p.get("text", ""),
                        p["url"],
                        p.get("ts", ""),
                        _hash(p["url"] + p.get("text", "")),
                    ),
                )
            except Exception:
                pass
        conn.commit()
    return {"posts": posts, "via": "api", "cached": False}


# --- Posts (cached, offline) ---

@router.get("/posts")
async def posts(
    platform: Optional[str] = None,
    query: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
):
    await _ensure_poll_loop()
    limit = max(1, min(limit, 100))
    offset = max(0, offset)
    with contextlib.closing(_get_db()) as conn:
        sql = "SELECT * FROM posts WHERE 1=1"
        args: list[Any] = []
        if platform:
            sql += " AND platform=?"
            args.append(platform.lower())
        if query:
            sql += " AND (text LIKE ? OR author LIKE ?)"
            args.extend([f"%{query}%", f"%{query}%"])
        sql += " ORDER BY ts DESC LIMIT ? OFFSET ?"
        args.extend([limit, offset])
        rows = conn.execute(sql, args).fetchall()
        total = conn.execute("SELECT COUNT(*) AS c FROM posts").fetchone()["c"]
    return {"posts": [dict(r) for r in rows], "total": total}


@router.post("/read")
async def read_post(body: ReadBody):
    await _ensure_poll_loop()
    url = body.url
    if not url.startswith(("http://", "https://")):
        raise HTTPException(status_code=400, detail="url must start with http(s)://")
    if "x.com" in url or "twitter.com" in url or "linkedin.com" in url:
        data = await browser_bridge.read_url(url, BROWSER_DATA)
        return data

    import httpx
    from bs4 import BeautifulSoup

    async with httpx.AsyncClient(timeout=15, follow_redirects=True) as c:
        r = await c.get(url)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")
        title = soup.title.string.strip() if soup.title and soup.title.string else ""
        return {"url": url, "title": title, "text": soup.get_text()[:8000], "via": "http"}


@router.post("/post")
async def post(body: PostBody):
    """Post on X via the persistent browser session (no API key)."""
    await _ensure_poll_loop()
    if body.platform.lower() != "x":
        raise HTTPException(status_code=400, detail="only platform 'x' is supported")
    try:
        return await browser_bridge.post_to_x(body.text, BROWSER_DATA)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"post failed: {e}")


@router.post("/scrape")
async def scrape(body: ScrapeBody):
    """Market-intelligence scrape (NOT lead generation)."""
    await _ensure_poll_loop()
    site = body.site.lower()
    try:
        if site == "linkedin":
            results = await scrapers.scrape_linkedin_posts(body.query, body.limit, BROWSER_DATA)
        elif site == "expatriates":
            results = await scrapers.scrape_expatriates(body.category, body.country, body.limit)
        elif site == "maps":
            results = await scrapers.scrape_maps_businesses(body.query, body.city, BROWSER_DATA)
        else:
            raise HTTPException(status_code=400, detail=f"unsupported site: {site}")
        return {"success": True, "site": site, "count": len(results), "results": results}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"scrape failed: {e}")


@router.post("/refresh-all")
async def refresh_all(body: Optional[dict] = None):
    """Poll every watchlist entry — Newswire parity."""
    del body
    await _ensure_poll_loop()
    with contextlib.closing(_get_db()) as conn:
        wl = conn.execute("SELECT * FROM watchlist").fetchall()
    if not wl:
        return {"refreshed": 0, "hint": "Add watchlist entries via /watchlist"}
    new_total = 0
    for w in wl:
        try:
            if w["platform"] in ("x", "linkedin"):
                posts = await browser_bridge.search_platform(
                    w["platform"], w["query"], 10, BROWSER_DATA, DB_PATH
                )
            else:
                def _dbf():
                    return _get_db()

                posts = await api_clients.search_api(w["platform"], w["query"], 10, _dbf)
                # ensure cached (search_api already caches api; but poll must cache)
                if posts:
                    with contextlib.closing(_get_db()) as c2:
                        for p in posts:
                            try:
                                c2.execute(
                                    "INSERT OR IGNORE INTO posts"
                                    "(id,platform,author,text,url,ts,hash) VALUES(?,?,?,?,?,?,?)",
                                    (
                                        p["id"],
                                        p["platform"],
                                        p.get("author", ""),
                                        p.get("text", ""),
                                        p["url"],
                                        p.get("ts", ""),
                                        _hash(p["url"] + p.get("text", "")),
                                    ),
                                )
                            except Exception:
                                pass
                        c2.commit()
            new_total += len(posts)
        except Exception as e:
            print(f"[social-bridge] refresh {w['platform']}:{w['query']} failed: {e}")
    return {"refreshed": len(wl), "new_count": new_total}


# --- Watchlist ---

@router.get("/watchlist")
async def get_watchlist():
    with contextlib.closing(_get_db()) as conn:
        rows = conn.execute("SELECT * FROM watchlist ORDER BY created_at DESC").fetchall()
    return {"watchlist": [dict(r) for r in rows]}


@router.post("/watchlist")
async def add_watchlist(body: SearchBody):
    wid = _hash(body.platform + body.query + str(time.time()))
    with contextlib.closing(_get_db()) as conn:
        conn.execute(
            "INSERT INTO watchlist(id,platform,query,created_at) VALUES(?,?,?,?)",
            (wid, body.platform.lower(), body.query, int(time.time())),
        )
        conn.commit()
    return {"id": wid}


@router.delete("/watchlist/{wid}")
async def del_watchlist(wid: str):
    if not wid or len(wid) > 64:
        raise HTTPException(status_code=400, detail="invalid id")
    with contextlib.closing(_get_db()) as conn:
        conn.execute("DELETE FROM watchlist WHERE id=?", (wid,))
        conn.commit()
    return {"deleted": wid}


# --- Settings ---

@router.get("/settings")
async def get_settings():
    with contextlib.closing(_get_db()) as conn:
        rows = conn.execute("SELECT key,value FROM settings").fetchall()
    out: dict[str, Any] = {}
    for r in rows:
        v = r["value"]
        if isinstance(v, str) and v.startswith(("[", "{", '"')):
            try:
                out[r["key"]] = json.loads(v)
                continue
            except Exception:
                pass
        out[r["key"]] = v
    return {"settings": out}


@router.patch("/settings")
async def patch_settings(body: dict):
    if not isinstance(body, dict) or len(body) > 20:
        raise HTTPException(status_code=400, detail="invalid body")
    with contextlib.closing(_get_db()) as conn:
        for k, v in body.items():
            if not isinstance(k, str) or len(k) > 64:
                continue
            val = json.dumps(v) if isinstance(v, (list, dict)) else str(v)
            if len(val) > 4000:
                val = val[:4000]
            conn.execute(
                "INSERT OR REPLACE INTO settings(key,value) VALUES(?,?)", (k, val)
            )
        conn.commit()
    return await get_settings()
