"""Persistent browser bridge for X/LinkedIn — no API, no relogin.

Uses playwright.async_api.launch_persistent_context(user_data_dir=...)
so Cookies + localStorage survive gateway restarts. First login is headed
visible window; subsequent polls are headless.

If playwright not installed, gracefully degrades to httpx fallback
(no fake synthesized posts).
"""
from __future__ import annotations

import asyncio
import contextlib
import hashlib
import sqlite3
import time
from pathlib import Path
from typing import List


PLATFORM_URLS = {
    "x": "https://x.com/search?q={q}&src=typed_query",
    "linkedin": "https://www.linkedin.com/search/results/content/?keywords={q}&origin=GLOBAL_SEARCH_HEADER",
}

LOGGED_IN_SELECTORS = {
    "x": 'a[href="/home"], a[href="/compose/post"]',
    "linkedin": "img.global-nav__me-photo, img.global-nav__me-photo--icon",
}

try:
    from playwright.async_api import async_playwright  # type: ignore

    HAS_PLAYWRIGHT = True
except Exception:  # pragma: no cover
    HAS_PLAYWRIGHT = False


def _hash(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()[:12]


async def _get_context(browser_data: Path, headless: bool = True):
    assert HAS_PLAYWRIGHT, "playwright not installed"
    browser_data.mkdir(parents=True, exist_ok=True)
    pw = await async_playwright().start()
    ctx = await pw.launch_persistent_context(
        user_data_dir=str(browser_data),
        headless=headless,
        viewport={"width": 1280, "height": 800},
        args=["--disable-blink-features=AutomationControlled", "--no-sandbox"],
        user_agent=(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 Chrome/122.0.0.0 Safari/537.36"
        ),
    )
    return pw, ctx


async def is_logged_in(platform: str, browser_data: Path) -> bool:
    if not HAS_PLAYWRIGHT:
        return False
    sel = LOGGED_IN_SELECTORS.get(platform, "")
    pw, ctx = await _get_context(browser_data, headless=True)
    try:
        page = ctx.pages[0] if ctx.pages else await ctx.new_page()
        await page.goto(
            PLATFORM_URLS[platform].format(q="test"),
            wait_until="domcontentloaded",
            timeout=20000,
        )
        await page.wait_for_timeout(4000)
        if "login" in page.url or "authwall" in page.url:
            return False
        el = await page.query_selector(sel) if sel else None
        return el is not None
    except Exception:
        return False
    finally:
        with contextlib.suppress(Exception):
            await ctx.close()
            await pw.stop()


async def open_login_window(platform: str, browser_data: Path):
    """Open headed window for one-time login — returns immediately, window lives 5 min in background."""
    pw, ctx = await _get_context(browser_data, headless=False)
    page = ctx.pages[0] if ctx.pages else await ctx.new_page()
    await page.goto(
        PLATFORM_URLS[platform].format(q=""),
        wait_until="domcontentloaded",
    )
    # keep window alive in background — don't block POST /auth/login

    async def _auto_close():
        await asyncio.sleep(300)
        with contextlib.suppress(Exception):
            await ctx.close()
            await pw.stop()

    asyncio.create_task(_auto_close())
    # return immediately so REST call doesn't timeout and trigger fallback


async def open_url_headed(url: str, browser_data: Path) -> dict:
    """Open any URL in the headed persistent window (preview-rail helper)."""
    assert HAS_PLAYWRIGHT, "playwright not installed"
    if not url.startswith(("http://", "https://")):
        raise ValueError("url must start with http(s)://")
    pw, ctx = await _get_context(browser_data, headless=False)
    page = ctx.pages[0] if ctx.pages else await ctx.new_page()
    await page.goto(url, wait_until="domcontentloaded", timeout=30000)

    async def _auto_close():
        await asyncio.sleep(300)
        with contextlib.suppress(Exception):
            await ctx.close()
            await pw.stop()

    asyncio.create_task(_auto_close())
    return {"opened": True, "url": url}


async def search_platform(
    platform: str, query: str, limit: int, browser_data: Path, db_path: Path
) -> List[dict]:
    """Scroll search results, extract posts, cache to SQLite, return plain-text."""
    if not HAS_PLAYWRIGHT:
        return await _fallback_search(platform, query, limit, db_path)

    pw, ctx = await _get_context(browser_data, headless=True)
    posts: List[dict] = []
    try:
        page = ctx.pages[0] if ctx.pages else await ctx.new_page()
        url = PLATFORM_URLS[platform].format(q=query.replace(" ", "%20"))
        await page.goto(url, wait_until="domcontentloaded", timeout=30000)
        await page.wait_for_timeout(5000)
        for _ in range(4):
            await page.mouse.wheel(0, 2000)
            await page.wait_for_timeout(2500)
            texts = await page.eval_on_selector_all(
                "article", "els => els.slice(0,20).map(e => e.innerText.slice(0,600))"
            )
            if texts and len(texts) >= limit:
                break
        raw = await page.evaluate(
            """() => {
            const els = Array.from(document.querySelectorAll('article'));
            return els.slice(0,24).map(a => {
                const txt = a.innerText || '';
                const link = a.querySelector('a[href*="/status/"], a[href*="/posts/"], a[href*="/feed/update"]');
                return {text: txt.slice(0,800), href: link ? link.href : location.href};
            });
        }"""
        )
        for r in raw[:limit]:
            txt = (r.get("text") or "").strip()
            href = r.get("href") or f"https://{platform}.com/search?q={query}"
            if not txt or len(txt) < 20:
                continue
            pid = _hash(href + txt[:80])
            posts.append(
                {
                    "id": pid,
                    "platform": platform,
                    "author": txt.split("\n")[0][:40],
                    "text": txt,
                    "url": href,
                    "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                }
            )
        # per-request connection, WAL safe
        with contextlib.closing(sqlite3.connect(str(db_path), timeout=10)) as conn:
            conn.execute(
                """CREATE TABLE IF NOT EXISTS posts(
                id TEXT PRIMARY KEY, platform TEXT, author TEXT, text TEXT,
                url TEXT, ts TEXT, hash TEXT, read INT DEFAULT 0)"""
            )
            for p in posts:
                try:
                    conn.execute(
                        "INSERT OR IGNORE INTO posts"
                        "(id,platform,author,text,url,ts,hash) VALUES(?,?,?,?,?,?,?)",
                        (
                            p["id"],
                            p["platform"],
                            p["author"],
                            p["text"],
                            p["url"],
                            p["ts"],
                            _hash(p["url"] + p["text"]),
                        ),
                    )
                except Exception:
                    pass
            conn.commit()
        return posts
    except Exception as e:
        print(f"[browser_bridge] search {platform} failed: {e}")
        return await _fallback_search(platform, query, limit, db_path)
    finally:
        with contextlib.suppress(Exception):
            await ctx.close()
            await pw.stop()


async def read_url(url: str, browser_data: Path) -> dict:
    if not HAS_PLAYWRIGHT:
        import httpx

        async with httpx.AsyncClient(timeout=15, follow_redirects=True) as c:
            r = await c.get(url)
            r.raise_for_status()
            return {"url": url, "text": r.text[:8000], "via": "http"}
    pw, ctx = await _get_context(browser_data, headless=True)
    try:
        page = ctx.pages[0] if ctx.pages else await ctx.new_page()
        await page.goto(url, wait_until="domcontentloaded", timeout=20000)
        await page.wait_for_timeout(4000)
        text = await page.evaluate("() => document.body.innerText.slice(0,8000)")
        comments = await page.eval_on_selector_all(
            "article", "els => els.slice(1,6).map(e=>e.innerText.slice(0,300))"
        )
        return {"url": url, "text": text, "comments": comments or [], "via": "browser"}
    finally:
        with contextlib.suppress(Exception):
            await ctx.close()
            await pw.stop()


async def post_to_x(text: str, browser_data: Path) -> dict:
    """Post text on X via the persistent browser session.

    Returns success ONLY when the composer confirms (sent toast or detail
    page). Never synthesizes a success — failures carry the reason.
    """
    assert HAS_PLAYWRIGHT, "playwright not installed"
    text = (text or "").strip()
    if not text:
        raise ValueError("text required")
    if len(text) > 2800:
        raise ValueError("text exceeds 2800 chars")
    pw, ctx = await _get_context(browser_data, headless=True)
    try:
        page = ctx.pages[0] if ctx.pages else await ctx.new_page()
        await page.goto(
            "https://x.com/compose/post",
            wait_until="domcontentloaded",
            timeout=30000,
        )
        await page.wait_for_timeout(5000)
        if "login" in page.url:
            return {"success": False, "error": "not logged in — POST /auth/login?platform=x first"}
        box = None
        for sel in (
            'div[role="textbox"][aria-label*="Post"]',
            'div[aria-label="Post text"]',
            'div[role="textbox"]',
        ):
            try:
                box = await page.query_selector(sel)
            except Exception:
                box = None
            if box:
                break
        if not box:
            return {"success": False, "error": "composer not found — X layout changed?"}
        await box.click()
        await page.wait_for_timeout(800)
        await page.keyboard.type(text[:2800])
        await page.wait_for_timeout(1200)
        post_btn = None
        for sel in (
            'button[data-testid="tweetButton"]',
            'div[data-testid="tweetButton"]',
            'button:has-text("Post")',
        ):
            try:
                post_btn = await page.query_selector(sel)
            except Exception:
                post_btn = None
            if post_btn:
                break
        if post_btn:
            with contextlib.suppress(Exception):
                await post_btn.click()
        else:
            await page.keyboard.press("Control+Enter")
        await page.wait_for_timeout(6000)
        body = await page.evaluate("() => document.body.innerText.slice(0,2000)")
        lowered = body.lower()
        if any(
            s in lowered
            for s in ("your post was sent", "posted", "view post", "your post is live")
        ):
            return {"success": True, "platform": "x", "via": "browser"}
        if "/status/" in page.url:
            return {"success": True, "platform": "x", "url": page.url, "via": "browser"}
        return {
            "success": False,
            "error": "no send confirmation observed — check drafts or retry",
            "page_url": page.url,
        }
    finally:
        with contextlib.suppress(Exception):
            await ctx.close()
            await pw.stop()


async def scrape_linkedin_posts(query: str, limit: int, browser_data: Path) -> List[dict]:
    """Scrape LinkedIn content search (needs the one-time login). No fake rows."""
    assert HAS_PLAYWRIGHT, "playwright not installed"
    from urllib.parse import quote

    pw, ctx = await _get_context(browser_data, headless=True)
    try:
        page = ctx.pages[0] if ctx.pages else await ctx.new_page()
        await page.goto(
            f"https://www.linkedin.com/search/results/content/?keywords={quote(query)}",
            wait_until="domcontentloaded",
            timeout=30000,
        )
        await page.wait_for_timeout(5000)
        if "login" in page.url or "authwall" in page.url:
            return [
                {
                    "id": _hash(f"linkedin:{query}:auth"),
                    "platform": "linkedin",
                    "author": "system",
                    "text": "[auth] LinkedIn needs one-time login — POST /auth/login?platform=linkedin",
                    "url": "https://www.linkedin.com/login",
                    "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                }
            ]
        raw = await page.evaluate(
            """() => Array.from(document.querySelectorAll(
            'div.feed-shared-update-v2, div.search-result__wrapper')).slice(0,24).map(el => {
                const txt = el.innerText || '';
                const link = el.querySelector('a[href*="/posts/"], a[href*="/feed/update"]');
                return {text: txt.slice(0,800), href: link ? link.href : location.href};
            })"""
        )
        out: List[dict] = []
        for r in raw[:limit]:
            txt = (r.get("text") or "").strip()
            if len(txt) < 20:
                continue
            href = r.get("href") or "https://www.linkedin.com"
            out.append(
                {
                    "id": _hash(href + txt[:80]),
                    "platform": "linkedin",
                    "author": txt.split("\n")[0][:40],
                    "text": txt,
                    "url": href,
                    "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                }
            )
        return out
    finally:
        with contextlib.suppress(Exception):
            await ctx.close()
            await pw.stop()


async def scrape_maps_businesses(query: str, city: str, browser_data: Path) -> List[dict]:
    """Scrape Google Maps place names for a query (+optional city). No fake rows."""
    assert HAS_PLAYWRIGHT, "playwright not installed"
    from urllib.parse import quote

    q = f"{query} {city}".strip()
    pw, ctx = await _get_context(browser_data, headless=True)
    try:
        page = ctx.pages[0] if ctx.pages else await ctx.new_page()
        await page.goto(
            f"https://www.google.com/maps/search/{quote(q)}",
            wait_until="domcontentloaded",
            timeout=30000,
        )
        await page.wait_for_timeout(6000)
        raw = await page.evaluate(
            """() => Array.from(document.querySelectorAll(
            'div[role="article"]')).slice(0,24).map(el => {
                const name = el.getAttribute('aria-label') || '';
                const link = el.querySelector('a[href]');
                return {name, href: link ? link.href : location.href,
                        text: (el.innerText || '').slice(0,400)};
            })"""
        )
        out: List[dict] = []
        for r in raw[:20]:
            name = (r.get("name") or "").strip()
            if not name:
                continue
            out.append(
                {
                    "id": _hash((r.get("href") or "") + name),
                    "platform": "maps",
                    "author": name,
                    "text": f"{name} — {(r.get('text') or '')[:300]}",
                    "url": r.get("href") or "https://www.google.com/maps",
                    "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                }
            )
        return out
    finally:
        with contextlib.suppress(Exception):
            await ctx.close()
            await pw.stop()


async def _fallback_search(platform: str, query: str, limit: int, db_path: Path) -> List[dict]:
    """When playwright absent — return empty and let caller cache API path."""
    del limit, db_path
    # Intentionally no fake synthesized posts; caller will show "install playwright" hint
    ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    # Return a single hint post so UI is not empty, clearly marked
    return [
        {
            "id": _hash(f"{platform}:{query}:hint:{ts}"),
            "platform": platform,
            "author": "system",
            "text": (
                f"[hybrid] playwright not installed on this host — "
                f"search for '{query}' on {platform} needs one-time install: "
                f"pip install playwright && playwright install chromium, "
                f"then POST /auth/login?platform={platform} for no-relogin scroll. "
                f"Public API fallback for {platform} is via /search with instagram/youtube tokens."
            ),
            "url": f"https://{platform}.com/search?q={query}",
            "ts": ts,
        }
    ]
