# ani-cli-arabic

## Package structure
- Single package `ani_cli_arabic/`, entry point `ani_cli_arabic.app:main`
- Two UI entry paths: standard TUI (`app.py`, default) and minimal CLI (`cli.py`, `--interactive` flag)
- Two independent language tracks: Arabic (via `api.py` → `AnimeAPI`) and English (via `scrapers/`)

## English scraping pipeline
- `ENGLISH_PROVIDERS = ["miruro", "hianime", "allanime", "api", "mkissa", "gogoanime"]` in `scrapers/provider_manager.py`
- Every scraper MUST inherit `BaseScraper` (ABC) and implement `search(query)`, `get_episodes(anime_id)`, `get_stream_url(episode_id)`
- `ProviderManager.resolve_stream()` chains providers in order, per-step failure isolation (each method wrapped in try/except returning None)
- Provider timeout: `_PROVIDER_TIMEOUT = 30.0` seconds (accommodates Playwright page loads)
- Stream dict format: `{"stream_url": str|None, "headers": dict}`

## Provider details
| Provider | Source | Stream method | Status |
|----------|--------|---------------|--------|
| `miruro` | miruro.tv secure pipe via Playwright | Playwright headless → `page.evaluate(fetch)` on pipe API | **Working** — pipe returns gzip(base64(json)) with m3u8 URLs from `hls.anidb.app` via `pewe` provider |
| `hianime` | hianime.to `/ajax/` protocol | HTTP first, then Playwright `page.evaluate(fetch)` | Search/episodes parse from ajax JSON; sources CF-gated on most networks (mirror `hi-anime.co` reachable, `.to` unreachable) |
| `allanime` | allanime.* / allmanga.to GraphQL API | HTTP POST to `api.allanime.day/api` | **Search + episodes verified working** (fast HTTP); episode sources gated behind `AA_CRYPTO_MISSING` AES-GCM client handshake, `get_stream_url` returns None (falls through to next provider) |
| `api` | Consumet-protocol API | HTTP to `ANI_API_BASE_URL` | Requires self-hosted endpoint; `_discover()` auto-detects working public instances |
| `mkissa` | mkissa.to + GraphQL API | Playwright network interceptor | Search/episodes work via HTTP; stream blocked by Turnstile captcha |
| `gogoanime` | gogoanime.co.za + live mirror hosts | HTTP + embed resolver (`embeds.py`) | Search/episodes work via HTTP; live episode host auto-discovered from hrefs; embed (kwik.cx) blocked by Cloudflare on this network |
- Shared embed gateway in `embeds.py`: `extract_media_url(html)`, `resolve_embed(url, referer)` → plain HTTP first, Playwright route/capture fallback. gogoanime episode IDs are full URLs `{scheme}://{host}/{slug}/{ep}` parsed with `urlparse` in `get_stream_url`.
- Miruro uses **Playwright** (not curl_cffi) to bypass Cloudflare on `miruro.tv/api/secure/pipe`. The pipe endpoint is behind Cloudflare WAF and only responds from a real browser JS context. Implementation: shared `Browser` instance (thread-safe), new `BrowserContext`+`Page` per call, `page.goto(miruro.tv)` to set CF cookies, then `page.evaluate(fetch)` to call the pipe. Search still uses AniList GraphQL via httpx (no CF, fast).
- **AniList outage resilience**: AniList GraphQL (`graphql.anilist.co`) occasionally returns the documented 403 "temporarily disabled due to severe stability issues" outage signal — this is a real upstream outage, NOT a bot-block, and cannot be bypassed by headers/Playwright. When AniList search fails, `MiruroScraper.search()` falls back to the miruro pipe's own `search` path (`_search_pipe`), which returns AniList-shaped results (`id` = AniList id). Results are scored client-side by `_search_score()` (exact match 1.0, substring 0.9, word-overlap ratio) across romaji/english/native titles. Note: during outages the pipe search may return a generic popular list ignoring the query term — the client-side scoring is what filters it down.
- Miruro stream resolution is unaffected by the AniList outage as long as search returns an AniList id — the `episodes`/`sources` pipe paths only need `anilistId`.
- Provider priority in `_PROVIDER_PRIORITY`: `["pewe", "kiwi", "bee", "bonk", "ally", "moo", "hop"]`. `pewe` (anidb.app CDN) is first — returns playable m3u8 URLs. `kiwi` uses `uwucdn.top` which is blocked by Cloudflare (403). The `eid` values are shared across providers (animepahe IDs), so priority ordering determines which CDN is used.
- Streams from `hls.anidb.app` are playable in mpv with `Referer: https://anidb.app/`. Verified via httpx (200, m3u8 content) and mpv (plays 1920x1080 h264 successfully).
- Playwright browser is shared at the class level (`MiruroScraper._browser`) — thread-safe. A new context+page is created per pipe call. Total time: ~5s for episodes, ~4-5s for sources (within 30s provider timeout).
- curl_cffi is no longer used by miruro (pipe rejects curl_cffi with 403 CF challenge). If curl_cffi is still in dependencies, it can be removed.
- Old scraper files `animepahe.py` and `anikoto.py` exist in repo but are NOT registered in `provider_manager.py` (kept out of the chain). `animepahe.py` has a `_capture_json` guard so non-JSON Playwright responses no longer crash its search.
- AllAnime `get_stream_url` now performs the **full client-crypto handshake in pure Python** (no Playwright needed) and is verified working end-to-end:
  - `aa-boot` HMAC token (`x-aa-boot` header) from build mask `ev("81")` = `1c51425b...` (32 bytes).
  - Fresh bootstrap `GET api.mkissa.net/client-crypto/v1/bootstrap?buildId=81&k=k7` → `partB` + `epoch` (rotates every ~3 days; key = `partB XOR mask`). Must be fetched on every run.
  - `aaReq` = base64(`0x01` + iv[12] + AES-GCM(payload) + **tag[16]**). IV = first 12 bytes of `SHA-256(epoch:81:qh:ts:k7)`; payload `{v:1,ts,epoch,buildId:"81",qh,k:"k7"}` with `ts = floor(now/300000)*300000`. **Critical gotcha**: WebCrypto `encrypt()` returns ct+tag appended; in Python you must append `enc.tag` (missing it yields `AA_CRYPTO_STALE`).
  - POST to `api.mkissa.net/api` with the site's **exact F8 episode query text** (hash `2f563bb8...` — a custom/short query yields resolver crash `Cannot set properties of undefined`). Send `x-build-id:81` header + `extensions.persistedQuery.sha256Hash` = SHA-256 of the query text.
  - Response `tobeparsed` blob decrypts (AES-256-GCM, tag in last 16 bytes) to the real `sourceUrls` JSON.
  - `--`-prefixed sourceUrls are AllAnime's hex remap obfuscation (`_HEX_REMAP` table) → `/apivtwo/clock?id=...` paths (clock API is 404/CF-gated, generally unusable). Decoded via the table, not kept.
  - AllAnime sourceUrls are iframe embeds (Filemoon/Vidnest = CF-gated; Ok.ru = playable m3u8 via `hlsManifestUrl` in its metadata JSON, handled in `embeds.py` by `_HLS_MANIFEST_ESC_RE`). `get_stream_url` falls back to resolving embeds via `embeds.resolve_embed`.
