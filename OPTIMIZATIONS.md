# Performance Optimizations — Future Enhancements

This document tracks **trade-off optimizations** for `ani-cli-ar`. They were analyzed but **NOT implemented** because each involves a minor sacrifice (staleness risk, cache invalidation, rate-limit exposure, or reduced safety margin).

The zero-sacrifice optimizations already shipped (Playwright resource blocking, MPV buffering flags, shared HTTP connection pools, API discovery cache) live in the normal codebase; the items below are intentionally deferred.

---

## 1. Metadata Caching (6-hour TTL for episode lists)

**File:** `scrapers/miruro.py`, `scrapers/provider_manager.py`

### Speed gain
- Skips the entire Miruro pipe round-trip (~4–6s per call, currently a fresh Playwright session) on repeat episode loads.
- Biggest win for: switching episodes, replaying, "auto-next", and revisiting the same anime in one session.
- Typical save: **4–6s** per episode-list load after the first.

### Risk / drawback
- Episode lists go **stale**: newly aired episodes won't appear until the TTL expires.
- Requires a manual "refresh" affordance (e.g. force-refresh when TTL expired, or a hotkey) or a modest TTL.

### Implementation details
- Add a cache keyed by `(anilist_id, preferred_category)` → `(timestamp, episode_list)`.
- Use a module-level `dict` plus `time.time()`; no external dependency:
  ```python
  _EP_CACHE_TTL = 6 * 3600  # seconds
  _episode_cache: dict[tuple, tuple[float, list]] = {}
  ```
- In `MiruroScraper.get_episodes()`:
  - Check cache first: `cached = _episode_cache.get(key); if cached and time.time() - cached[0] < _EP_CACHE_TTL: return cached[1]`
  - On successful pipe fetch, store `(time.time(), result)`.
  - On `[]` result, **do not** cache (avoids poisoning cache on transient failures).
- Invalidate or force-refresh path: expose an optional `refresh=True` parameter threaded through `get_episodes()` → `ProviderManager.resolve_stream()` so a "Refresh" menu action bypasses the cache.
- Keep the key language-aware (include `preferred_category`) so sub/dub never cross-contaminate.
- Note: the cache holds in-memory only (per process). Persisting to disk is NOT recommended — TTL correctness gets complicated and stale files confuse users.

### Recommendation
**Implement later.** High value, low complexity. Requires only the TTL + a refresh escape hatch.

---

## 2. Short-TTL Stream URL Caching (120-second cache with auto-refresh on failure)

**File:** `scrapers/miruro.py` (`get_stream_url`), optionally `scrapers/provider_manager.py`

### Speed gain
- Replay or immediate next-episode playback of the same episode skips the pipe `sources` call (~4–5s).
- Also avoids re-hitting Miruro's rate limiter on rapid replays.

### Risk / drawback
- CDN token URLs (`hls.anidb.app`) may expire mid-cache → a **dead link** on replay.
- Mitigated by the 120s TTL + auto-refresh-on-failure (below).

### Implementation details
- Cache keyed by episode id string → `(timestamp, stream_url, headers)`:
  ```python
  _STREAM_CACHE_TTL = 120  # seconds
  _stream_cache: dict[str, tuple[float, str, dict]] = {}
  ```
- In `MiruroScraper.get_stream_url()`:
  - Return cached entry if `time.time() - ts < _STREAM_CACHE_TTL`.
  - Otherwise fetch via `_pipe_fetch`; on success store `(time.time(), url, headers)`.
- **Auto-refresh on failure:** in `provider_manager._try_provider()` (or the player-launch path), when mpv/playback of a cached URL fails with a network/CDN error:
  - Purge the cache entry.
  - Re-run `get_stream_url()` once and retry playback before giving up.
- Never cache a `None`/empty result.
- Keep the TTL short (120s) so a stale URL is at most briefly served and auto-refresh recovers quickly.

### Recommendation
**Implement later.** Low complexity; the auto-refresh-on-failure is the critical safety piece — do not ship the cache without it.

---

## 3. Disable `--networkidle` wait in Miruro (`domcontentloaded` + cookie polling)

**File:** `scrapers/miruro.py` — `_pipe_fetch()`

