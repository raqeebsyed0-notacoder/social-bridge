"""API clients for platforms where tokens CAN be provided (IG/FB/YT/TikTok)."""

from __future__ import annotations

import contextlib
import hashlib
import sqlite3
import time
from typing import Callable, List


def _hash(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()[:12]


async def search_api(
    platform: str, query: str, limit: int, db_factory: Callable[[], sqlite3.Connection]
) -> List[dict]:
    """Public/search fallback — upgrades to real API when token exists in settings."""
    token = None
    conn = None
    try:
        # db_factory is a callable returning a fresh connection (per-request, WAL safe)
        conn = db_factory() if callable(db_factory) else db_factory  # type: ignore
        row = conn.execute(
            "SELECT value FROM settings WHERE key=?", (f"token_{platform}",)
        ).fetchone()
        if row is not None:
            # sqlite3.Row supports mapping and index access
            try:
                token = row["value"]
            except Exception:
                token = row[0]
        if platform == "youtube" and token:
            # close before await to avoid holding sqlite lock across network
            if conn is not None:
                with contextlib.suppress(Exception):
                    conn.close()
                conn = None
            return await _youtube_search(query, limit, str(token))
        if platform == "instagram" and token:
            if conn is not None:
                with contextlib.suppress(Exception):
                    conn.close()
                conn = None
            return await _instagram_search(query, limit, str(token))
    except Exception:
        pass
    finally:
        if conn is not None:
            with contextlib.suppress(Exception):
                conn.close()

    # Fallback public hint posts (clearly marked, not fake data)
    ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    posts: List[dict] = []
    for i in range(min(limit, 3)):
        pid = _hash(f"{platform}:{query}:{i}:{ts}")
        posts.append(
            {
                "id": pid,
                "platform": platform,
                "author": f"{platform} public",
                "text": (
                    f"[public] '{query}' on {platform} — add token in /social Settings "
                    f"for full API access. Hint post {i + 1} (enable token_youtube etc. in PATCH /settings)."
                ),
                "url": f"https://{platform}.com/search?q={query}",
                "ts": ts,
            }
        )
    return posts


async def _youtube_search(query: str, limit: int, api_key: str) -> List[dict]:
    import httpx

    try:
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.get(
                "https://www.googleapis.com/youtube/v3/search",
                params={
                    "part": "snippet",
                    "q": query,
                    "maxResults": limit,
                    "key": api_key,
                    "type": "video",
                },
            )
            r.raise_for_status()
            j = r.json()
            out: List[dict] = []
            for it in j.get("items", [])[:limit]:
                sn = it.get("snippet", {})
                vid = it.get("id", {}).get("videoId", "") if isinstance(it.get("id"), dict) else ""
                out.append(
                    {
                        "id": _hash(vid or sn.get("title", "")),
                        "platform": "youtube",
                        "author": sn.get("channelTitle", ""),
                        "text": f"{sn.get('title', '')} — {sn.get('description', '')[:300]}",
                        "url": f"https://youtube.com/watch?v={vid}" if vid else "https://youtube.com",
                        "ts": sn.get("publishedAt") or time.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    }
                )
            if out:
                return out
    except Exception as e:
        print(f"[api_clients] youtube failed: {e}")

    # Fallback to hint posts via recursive call with no token (use memory DB to avoid recursion loop)
    ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    return [
        {
            "id": _hash(f"youtube:{query}:fallback:{ts}"),
            "platform": "youtube",
            "author": "youtube public",
            "text": f"[public] youtube search '{query}' failed or no token — check token_youtube in settings.",
            "url": "https://youtube.com",
            "ts": ts,
        }
    ]


async def _instagram_search(query: str, limit: int, token: str) -> List[dict]:
    del token
    # Placeholder — Meta Graph hashtag search requires business account + token
    # Return hint; real implementation would call graph.facebook.com
    ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    return [
        {
            "id": _hash(f"instagram:{query}:{ts}"),
            "platform": "instagram",
            "author": "instagram",
            "text": f"[stub] instagram search for '{query}' — wire Meta Graph hashtag API when token set.",
            "url": f"https://instagram.com/explore/tags/{query}/",
            "ts": ts,
        }
    ]
