"""Market-intelligence scrapers (NOT lead generation — that is LeadJin's job).

All functions return real extracted rows or raise with a clear reason.
Nothing is ever synthesized: empty results come back as an empty list.
"""
from __future__ import annotations

import hashlib
import time
from pathlib import Path
from typing import List


def _hash(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()[:12]


def _ts() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


async def scrape_linkedin_posts(query: str, limit: int, browser_data: Path) -> List[dict]:
    """LinkedIn content search via the persistent browser session."""
    from browser_bridge import scrape_linkedin_posts as _scrape

    posts = await _scrape(query, limit, browser_data)
    return posts


async def scrape_expatriates(category: str, country: str, limit: int) -> List[dict]:
    """Scrape expatriates.com classifieds (plain HTTP — no login needed)."""
    import httpx
    from bs4 import BeautifulSoup

    category = (category or "jobs").strip().lower()
    country = (country or "saudi-arabia").strip().lower()
    limit = max(1, min(limit, 50))
    base = f"https://www.expatriates.com/classifieds/{country}/{category}/"
    out: List[dict] = []
    async with httpx.AsyncClient(
        timeout=20,
        follow_redirects=True,
        headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"},
    ) as c:
        r = await c.get(base)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")
        for a in soup.select("a[href*='/classifieds/']")[: limit * 2]:
            title = (a.get_text() or "").strip()
            href = a.get("href") or ""
            if len(title) < 10 or len(out) >= limit:
                continue
            if href.startswith("/"):
                href = "https://www.expatriates.com" + href
            out.append(
                {
                    "id": _hash(href + title[:60]),
                    "platform": "expatriates",
                    "author": f"{category}/{country}",
                    "text": title[:500],
                    "url": href,
                    "ts": _ts(),
                }
            )
    return out


async def scrape_maps_businesses(query: str, city: str, browser_data: Path) -> List[dict]:
    """Google Maps business names via the persistent browser session."""
    from browser_bridge import scrape_maps_businesses as _scrape

    return await _scrape(query, city or "", browser_data)
