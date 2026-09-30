"""Social-Bridge — agent tool half (classic plugin) + dashboard half.

Exposes social_search / social_read / social_refresh as LLM-callable tools.
They delegate to the same browser_bridge / api_clients used by dashboard/plugin_api.py.
Unified package: this file = agent half, dashboard/plugin_api.py = backend, desktop/plugin.js = UI.
"""
import asyncio
import concurrent.futures
import json
from pathlib import Path


DATA_DIR = Path(__file__).parent / "dashboard"
BROWSER_DATA = DATA_DIR / "browser_data"
DB_PATH = DATA_DIR / "data.db"

import sys as _sys
_SIBLING_DIR = DATA_DIR
if str(_SIBLING_DIR) not in _sys.path:
    _sys.path.insert(0, str(_SIBLING_DIR))


def _load_sibling(stem: str):
    """Import a sibling module from this plugin's ``dashboard/`` dir, by its own name.

    ``dashboard/`` is NOT unique — euphoria-paperclip, hermes-newswire, memory-wiki,
    nous-prices and social-bridge each ship a top-level ``dashboard/`` package (some
    without ``__init__.py``, so they resolve as namespace packages). Whichever plugin's
    dir is ahead on ``sys.path`` wins the bare name ``dashboard``, so the old
    ``from dashboard.browser_bridge import ...`` could bind to the WRONG package and die
    with ``No module named 'dashboard.browser_bridge'`` at tool-call time. Import the
    siblings by their own unique module names with ``dashboard/`` on ``sys.path`` — the
    same pattern ``dashboard/plugin_api.py`` uses (and which shares one module instance
    with the dashboard backend) — and evict a foreign cache entry if one claimed the name.
    """
    cached = _sys.modules.get(stem)
    if cached is not None:
        cached_file = str(getattr(cached, "__file__", "") or "")
        if not cached_file.startswith(str(_SIBLING_DIR)):
            del _sys.modules[stem]  # another plugin's same-named module — not ours
    __import__(stem)
    return _sys.modules[stem]


bootstrap_db = _load_sibling("db_schema").bootstrap