- `api.allanime.day/api` validates the same crypto as mkissa (returns `AA_CRYPTO_STALE` for stale keys, `NEED_CAPTCHA` under IP rate-limit bursts) but `mkissa.net` is the working host.

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
| `scrapers/hianime.py` | HiAnime `/ajax/` HTTP + Playwright scraper |
| `scrapers/allanime.py` | AllAnime GraphQL scraper (search/episodes verified) |
| `scrapers/embeds.py` | Shared embed gateway: `extract_media_url`, `resolve_embed` |
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
- `cryptography` (not pycryptodome) is required by `allanime.py` for the best-effort `tobeparsed` AES-256-CTR decrypt

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

## Permanent Developer Directives

### Safety & Creative Autonomy

1. **Safety First (Sanity Check)**
   - Before implementing any requested feature or bugfix, verify that it does not introduce breaking changes, performance regressions, or platform incompatibilities (especially between Linux and Windows).
   - If a requested change is problematic or unsafe, DO NOT implement it blindly. Instead, skip or modify it safely, and explain the technical reasoning in the task summary.

2. **Creative Autonomy & Quality-of-Life Improvements**
   - You are empowered to introduce extra UX enhancements, code refactors, or subtle quality-of-life additions related to the user's current goal, even if not explicitly requested.
   - Any added proactive features must be clearly highlighted in the final output summary so the user is fully aware of them.