### Speed gain
- The current `page.goto(MIRURO_BASE, wait_until="networkidle", timeout=25000)` waits until the network has been idle for 500ms. Even with resource blocking, tracking beacons / long-polling can stretch this.
- Switching to `wait_until="domcontentloaded"` + explicit CF-cookie polling saves roughly **1–3s per pipe call** (both `episodes` and `sources` calls).

### Risk / drawback
- The Cloudflare WAF cookie (`__cf_bm`/`cf_clearance`) may not be finalized by `domcontentloaded` → **intermittent pipe 403s** if we evaluate `fetch` too early.
- This is the highest-risk optimization in the list — it touches the anti-bot bypass directly.

### Implementation details
- In `_pipe_fetch()`, replace:
  ```python
  page.goto(MIRURO_BASE, wait_until="networkidle", timeout=25000)
  ```
  with:
  ```python
  page.goto(MIRURO_BASE, wait_until="domcontentloaded", timeout=25000)
  ```
- After navigation, poll for readiness before evaluating the pipe fetch:
  ```python
  deadline = time.time() + 20
  while time.time() < deadline:
      ready = page.evaluate(
          "() => document.cookie.includes('cf') || typeof fetch === 'function'"
      )
      if ready:
          break
      time.sleep(0.5)
  ```
  (A stronger check: attempt a lightweight `page.evaluate("fetch('/')")` probe and retry until it returns a non-403 status.)
- Keep the existing `_RETRYABLE_STATUS` logic so an occasional 403 falls back to the normal retry loop with backoff.
- **Must be validated in staging against a real `miruro.tv` pipe call** before shipping — confirm the m3u8 still resolves with HTTP 200 and mpv playback.

### Recommendation
**Test in staging first.** Potentially the biggest remaining per-call win, but the only one that can break the core streaming path if the cookie check is too weak.

---

## 4. Background Pre-fetching for Auto-Next Episodes

**File:** `app.py` (`handle_episode_selection`, `handle_english_stream`, `_fetch_english_stream`), `cli.py` (`play_video`)

### Speed gain
- While the user watches episode N, the app starts resolving episode N+1's stream in a background thread.
- Saves ~4–5s of perceived delay on auto-next / manual "Next Episode".

### Risk / drawback
- An extra Miruro pipe call per episode → **higher Cloudflare rate-limit exposure** (`_REQUEST_INTERVAL` is currently 1.0s; the WAF could throttle).
- Wasted bandwidth/pipe calls if the user quits or backtracks instead of continuing.
- Playwright sessions must remain thread-safe (already enforced by the class-level `_rate_limit_lock`, but a long-running background browser could outlive the foreground one).

### Implementation details
- Respect the existing rate limiter (`MiruroScraper._respect_rate_limit()`) — pre-fetch uses the same shared lock.
- Use a **single pending slot** so at most one episode is prefetched at a time:
  ```python
  self._prefetch_lock = threading.Lock()
  self._prefetched_stream = None  # (episode_num, url, headers)
  ```
- Trigger after a stream successfully starts playing in `handle_english_stream()` (episode N). Spawn a daemon thread that calls `_fetch_english_stream(title, ep_num=N+1, ...)` and stores the result in the slot.
- On "Next Episode", check the slot first:
  - If it matches `N+1`, use it immediately (skip the loading spinner).
  - If empty or stale (episode mismatch), fall back to the normal synchronous resolve.
- Clear the slot when the episode selection menu reopens or the user navigates away.
- Guard with a hard cap: never hold more than one prefetched stream; discard on any `KeyboardInterrupt`/exit.

### Recommendation
**Implement cautiously, later.** Strong UX win for marathon watching, but needs the rate-limit guard and single-slot cap to avoid tripping Cloudflare on `miruro.tv`.

---

## Summary

| # | Optimization | Est. gain | Key risk | Ready when |
|---|--------------|-----------|----------|------------|
| 1 | Metadata caching (6h TTL) | 4–6s per ep list reload | Stale episode counts | TTL + refresh escape hatch added |
| 2 | Stream URL cache (120s TTL) | 4–5s per replay | Expired CDN token | Auto-refresh-on-failure shipped |
| 3 | No `networkidle` wait | 1–3s per pipe call | Pipe 403s if CF cookie late | Validated against live pipe + mpv |
| 4 | Background pre-fetch | 4–5s on auto-next | CF rate-limit exposure | Single-slot cap + rate-limit guard |

All four are independent of the shipped zero-sacrifice optimizations and can be layered on incrementally.
