# ani-cli-arabic

## Package structure
- Single package `ani_cli_arabic/`, entry point `ani_cli_arabic.app:main`
- Two UI entry paths: standard TUI (`app.py`, default) and minimal CLI (`cli.py`, `--interactive` flag)
- Two independent language tracks: Arabic (via `api.py` → `AnimeAPI`) and English (via `scrapers/`)

## English scraping pipeline
- `ENGLISH_PROVIDERS = ["miruro", "api", "mkissa", "gogoanime"]` in `scrapers/provider_manager.py`
- Every scraper MUST inherit `BaseScraper` (ABC) and implement `search(query)`, `get_episodes(anime_id)`, `get_stream_url(episode_id)`
- `ProviderManager.resolve_stream()` chains providers in order, per-step failure isolation (each method wrapped in try/except returning None)
- Provider timeout: `_PROVIDER_TIMEOUT = 30.0` seconds (accommodates Playwright page loads)
- `--provider` CLI flag. Choices in argparse: `['mkissa', 'gogoanime']` — does NOT include `api` (known issue; fast path if needed)
- Stream dict format: `{"stream_url": str|None, "headers": dict}`

## Provider details
| Provider | Source | Stream method | Status |
|----------|--------|---------------|--------|
| `miruro` | miruro.tv secure pipe via Playwright | Playwright headless → `page.evaluate(fetch)` on pipe API | **Working** — pipe returns gzip(base64(json)) with m3u8 URLs from `hls.anidb.app` via `pewe` provider |
| `api` | Consumet-protocol API | HTTP to `ANI_API_BASE_URL` | Requires self-hosted endpoint; `_discover()` auto-detects working public instances |
| `mkissa` | mkissa.to + GraphQL API | Playwright network interceptor | Search/episodes work via HTTP; stream blocked by Turnstile captcha |
| `gogoanime` | gogoanime.co.za | Playwright on vidwish.live embeds | Search/episodes work via HTTP; embed blocked by Cloudflare JS challenge |
- Miruro uses **Playwright** (not curl_cffi) to bypass Cloudflare on `miruro.tv/api/secure/pipe`. The pipe endpoint is behind Cloudflare WAF and only responds from a real browser JS context. Implementation: shared `Browser` instance (thread-safe), new `BrowserContext`+`Page` per call, `page.goto(miruro.tv)` to set CF cookies, then `page.evaluate(fetch)` to call the pipe. Search still uses AniList GraphQL via httpx (no CF, fast).
- Provider priority in `_PROVIDER_PRIORITY`: `["pewe", "kiwi", "bee", "bonk", "ally", "moo", "hop"]`. `pewe` (anidb.app CDN) is first — returns playable m3u8 URLs. `kiwi` uses `uwucdn.top` which is blocked by Cloudflare (403). The `eid` values are shared across providers (animepahe IDs), so priority ordering determines which CDN is used.
- Streams from `hls.anidb.app` are playable in mpv with `Referer: https://anidb.app/`. Verified via httpx (200, m3u8 content) and mpv (plays 1920x1080 h264 successfully).
- Playwright browser is shared at the class level (`MiruroScraper._browser`) — thread-safe. A new context+page is created per pipe call. Total time: ~5s for episodes, ~4-5s for sources (within 30s provider timeout).
- curl_cffi is no longer used by miruro (pipe rejects curl_cffi with 403 CF challenge). If curl_cffi is still in dependencies, it can be removed.
- Old scraper files `animepahe.py` and `anikoto.py` exist in repo but are dead code (not imported, removed from provider list)

## Arabic pipeline (separate, untouched by English work)
- `ARABIC_PROVIDERS = ["arabic_api_primary", "arabic_api_backup"]` — implemented in `api.py` via `AnimeAPI` class
- Uses `api.py` endpoints and `get_streaming_servers()` / `build_mediafire_url()` for quality selection
- Kept strictly separated from English code — English and Arabic provider loops must never mix or cross-fallback

