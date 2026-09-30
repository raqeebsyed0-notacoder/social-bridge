# Social Bridge v1.0.1

Hybrid social research — browser for X/LinkedIn (no API key), API for IG/FB/YT/TikTok.
Permanent desktop plugin like Newswire: one login survives reboots.

## Install (laptop)

1. Delete any old copy first (desktop app folder AND any previous source folder).
2. Unzip this package, e.g. to `Documents/social-bridge`.
3. Hermes Desktop → Plugins → Install from path → pick the unzipped folder.
4. Open `/social` → Settings → Login X / Login LinkedIn in the preview rail.

## Layout

- `plugin.yaml` — manifest (5 tools, 1 hook, valid JSON-schema config)
- `__init__.py` — agent half (social_search/read/refresh/post/scrape)
- `dashboard/plugin_api.py` — backend (14 routes: auth, preview, search, posts,
  read, post, scrape, refresh-all, watchlist, settings)
- `dashboard/browser_bridge.py` — Playwright persistent-context bridge
- `dashboard/api_clients.py` — YouTube/Instagram token APIs
- `dashboard/scrapers.py` — LinkedIn/expatriates.com/Maps market scraping
- `dashboard/db_schema.py` — SQLite bootstrap (additive only)
- `desktop/plugin.js` — the /social UI (Post, Latest, Watchlist, Settings)

Rebuilt 2026-09-30 (v1.0.1): backend rewritten to the verified UI contract.
No synthesized data anywhere — failures return honest errors.