def _run(coro):
    """Loop-aware runner: works inside or outside a running event loop."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    # running loop exists (Hermes gateway) -> run in a fresh thread
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        fut = pool.submit(asyncio.run, coro)
        return fut.result(timeout=90)


def register(ctx):
    # --- tool: social_search ---
    search_schema = {
        "name": "social_search",
        "description": (
            "Search social platforms (x, linkedin via browser — no API needed; "
            "instagram/youtube/facebook/tiktok via API if token set). "
            "Returns cached + live posts. Hybrid: local browser for X/LinkedIn, "
            "Euphoria API for rest."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "platform": {
                    "type": "string",
                    "enum": ["x", "linkedin", "instagram", "youtube", "facebook", "tiktok"],
                    "description": "Platform to search",
                },
                "query": {
                    "type": "string",
                    "description": "Search query / hashtag / keyword",
                },
                "limit": {
                    "type": "integer",
                    "description": "Max posts (default 12)",
                    "default": 12,
                },
            },
            "required": ["platform", "query"],
        },
    }

    def handle_search(params, **kw):
        del kw
        platform = str(params.get("platform", "x")).lower()
        query = str(params.get("query", ""))
        limit = int(params.get("limit", 12))
        if not query or len(query) > 200:
            return json.dumps({"success": False, "error": "query required (1-200 chars)"})
        limit = max(1, min(limit, 50))
        try:
            if platform in ("x", "linkedin"):
                search_platform = _load_sibling("browser_bridge").search_platform

                posts = _run(search_platform(platform, query, limit, BROWSER_DATA, DB_PATH))
            else:
                search_api = _load_sibling("api_clients").search_api
                import sqlite3

                def db_factory():
                    c = sqlite3.connect(str(DB_PATH))
                    c.row_factory = sqlite3.Row
                    return c

                posts = _run(search_api(platform, query, limit, db_factory))
            return json.dumps(
                {
                    "success": True,
                    "platform": platform,
                    "query": query,
                    "count": len(posts),
                    "posts": posts,
                }
            )
        except Exception as e:
            return json.dumps({"success": False, "error": str(e)[:500]})

    ctx.register_tool(
        name="social_search",
        toolset="social_bridge",
        schema=search_schema,
        handler=handle_search,
    )

    # --- tool: social_read ---
    read_schema = {
        "name": "social_read",
        "description": (
            "Read a social post URL (X/LinkedIn via persistent browser, others via HTTP). "
            "Extracts text + comments."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "Post URL to read"},
            },
            "required": ["url"],
        },
    }

    def handle_read(params, **kw):
        del kw
        url = str(params.get("url", ""))
        if not url.startswith(("http://", "https://")):
            return json.dumps({"success": False, "error": "url must start with http(s)://"})
        try:
            if "x.com" in url or "linkedin.com" in url:
                read_url = _load_sibling("browser_bridge").read_url

                data = _run(read_url(url, BROWSER_DATA))
            else:
                import httpx
                from bs4 import BeautifulSoup

                async def _fetch():
                    async with httpx.AsyncClient(timeout=15) as c:
                        r = await c.get(url, follow_redirects=True)
                        r.raise_for_status()
                        soup = BeautifulSoup(r.text, "html.parser")
                        return {"url": url, "text": soup.get_text()[:8000], "via": "http"}

                data = _run(_fetch())
            return json.dumps({"success": True, **data})
        except Exception as e:
            return json.dumps({"success": False, "error": str(e)[:500]})

    ctx.register_tool(
        name="social_read",
        toolset="social_bridge",
        schema=read_schema,
        handler=handle_read,
    )

    # --- tool: social_post ---
    post_schema = {
        "name": "social_post",
        "description": "Post on X via browser (no API key needed). Text only, no images.",
        "parameters": {
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "Post text (max 2800 chars)"},
            },
            "required": ["text"],
        },
    }

    def handle_post(params, **kw):
        del kw
        text = str(params.get("text", ""))
        if not text or len(text) > 2800:
            return json.dumps({"success": False, "error": "text required (1-2800 chars)"})
        try:
            post_to_x = _load_sibling("browser_bridge").post_to_x
            result = _run(post_to_x(text, BROWSER_DATA))
            return json.dumps(result)
        except Exception as e:
            return json.dumps({"success": False, "error": str(e)[:500]})

    ctx.register_tool(
        name="social_post",
        toolset="social_bridge",
        schema=post_schema,
        handler=handle_post,
    )

    # --- tool: social_scrape ---
    scrape_schema = {
        "name": "social_scrape",
        "description": "Scrape LinkedIn, expatriates.com, Google Maps for market intelligence. NOT for lead generation — that is LeadJin's job.",
        "parameters": {
            "type": "object",
            "properties": {
                "site": {"type": "string", "enum": ["linkedin", "expatriates", "maps"]},
                "query": {"type": "string", "description": "Search query"},
                "country": {"type": "string", "description": "For expatriates: saudi-arabia, qatar, uae"},
                "category": {"type": "string", "description": "For expatriates: jobs, plots, housing, job-seeker"},
                "city": {"type": "string", "description": "For maps: Dammam, Riyadh, etc."},
                "limit": {"type": "integer", "description": "Max results (default 20)"},
            },
            "required": ["site", "query"],
        },
    }

    def handle_scrape(params, **kw):
        del kw
        site = str(params.get("site", "linkedin")).lower()
        query = str(params.get("query", ""))
        limit = int(params.get("limit", 20))
        country = str(params.get("country", "saudi-arabia"))
        category = str(params.get("category", "jobs"))
        city = str(params.get("city", ""))
        if not query:
            return json.dumps({"success": False, "error": "query required"})
        limit = max(1, min(limit, 50))
        try:
            if site == "linkedin":
                scrape_linkedin_posts = _load_sibling("scrapers").scrape_linkedin_posts
                results = _run(scrape_linkedin_posts(query, limit, BROWSER_DATA))
            elif site == "expatriates":
                scrape_expatriates = _load_sibling("scrapers").scrape_expatriates
                results = _run(scrape_expatriates(category, country, limit))
            elif site == "maps":
                scrape_maps_businesses = _load_sibling("scrapers").scrape_maps_businesses
                results = _run(scrape_maps_businesses(query, city, BROWSER_DATA))
            else:
                return json.dumps({"success": False, "error": f"unsupported site: {site}"})
            return json.dumps({"success": True, "site": site, "count": len(results), "results": results})
        except Exception as e:
            return json.dumps({"success": False, "error": str(e)[:500]})

    ctx.register_tool(
        name="social_scrape",
        toolset="social_bridge",
        schema=scrape_schema,
        handler=handle_scrape,
    )

    # --- tool: social_refresh ---
    refresh_schema = {
        "name": "social_refresh",
        "description": "Refresh all watchlist searches (poll) — like Newswire refresh-all.",
        "parameters": {"type": "object", "properties": {}, "required": []},
    }

    def handle_refresh(params, **kw):
        del params, kw
        import sqlite3

        BROWSER_DATA.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(DB_PATH))
        conn.row_factory = sqlite3.Row
        try:
            # Same schema + default-watchlist bootstrap the dashboard uses, so this
            # tool works even when no dashboard request has opened the DB yet.
            bootstrap_db(conn)
            wl = conn.execute("SELECT * FROM watchlist").fetchall()
        finally:
            conn.close()
        if not wl:
            return json.dumps(
                {
                    "success": True,
                    "refreshed": 0,
                    "hint": "add watchlist via dashboard POST /watchlist",
                }
            )

        async def _refresh():
            total = 0
            for w in wl:
                try:
                    if w["platform"] in ("x", "linkedin"):
                        search_platform = _load_sibling("browser_bridge").search_platform

                        posts = await search_platform(
                            w["platform"], w["query"], 8, BROWSER_DATA, DB_PATH
                        )
                    else:
                        search_api = _load_sibling("api_clients").search_api

                        def dbf():
                            c = sqlite3.connect(str(DB_PATH))
                            c.row_factory = sqlite3.Row
                            return c

                        posts = await search_api(w["platform"], w["query"], 8, dbf)
                    total += len(posts)
                except Exception:
                    pass
            return total

        try:
            n = _run(_refresh())
            return json.dumps({"success": True, "refreshed": len(wl), "new_count": n})
        except Exception as e:
            return json.dumps({"success": False, "error": str(e)[:500]})

    ctx.register_tool(
        name="social_refresh",
        toolset="social_bridge",
        schema=refresh_schema,
        handler=handle_refresh,
    )

    def on_post_tool(tool_name: str = "", status: str = "", **kwargs):
        """Observe social_* tool calls.

        Hermes fires ``post_tool_call`` with KEYWORD arguments only, and the
        payload provides: api_request_id, args, duration_ms, error_message,
        error_type, middleware_trace, result, session_id, status, task_id,
        telemetry_schema_version, tool_call_id, tool_name, turn_id.

        Any *required* parameter whose name is not in that set (the old
        ``params``) raises ``TypeError: missing 1 required positional argument``
        on EVERY tool call, which silently disables this hook. Keep every
        parameter optional or named after a payload field.
        """
        del kwargs
        if tool_name.startswith("social_"):
            print(f"[social-bridge] {tool_name} status={status or 'ok'}")

    try:
        ctx.register_hook("post_tool_call", on_post_tool)
    except Exception:
        pass