## Watch Together players
- Host and guest each pick mpv or VLC at session start (`app.py:_select_watch_player`, respects `settings.player` default).
- mpv host: `MpvIpcClient` on a unique Unix socket (`_unique_socket_path`). VLC host: `VlcIpcClient` over TCP on a free loopback port (`_pick_free_port`, range 42000-43000), selected before launch.
- VLC is launched with `--extraintf=rc --rc-host=127.0.0.1:<PORT>` (host, keeps Qt GUI) or `--intf=rc` is NOT used — guests use the same `--extraintf=rc` launch plus unbind hotkeys.
- **`--rc-quiet` is NOT available on VLC 3.x** (dropped after 2.x) — do not pass it; the rc interface doesn't echo commands in VLC 3, and responses are terminated by the `> ` prompt.
- VLC rc commands used: `get_time` (integer seconds), `status` (parse `( state playing|paused|stopped )`), `seek <int>` (absolute), `pause` (toggles), `play`, `quit`. `is_playing` is unreliable for pause detection (returns 1 while paused) — use `status`.
- `set_pause()` reads current state first, then sends `pause` only if mismatched (since `pause` toggles).
- Guest VLC control lock: `--key-play= --key-jump+short= --key-jump+medium= --key-jump+long= --key-jump+extrashort= --key-next= --key-prev= --key-stop= --key-quit=` (inline empty values). **Never** pass empty-string values as separate argv entries (`--key-play=` `""`) — VLC treats the `""` as an empty MRL and opens a DVD instead of the URL.
- Both `MpvIpcClient` and `VlcIpcClient` expose the same interface: `connect`, `close`, `get_time_pos`, `get_pause`, `set_pause`, `seek`, `connected`. Broadcasts stay player-agnostic JSON.

## Key files
| File | Purpose |
|------|---------|
| `scrapers/provider_manager.py` | Provider chaining, `resolve_stream()` |
| `scrapers/miruro.py` | Miruro pipe decryption scraper (primary, working) |
| `scrapers/api_provider.py` | Consumet-protocol API scraper |
| `scrapers/mkissa.py` | Mkissa HTTP + Playwright scraper |
| `scrapers/gogoanime.py` | Gogoanime HTTP + Playwright scraper |
| `api.py` | Arabic provider `AnimeAPI` |
| `app.py` | Main entry, TUI mode |
| `cli.py` | Minimal CLI mode |
| `watch_together.py` | Watch Together: `SupabaseRealtime`, `MpvIpcClient`, `VlcIpcClient`, `WatchHost`/`WatchGuest` |
| `player.py` | `PlayerManager`: mpv/VLC arg builders, `build_vlc_args` (rc + lock flags) |

## External tooling
- **mpv** required for playback (auto-installed by `deps.py`)
- **ffmpeg** helper dependency
- **fzf** used for fuzzy selection in CLI mode when available
- **Playwright** (Chromium) for stream extraction on miruro (primary), mkissa, and gogoanime — browser shared at class level in MiruroScraper
- Set `ANI_API_BASE_URL` environment variable to point the `api` scraper at a self-hosted Consumet instance

## Version / packaging
- Single source of version: `ani_cli_arabic/version.py:__version__` (currently `1.8.4`)
- Build scripts in `scripts/` (build.sh, build.bat, build.py, setup.py)
- Install via `install.sh` or AUR package
- pypi distribution, AUR (`ani-cli-arabic`)

## Conventions
- No tests exist in the repo
- No formatter/linter config (no ruff, black, pylint config found)
- No CI workflows

## Execution guidelines

### Automatic Playwright fallback
- OpenCode is authorized and expected to use Playwright stealth browser rendering immediately when direct HTTP (httpx/requests) requests fail, return Cloudflare challenges (403/503), or encounter anti-bot protection.
- Do not wait for confirmation to use Playwright — fall back automatically from HTTP to headless Playwright to pass JS challenges and extract streams.

### Network & timeouts
- Enforce strict 5.0-second `asyncio.wait_for` timeout on direct HTTP stream resolution calls.
- For Playwright browser page loads, allow up to `_PROVIDER_TIMEOUT` (30.0s) since page rendering and JS execution take longer.
- All HTTP requests must include realistic browser headers (`User-Agent`, `Referer`, `Accept`) to minimize anti-bot blocking.
- Playwright browser is shared at the class level (`MiruroScraper._browser`) — thread-safe. A new context+page is created per pipe call.

### UI & error handling
- Print clean progress logs during provider evaluation: `[?] Attempting provider: {name}...`, `[✓] Stream found via {name}!`, `[✗] {name} returned no stream.`
- Never let `NoneType` values reach string operations (`.lower()`, `.strip()`, etc.) — always sanitize optional input with a safe default before use.
