# Changelog

All notable changes to **ani-cli-arabic** (ani-cli-ar), a terminal-based anime streaming client with Arabic subtitle support.

Format follows [Keep a Changelog](https://keepachangelog.com/) conventions. This project does not follow strict SemVer; minor and alpha/beta suffixes denote ongoing work.

---

## [v1.9.5] - 2026-08-03 (Stable Release)

Stable release consolidating the 1.9.5 alpha line.

### 🚀 New Features & Options
- Room roster / member list with live status badges (synced / buffering / drift) broadcast to all members in Watch Together.
- Host authority model: the host is the single source of truth for playback; guests apply host-driven state.
- Kick / promote (co-host) / control-toggle member management from the host.
- Host transfer to a guest member.

### 🐛 Bug Fixes & Stability
- Watch Together connection hardening: missing-package, missing-credentials, timeouts, channel creation, websocket subscribe, and `send_broadcast` failures now print full `traceback.format_exc()` diagnostics instead of failing silently.
- Guard against launching the player / resolving streams when no active media is loaded (empty `title`/`episode`/`url` payloads).
- Provider chain now rejects raw player-metadata dicts / JSON blobs passed as `stream_url` and falls back to the next provider.
- Stream URL extraction un-escapes html/js entities (`\u0026`, `\&quot;`, `&amp;`, `\/`) and only returns validated `http(s)` links.
- ok.ru / OKCDN embed resolution now correctly pulls `hlsManifestUrl` (playable manifest) instead of raw metadata.

### ⚡ Performance & Player Optimizations
- MPV imported arguments include `--demuxer-max-bytes=150M` and `--demuxer-readahead-secs=30`.
- Added `set_speed` to both `MpvIpcClient` and `VlcIpcClient` to support rate-based synchronization.
- VLC `--rc-quiet` gated on VLC 4.x+ (avoids unknown-option warnings on VLC 3.x).

---

## [v1.9.5a3] - 2026-08-03

### 🐛 Bug Fixes & Stability
- Version-pinning bump for the pre-release 1.9.5 line.

---

## [v1.9.5a2] - 2026-08-03

### 🐛 Bug Fixes & Stability
- Wave 2 pre-release corrections toward the 1.9.5 stable.

---

## [v1.9.5a1] - 2026-08-03

### 🚀 New Features & Options
- Watch Together member management: room roster tracking, member kick, promote to co-host, and transfer-host actions.
- Host-authority sync: guests follow host playback unconditionally.
- Status reporting from guests (drift / playing / buffering) driving live member badges.

---

## [v1.9.4] - 2026-08-02

### 🐛 Bug Fixes & Stability
- Added Windows TCP IPC fallback for the mpv Watch Together socket (`AF_UNIX` unavailable on some Windows Python builds).
- Miruro stream resolution fixed (browser pipe fallback robustness).
- Normalized local language state across provider switching.

### ⚡ Performance & Player Optimizations
- Watch Together IPv4 loopback IPC transport used on Windows.

---

## [v1.9.4b2] - 2026-08-01

### 🐛 Bug Fixes & Stability
- Fix Miruro stream resolution and normalize local language state.
- Beta corrections prior to the 1.9.4 release.

---

## [v1.9.4b1] - 2026-08-01

### 🚀 New Features & Options
- `--test` update flag to opt into beta/pre-release update checks.

### 🐛 Bug Fixes & Stability
- Beta-test release for the 1.9.4 line; changelog-less internal release.

---

## [v1.9.4-dev] - 2026-08-01

### 🐛 Bug Fixes & Stability
- Dev bump introducing `--test` update support and Miruro fixes.

---

## [v1.9.3] - 2026-08-01

### 🚀 New Features & Options
- Session tracking with live heartbeats.
- `--stats` flag: printable streaming-history summary.
- Error analytics with rich diagnostic details (player / provider / quality / error action attributes).

### ⚡ Performance & Player Optimizations
- VLC support added to Watch Together with per-session player selection (mpv or VLC).
- Host-only playback controls enforced for Watch Together guests.

---

## [v1.9.2] - 2026-07-31

### 🐛 Bug Fixes & Stability
- Version bump separating the Watch Together rewrite from telemetry work.

---

## [v1.9.1] - 2026-07-31

### 🚀 New Features & Options
- Watch Together rooms over Supabase Realtime Broadcast + mpv IPC (first release).
- Hardcoded default Supabase credentials so rooms work out of the box.

### 🐛 Bug Fixes & Stability
- Migrated to the **async** Supabase client: supabase-py 2.x only supports Realtime on the async client; the sync client's `channel()` raised `NotImplementedError`. Realtime now runs on a dedicated asyncio loop thread with a sync channel wrapper (`subscribe` / `send_broadcast` / `unsubscribe`).

---

## [v1.9.0] - 2026-07-31

### 🚀 New Features & Options
- **Preferred provider** setting in the TUI.
- **Player selection** across mpv / VLC with `ask` as the default.
- Analytics backend (serverless + `api/index.py` deployment) for opt-in telemetry; fixed `usage_logs` RLS policies.
- `install.sh` installer; package renamed to `ani-cli-ar`.

### ⚡ Performance & Optimizations
- Playwright-based Miruro scraper replacing direct HTTP (bypasses Cloudflare on the secure pipe).
- Performance optimizations documented in `OPTIMIZATIONS.md`.
- Shared HTTP connection pooling and API discovery cache.

---

## [v1.8.9] - 2026-07-31

### 🚀 New Features & Options
- Dub language support.

### 🐛 Bug Fixes & Stability
- Retry / rate-limit handling in the provider chain.
- Update-flag plumbing for the updater.

---

## [v1.8.5] - 2026-07-26

### 🚀 New Features & Options
- Native English scraper pipeline (gogoanime / hi-anime / miruro-backed chain).
- Multiple media-player support (choose your preferred player).

### 🐛 Bug Fixes & Stability
- Working state snapshot tagged `v1.8.5-working`.

---

## [v1.8.4] - 2026-04-27 (approx.)

### 🧩 Features & Options
- Donation support section in the UI and README.
- README polish (Arabic layout, navigation/gap styling).

---

## [v1.8.3] - 2026-04-08

### 🐛 Bug Fixes & Stability
- Fixes #5.
- FIXED Downloads (aria2c / IDM flows).
- FIXED Favorites system.

---

## [v1.8.2] - 2026-03-20

### 🚀 New Features & Options
- Robust dependency installation with multi-mirror support and auto package-manager detection.
- Mandatory auto-updates with UI lockout when an update is pending.

### 🐛 Bug Fixes & Stability
- Fixed yt-dlp binary download path (bypasses pip resolution issues).
- Fixed MPV release parsing that could mistakenly download FFmpeg archives.
- Secured error handling, consolidated duplicate UI logic, improved cross-platform robustness.
- Removed redundant comments, AI slop, and unused imports.

### ⚡ Performance & Player Optimizations
- Cleaner project structure (removed stale `/out` folder and duplicate files).

---

## [v1.8.1] - 2026-01-08

### 🐛 Bug Fixes & Stability
- Fixed `fzf` integration on Windows.

---

## [v1.8] - 2026-01-07

### 🚀 New Features & Options
- **MAJOR CHANGES** — new rewrite documented in the changelog narrative.
- Star-history chart in README.

### 🐛 Bug Fixes & Stability
- Fixed PyPI publishing workflow.
- Fixed AUR workflow.
- Python 3.12 recommended (3.13+ may hit numpy compilation issues).

---

## [v1.7.3] - 2026-01-06

### 🚀 New Features & Options
- **MAJOR CHANGES** — added many features; see release notes.

---

## [v1.7.2] - 2026-01-04

### 🐛 Bug Fixes & Stability
- Fixes #3.
- General Linux compatibility — confirmed on kitty, konsole, and GNOME terminal.
- Cleaner folder-structure rework.

### ⚡ Performance & Player Optimizations
- Smoother overall terminal experience.

---

## [v1.7] - 2026-01-02

### 🚀 New Features & Options
- Redesigned GitHub page.
- Showcase section in README; new showcase video.

### 🐛 Bug Fixes & Stability
- Fixed mpv failing to launch on Linux Mint (community-reported).
- Fixed showcase-video resolution issue.
- Fixed release workflow (changelog special characters) and build config (license / classifiers).

---

## [v1.6] - 2025-12-28

### 🚀 New Features & Options
- Reworked API and UI components for improved state management and update handling.

---

## [v1.5.x] - 2025-12-26

### 🐛 Bug Fixes & Stability
- v1.5.3: merge/cleanup release.
- Earlier 1.5: enhanced version parsing and display in `version_info.txt`.

---

## [v1.2.x] - 2025-12-23/25

### 🚀 New Features & Options
- **Download support**: aria2c (fast) + IDM integration on Windows.
- **MAL integration**: `Relevant` (R) option fetches currently-airing anime via the Jikan API with SFW filtering.
- **Watch history**: watched episodes marked with an eye icon.
- **Next/Previous/Replay** menu after watching or downloading.

### 🐛 Bug Fixes & Stability
- v1.2.1: workflow fix.

---

## [v1.3.x] - 2025-12-25

### 🐛 Bug Fixes & Stability
- 1.3.3: corrected terminology in the Arabic README for terminal usage.

---

## [v1.0.0] - 2025-11-26

### 🚀 New Features & Options
- Initial public release.
- Rich TUI built with the Rich library.
- 17 color themes.
- Multiple quality options (1080p / 720p / 480p).
- Batch episode downloading.
- Trailer support (YouTube).
- Search (English / Japanese / Arabic titles), trending, top-rated, genre and studio browsing.
- Watch history, favorites system, and episode tracking.
- Discord Rich Presence with anime posters.
- Automatic update checker (can be disabled).
- Dependency auto-installer.
- CLI mode (`-i "title"`) plus TUI; cross-platform Windows / Linux.

---

[v1.8.2]: https://github.com/np4abdou1/ani-cli-arabic/releases/tag/v1.8.2
[v1.8.3]: https://github.com/np4abdou1/ani-cli-arabic/releases/tag/v1.8.3
[v1.8.5]: https://github.com/np4abdou1/ani-cli-arabic/releases/tag/v1.8.5-working
[v1.9.0]: https://github.com/np4abdou1/ani-cli-arabic/releases/tag/v1.9.0
[v1.9.1]: https://github.com/np4abdou1/ani-cli-arabic/releases/tag/v1.9.1
[v1.9.4]: https://github.com/np4abdou1/ani-cli-arabic/releases/tag/v1.9.4
[v1.9.5]: https://github.com/np4abdou1/ani-cli-arabic/releases/tag/v1.9.5