"""PyWebView desktop GUI bridge for ani-cli-arabic.

Exposes a JSApi class whose methods are callable directly from JavaScript via
``pywebview.api.search(...)`` etc. The GUI is a self-contained single-page app
in ``ani_cli_arabic/ui/index.html`` that talks to this bridge.

Run with:  ``ani-cli-arabic --gui``  (or ``python -m ani_cli_arabic.gui``)
"""
import functools
import json
import os
import re as _re
import sys
import threading
import time
import urllib.request
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor, as_completed, wait
from pathlib import Path
from typing import Any, Dict, List, Optional

from .scrapers._http_log import timed

from .version import APP_VERSION, __version__

# Lazy imports so the GUI can fail fast with a friendly message when the
# optional runtime/webview dependencies are missing.
try:
    import webview
    _HAS_WEBVIEW = True
except ImportError:  # pragma: no cover - environment without pywebview
    _HAS_WEBVIEW = False
    webview = None


_UI_DIR = Path(__file__).resolve().parent / "ui"
_INDEX_HTML = _UI_DIR / "index.html"

_ANILIST_GRAPHQL = "https://graphql.anilist.co"
_PROVIDER_TIMEOUT = 3.5
_CHOSEN_PROVIDER_TIMEOUT = 25.0
_MAX_SEARCH_CACHE = 128
_DETAIL_QUICK_WINDOW = 4.0


def _supports_kwarg(fn, name: str) -> bool:
    """True when ``fn`` accepts a keyword argument ``name``."""
    try:
        import inspect
        return name in inspect.signature(fn).parameters
    except (TypeError, ValueError):
        return False


def _call_maybe_cancel(scraper, method: str, *args, abort_event=None):
    """Call ``scraper.method(*args)``, forwarding the abort event to the
    scraper when it supports the ``cancel_event`` keyword."""
    fn = getattr(scraper, method, None)
    if fn is None:
        return None
    if abort_event is not None and _supports_kwarg(fn, "cancel_event"):
        return fn(*args, cancel_event=abort_event)
    return fn(*args)

# Arabic Subtitle track — routes the GUI to the Arabic API pipeline (same
# scraper-less flow the CLI uses for "Arabic Sub").
ARABIC_CATEGORY = "ar_sub"
ARABIC_PROVIDER = "arabic_api"
_ARABIC_QUALITY_KEYS = {"1080p": "FRFhdQ", "720p": "FRLink", "480p": "FRLowQ"}
_SUBTITLE_EXT_RE = _re.compile(r"\.(srt|vtt|ass|ssa)(?:\?|$)", _re.IGNORECASE)

_SEARCH_GRAPHQL = """\
query ($search: String, $page: Int, $perPage: Int) {
  Page(page: $page, perPage: $perPage) {
    media(search: $search, type: ANIME, sort: [SEARCH_MATCH, POPULARITY_DESC]) {
      id
      title { romaji english native }
      coverImage { large medium }
      seasonYear
    }
  }
}"""

_ANILIST_DETAIL_QUERY = """\
query ($id: Int) {
  Media(id: $id, type: ANIME) {
    id
    title { romaji english native }
    bannerImage
    coverImage { extraLarge large color }
    averageScore
    meanScore
    popularity
    status
    format
    season
    seasonYear
    episodes
    duration
    genres
    description
    studios(isMain: true) { nodes { name } }
    nextAiringEpisode { episode timeUntilAiring }
  }
}"""


_TYPE_LABELS = {
    "TV": "TV",
    "MOVIE": "Movie",
    "ONA": "ONA",
    "OVA": "OVA",
    "TV_SHORT": "TV Short",
    "SPECIAL": "Special",
    "MUSIC": "Music",
}

_STATUS_LABELS = {
    "RELEASING": "Airing",
    "FINISHED": "Completed",
    "NOT_YET_RELEASED": "Upcoming",
    "CANCELLED": "Cancelled",
    "HIATUS": "Hiatus",
}


def _anilist_media(anime_id: str) -> Dict[str, Any]:
    """Fetch raw AniList Media payload (cached). Returns {} on any failure."""
    try:
        import httpx
        r = httpx.post(
            _ANILIST_GRAPHQL,
            json={
                "query": _ANILIST_DETAIL_QUERY,
                "variables": {"id": int(anime_id)},
            },
            timeout=8.0,
        )
        if r.status_code != 200:
            return {}
        media = (r.json().get("data") or {}).get("Media") or {}
        return dict(media)
    except Exception:
        return {}


@functools.lru_cache(maxsize=128)
def _anilist_meta(anime_id: str) -> Dict[str, Any]:
    """Best-effort formatted AniList metadata for one id (memoized).

    Miruro/animepahe results carry AniList ids, so the same id resolves
    cover art, score, studios, genres and synopsis. Returns an empty dict
    when the id is not an AniList id or the API is unreachable.
    """
    media = _anilist_media(str(anime_id or ""))
    if not media:
        return {}

    desc = media.get("description") or ""
    import re as _re
    desc = _re.sub(r"<[^>]+>", "", desc).replace("\r\n", "\n").strip()
    if len(desc) > 1600:
        desc = desc[:1600].rstrip() + "…"

    studios = [
        n.get("name") for n in (media.get("studios") or {}).get("nodes") or []
        if n.get("name")
    ]

    cover = media.get("coverImage") or {}
    season = media.get("season")
    season_year = media.get("seasonYear")
    premiered = ""
    if season and season_year:
        premiered = f"{season.title()} {season_year}"
    elif season_year:
        premiered = str(season_year)

    score = None
    for key in ("meanScore", "averageScore"):
        val = media.get(key)
        if val:
            score = float(val) / 10.0
            break
    if score is not None:
        score = round(score, 2)

    return {
        "id": str(media.get("id") or anime_id),
        "title": (media.get("title") or {}).get("english")
                 or (media.get("title") or {}).get("romaji")
                 or anime_id,
        "romaji": (media.get("title") or {}).get("romaji") or "",
        "native": (media.get("title") or {}).get("native") or "",
        "poster": cover.get("extraLarge") or cover.get("large"),
        "backdrop": media.get("bannerImage") or "",
        "score": score,
        "popularity": media.get("popularity"),
        "format": media.get("format"),
        "type": _TYPE_LABELS.get(media.get("format"), media.get("format") or "TV"),
        "season": season,
        "year": season_year,
        "premiered": premiered,
        "status": _STATUS_LABELS.get(media.get("status"), media.get("status") or ""),
        "genres": list(media.get("genres") or []),
        "studio": studios[0] if studios else "",
        "studios": studios,
        "description": desc,
        "episodes": media.get("episodes"),
        "duration": media.get("duration"),
    }


@functools.lru_cache(maxsize=128)
def _anilist_search(query: str, limit: int = 30) -> List[Dict[str, Any]]:
    """Fast AniList search (cached): ``[{"id", "title", "poster"}, ...]``."""
    try:
        import httpx
        r = httpx.post(
            _ANILIST_GRAPHQL,
            json={
                "query": _SEARCH_GRAPHQL,
                "variables": {"search": query, "page": 1, "perPage": int(limit)},
            },
            timeout=8.0,
        )
        if r.status_code != 200:
            return []
        media = (r.json().get("data") or {}).get("Page") or {}
        out = []
        for m in media.get("media") or []:
            t = m.get("title") or {}
            cover = m.get("coverImage") or {}
            out.append({
                "id": str(m.get("id")),
                "title": t.get("english") or t.get("romaji") or "",
                "poster": cover.get("large") or cover.get("medium"),
                "year": m.get("seasonYear"),
            })
        return [x for x in out if x["id"] and x["title"]]
    except Exception:
        return []


def _load_bridge() -> "ProviderManager":
    """Build a ProviderManager. Imported lazily to keep startup snappy."""
    from .scrapers import ProviderManager
    return ProviderManager()


def _title_key(title: str) -> str:
    """Lowercased alphanumeric-only title key for provider id matching."""
    return _re.sub(r"[^a-z0-9]+", "", (title or "").lower())


def _title_overlap(a: str, b: str) -> float:
    """Word-overlap ratio in (0,1] or 0 when disjoint across the query words."""
    wa = set(_re.findall(r"[a-z0-9']+", (a or "").lower()))
    wb = set(_re.findall(r"[a-z0-9']+", (b or "").lower()))
    if not wb:
        return 0.0
    return len(wa & wb) / len(wb)


def _hit_score(hit_title: str, query: str) -> float:
    """0..1 similarity between a search hit's title and the title we want."""
    text = _title_key(hit_title)
    want = _title_key(query)
    if not text:
        return 0.0
    if want and text == want:
        return 1.0
    if want and (want in text or text in want):
        return 0.9
    return _title_overlap(hit_title, query)


def _pick_provider_hit(hits, title) -> Optional[Dict]:
    """Pick the best ``search()`` hit for a given anime title.

    Prefers an exact normalized-title match, then a substring/word-overlap
    match, falling back to the first result (mirrors CLI behavior)."""
    if not hits:
        return None
    best = None
    best_score = 0.0
    for h in hits:
        score = _hit_score(h.get("title"), title)
        if score > best_score:
            best_score = score
            best = h
    return best if best is not None else hits[0]


def _pick_arabic_hit(hits, title) -> Optional[Any]:
    """Pick the best ``AnimeAPI.search_anime()`` hit for a title.

    Scores on the EN title (exact normalized match, then substring/word
    overlap), mirroring ``_pick_provider_hit``, falling back to the first
    result like the CLI's Arabic Sub flow."""
    if not hits:
        return None
    best = None
    best_score = 0.0
    for h in hits:
        cand = getattr(h, "title_en", "") or ""
        score = _hit_score(cand, title)
        if score > best_score:
            best_score = score
            best = h
    return best if best is not None else hits[0]


def _rank_hit_score(title_score: float, eps_len, meta: Dict[str, Any]) -> float:
    """Rebalance a title-match score using the hit's episode count vs AniList.

    TV / long-running shows: heavy penalty for 1-episode hits (movies/specials/
    OVAs get fished up by title search), a bonus when the count closely matches
    AniList's total, and small bumps for obviously long-running series. Movies
    and specials expect exactly one episode, so a single-ep hit is ideal.
    """
    fmt = (meta.get("format") or "").upper()
    anilist_eps = meta.get("episodes") or 0
    is_single = fmt in ("MOVIE", "SPECIAL")
    s = float(title_score or 0.0)

    try:
        eps_len = int(eps_len)
    except (TypeError, ValueError):
        return s

    if is_single:
        return s + 0.25 if eps_len == 1 else s

    if eps_len == 1:
        s -= 1.0
    elif eps_len <= 3:
        s -= 0.5
    elif anilist_eps:
        if abs(eps_len - anilist_eps) <= max(2, int(anilist_eps * 0.10)):
            s += 0.30
        elif eps_len >= int(anilist_eps * 0.8):
            s += 0.15
        elif eps_len <= int(anilist_eps * 0.5):
            s -= 0.20
    # A very long episode list is damning evidence this is the main TV series
    # (a movie/OVA/special never has 100+ entries), which outweighs an odd or
    # missing title match (e.g. AllAnime "1P" for One Piece).
    if eps_len >= 200:
        s += 0.60
    elif eps_len >= 100:
        s += 0.35
    elif eps_len >= 12:
        s += 0.10
    return s


class JSApi:
    """Python<->JS bridge exposed to the webview as ``pywebview.api``.

    Every public method runs on a background thread so network/stream work
    never blocks the UI thread; results are returned as JSON-serializable
    Python values (dicts/lists/str/bool/None).
    """

    def __init__(self):
        self._manager: Optional[Any] = None
        self._player = None
        self._watch_host = None
        self._watch_guest = None
        self._history = None
        self._search_cache: OrderedDict[str, List[Dict]] = OrderedDict()
        self._ep_cache: OrderedDict[str, List[Dict]] = OrderedDict()
        self._raw_eps_cache: OrderedDict[str, List[Dict]] = OrderedDict()
        self._detail_cache: OrderedDict[str, Dict[str, Any]] = OrderedDict()
        self._provider_id_cache: OrderedDict[str, Optional[str]] = OrderedDict()
        self._hit_eps_cache: OrderedDict[str, Optional[int]] = OrderedDict()
        self._arabic_anime_cache: OrderedDict[str, Optional[Dict]] = OrderedDict()
        self._schedule_cache: OrderedDict[str, List[Dict]] = OrderedDict()
        self._refine_inflight: set = set()
        self._lock = threading.Lock()
        self._cache_lock = threading.RLock()
        self._update_state: Dict[str, Any] = {"checking": False, "checked": False}
        self._update_lock = threading.Lock()

    def _cache_put(self, cache: OrderedDict, key: str, value, maxsize: int) -> None:
        with self._cache_lock:
            cache[key] = value
            cache.move_to_end(key)
            while len(cache) > maxsize:
                cache.popitem(last=False)

    # ------------------------------------------------------------------
    # lazy helpers
    # ------------------------------------------------------------------
    def _pm(self):
        if self._manager is None:
            with self._lock:
                if self._manager is None:
                    self._manager = _load_bridge()
        return self._manager

    def _player_mgr(self):
        if self._player is None:
            from .player import PlayerManager
            self._player = PlayerManager()
        return self._player

    def _history_mgr(self):
        if self._history is None:
            from .history import HistoryManager
            self._history = HistoryManager()
        return self._history

    # ------------------------------------------------------------------
    # info
    # ------------------------------------------------------------------
    def get_version(self) -> Dict[str, str]:
        """Return app + providers info for the GUI header/status bar."""
        return {
            "version": APP_VERSION,
            "providers": self._pm().available_providers,
        }

    def get_available_players(self) -> Dict[str, str]:
        """Return detected players, e.g. {'MPV': '/usr/bin/mpv', 'VLC': ...}."""
        try:
            return self._player_mgr().get_available_players()
        except Exception:
            return {}

    # ------------------------------------------------------------------
    # in-app updates
    # ------------------------------------------------------------------
    def check_for_updates(self) -> Dict[str, Any]:
        """Compare the running version against the latest PyPI release.

        Runs the HTTP check on a background thread so the UI is never
        blocked; the first call triggers the fetch and returns immediately,
        subsequent calls return the cached result. The resolved state is also
        pushed to the webview via a ``update-checked`` DOM event so the UI can
        render an "Update Available" banner without polling.
        """
        with self._update_lock:
            if self._update_state.get("checked"):
                return dict(self._update_state)
            if self._update_state.get("checking"):
                return dict(self._update_state)

        self._update_state = {"checking": True, "checked": False}
        threading.Thread(target=self._fetch_update_state, daemon=True).start()
        return dict(self._update_state)

    def _fetch_update_state(self) -> None:
        """Background worker: query PyPI, build the state dict, notify UI."""
        state = {"checking": False, "checked": True, "current": __version__}
        try:
            req = urllib.request.Request(
                "https://pypi.org/pypi/ani-cli-ar/json",
                headers={"User-Agent": f"ani-cli-ar/{__version__}"},
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            latest = str(data.get("info", {}).get("version") or "").strip()
            if latest:
                state["latest"] = latest
                state["update_available"] = self._version_gt(latest, __version__)
            else:
                state["update_available"] = False
                state["error"] = "No version info returned by PyPI."
        except Exception as exc:
            state["update_available"] = False
            state["error"] = str(exc)

        with self._update_lock:
            self._update_state = state
        self._push_update_state(state)

    def _version_gt(self, a: str, b: str) -> bool:
        """Simple dotted-numeric version comparison, ignoring pre-release tags."""
        import re as _re

        def _nums(ver):
            nums = []
            for part in str(ver).strip().lower().lstrip("v").replace("-", ".").split("."):
                m = _re.match(r"(\d+)", part)
                if m:
                    nums.append(int(m.group(1)))
                else:
                    nums.append(0)
            return nums

        return tuple(_nums(a)) > tuple(_nums(b))

    def _push_update_state(self, state: Dict[str, Any]) -> None:
        """Deliver the update state to the frontend via a DOM custom event."""
        try:
            import webview as _wv
            for win in getattr(_wv, "windows", []) or []:
                if win and getattr(win, "loaded", False):
                    payload = json.dumps(state)
                    win.evaluate_js(
                        f"window.dispatchEvent(new CustomEvent('update-checked',"
                        f"{{detail: {payload}}}))"
                    )
        except Exception:
            pass

    # ------------------------------------------------------------------
    # search / episodes
    # ------------------------------------------------------------------
    def search(self, query: str, language: str = "english") -> List[Dict]:
        """Search anime, returning results fast.

        Fast path: cached AniList query (ids + posters) — this is the bulk of
        what users see. Provider availability/coverage is enriched in the
        background and pushed to the UI via a ``search-refined`` DOM event so
        the grid renders instantly with posters and then gains server badges.

        Each item: ``{"id": str, "title": str, "provider": str,
        "poster": str|None, "providers": list}``.
        """
        query = (query or "").strip()
        if not query:
            return []
        lang = "english" if "arabic" not in (language or "").lower() else "arabic"
        cache_key = f"{lang}:{query.lower()}"

        with self._lock:
            if cache_key in self._search_cache:
                return self._search_cache[cache_key]

        # Fast path: AniList immediately (miruro ids ARE AniList ids).
        results = []
        seen = set()
        for hit in _anilist_search(query):
            aid = hit["id"]
            if aid in seen:
                continue
            seen.add(aid)
            results.append({
                "id": aid,
                "title": hit["title"],
                "provider": "miruro",
                "poster": hit.get("poster") or self._poster_for(aid),
                "year": hit.get("year"),
                "providers": [],
            })

        with self._lock:
            self._cache_put(self._search_cache, cache_key, results, _MAX_SEARCH_CACHE)

        if results:
            self._start_refine(cache_key, query, lang, list(results))

        return list(results)

    def _start_refine(self, cache_key, query, lang, base):
        """Kick the background provider-enrichment worker (single-flight)."""
        with self._lock:
            if cache_key in self._refine_inflight:
                return
            self._refine_inflight.add(cache_key)
        threading.Thread(
            target=self._refine_search,
            args=(cache_key, query, lang, base),
            daemon=True,
        ).start()

    def _refine_search(self, cache_key, query, lang, base) -> None:
        """Background search enrichment: probe providers in parallel, attach
        which providers have each title, then fire ``search-refined``."""
        try:
            enriched = self._enrich_results(query, base)
            with self._lock:
                self._search_cache[cache_key] = enriched
                self._search_cache.move_to_end(cache_key)
            self._dispatch_event("search-refined", {"query": query, "results": enriched})
        finally:
            with self._lock:
                self._refine_inflight.discard(cache_key)

    def _enrich_results(self, query, base):
        """Probe all providers in parallel (± per-provider timeout) and merge
        provider coverage into the base (miruro/AniList) result cards."""
        pm = self._pm()

        def _probe(name):
            scraper = pm._providers.get(name)
            if scraper is None:
                return None
            try:
                items = scraper.search(query) or []
                return {
                    "ids": [str(x.get("id") or "") for x in items],
                    "titles": [(x.get("title") or "").lower().strip() for x in items],
                }
            except Exception:
                return None

        names = [n for n in pm.available_providers if pm._providers.get(n)]
        results_map = self._parallel_map(names, _probe)

        # Merge coverage: match provider ids against base ids; also match by
        # normalized title so providers with different id schemes still count.
        out = [dict(r) for r in base]
        for r in out:
            r["providers"] = [r["provider"]] if r.get("provider") and r["provider"] != "miruro" else []
        title_index = {}
        for r in out:
            t = (r.get("title") or "").lower().strip()
            if t:
                title_index.setdefault(t, r)
        for name, payload in results_map.items():
            if not name or payload is None:
                continue
            for pid in (payload.get("ids") or []):
                for r in out:
                    if r.get("id") == pid:
                        if name not in r["providers"]:
                            r["providers"].append(name)
                        break
            for t in (payload.get("titles") or []):
                target = title_index.get(t) or title_index.get(t.rstrip("!"))
                if target is None or not t:
                    continue
                if name not in target["providers"]:
                    target["providers"].append(name)
        return out

    def _parallel_map(self, names, fn):
        """Run ``fn(name)`` for every name on a thread pool; a strict per-future
        timeout keeps dead/slow scrapers from blocking the caller (≈3.5s
        wall-clock bound). Returns {name: result} for the futures that finished.
        """
        names = [n for n in names if n]
        if not names:
            return {}
        ex = ThreadPoolExecutor(max_workers=min(len(names), 6))
        outcomes = {}
        try:
            futs = {ex.submit(fn, n): n for n in names}
            done, _ = wait(list(futs), timeout=_PROVIDER_TIMEOUT)
            for fut in done:
                n = futs[fut]
                try:
                    outcomes[n] = fut.result(timeout=0)
                except Exception:
                    outcomes[n] = None
        finally:
            ex.shutdown(wait=False)
        return outcomes

    def _dispatch_event(self, name: str, payload: Any) -> None:
        """Deliver an event to the frontend via a DOM custom event."""
        try:
            import webview as _wv
            for win in getattr(_wv, "windows", []) or []:
                if win and getattr(win, "loaded", False):
                    win.evaluate_js(
                        f"window.dispatchEvent(new CustomEvent('{name}',"
                        f"{{detail: {json.dumps(payload)}}}))"
                    )
        except Exception:
            pass

    def _poster_for(self, anime_id: str) -> Optional[str]:
        """Best-effort poster URL via AniList cover art (miruro ids are AniList
        ids). Returns None when unavailable so the GUI shows a placeholder."""
        return (self._anilist_details(anime_id) or {}).get("poster")

    # ------------------------------------------------------------------
    # details
    # ------------------------------------------------------------------
    def _anilist_details(self, anime_id: str) -> Dict[str, Any]:
        """Return rich per-title AniList metadata for one id.

        Miruro/animepahe results carry AniList ids, so the same id resolves
        cover art, score, studios, genres and synopsis. Delegates to the
        memoized module-level ``_anilist_meta`` for O(1) repeat access.
        """
        return _anilist_meta(str(anime_id or ""))

    def get_trending(self, limit: int = 12) -> List[Dict]:
        """Return a curated trending/popular list for the home screen.

        Each result is shaped like a search hit (``{"id", "title",
        "provider", "poster"}``) so the same grid renders both sources.
        """
        try:
            import httpx
            r = httpx.post(
                "https://graphql.anilist.co",
                json={
                    "query": """\
                    query ($page: Int, $perPage: Int) {
                      Page(page: $page, perPage: $perPage) {
                        media(sort: TRENDING_DESC, type: ANIME) {
                          id
                          title { romaji english native }
                          coverImage { medium }
                        }
                      }
                    }""",
                    "variables": {"page": 1, "perPage": int(limit)},
                },
                timeout=8.0,
            )
            if r.status_code != 200:
                return []
            media = (r.json().get("data") or {}).get("Page") or {}
            out = []
            for m in media.get("media") or []:
                t = m.get("title") or {}
                out.append({
                    "id": str(m.get("id")),
                    "title": t.get("english") or t.get("romaji") or "",
                    "provider": "trending",
                    "poster": (m.get("coverImage") or {}).get("medium") or "",
                })
            return [x for x in out if x["id"] and x["title"]]
        except Exception:
            return []

    def get_schedule(self, day: str = "") -> List[Dict]:
        """AniList airing schedule for the coming week, optionally filtered to a
        single weekday. Day keys are lowercase 3-letter abbreviations
        (``"sun"``..``"sat"``); pass ``""`` for everything. Backed by a weekly
        cache so the frontend day filter is instant after the first fetch.
        """
        day = (day or "").strip().lower()
        key = "week"
        with self._lock:
            items = self._schedule_cache.get(key)
        if items is None:
            items = self._fetch_schedule()
            with self._lock:
                self._cache_put(self._schedule_cache, key, items, 4)
        if day:
            items = [x for x in items if (x.get("day") or "").lower() == day]
        return list(items)

    def _fetch_schedule(self) -> List[Dict]:
        """Pull currently-airing anime with next-airing timestamps, bucketed by
        weekday. Non-fatal: returns [] on any failure (the UI renders an empty
        schedule bar). Sorted by airing time, soonest first."""
        out: List[Dict] = []
        try:
            import datetime
            import httpx
            r = httpx.post(
                _ANILIST_GRAPHQL,
                json={
                    "query": """\
                    query ($page: Int, $perPage: Int) {
                      Page(page: $page, perPage: $perPage) {
                        media(status: RELEASING, sort: [POPULARITY_DESC], type: ANIME) {
                          id
                          title { romaji english }
                          coverImage { large }
                          episodes
                          nextAiringEpisode { episode airingAt }
                        }
                      }
                    }""",
                    "variables": {"page": 1, "perPage": 60},
                },
                timeout=10.0,
            )
            if r.status_code != 200:
                return out
            media = (r.json().get("data") or {}).get("Page") or {}
            now_ts = time.time()
            for m in media.get("media") or []:
                t = m.get("title") or {}
                nxt = m.get("nextAiringEpisode") or {}
                airing_at = nxt.get("airingAt")
                if not airing_at:
                    continue
                try:
                    day = datetime.datetime.fromtimestamp(airing_at).strftime("%a").lower()
                except Exception:
                    day = ""
                out.append({
                    "id": str(m.get("id")),
                    "title": t.get("english") or t.get("romaji") or "",
                    "poster": (m.get("coverImage") or {}).get("large") or "",
                    "episode": nxt.get("episode"),
                    "airing_at": int(airing_at),
                    "day": day,
                    "in": max(0, int(airing_at - now_ts)),
                })
        except Exception:
            return []
        out.sort(key=lambda x: x.get("airing_at") or 0)
        return [x for x in out if x["id"] and x["title"] and x["day"]]

    def get_anime_meta(self, anime_id: str) -> Dict[str, Any]:
        """Return AniList metadata only — instant (cached), never probes
        providers. Used to paint the details hero immediately."""
        anime_id = str(anime_id or "")
        meta = self._anilist_details(anime_id)
        if not meta:
            meta = {"id": anime_id, "title": anime_id}
        return meta

    def get_anime_details(
        self,
        anime_id: str,
        provider: Optional[str] = None,
        category: str = "sub",
    ) -> Dict[str, Any]:
        """Return metadata + episodes + available providers for one title.

        Instant fast path: metadata paints immediately and the provider chain is
        probed in parallel with a strict ``_DETAIL_QUICK_WINDOW`` cap — the first
        provider to return episodes wins, honouring an explicit ``provider``
        pick. The accurate per-provider availability probe keeps running in the
        background and delivers the final result via a ``details-refreshed``
        DOM event so the details view re-renders in place. Every provider is
        reported as selectable (parity with the CLI chain); none are disabled.
        """
        anime_id = str(anime_id or "")
        if category == ARABIC_CATEGORY:
            return self._arabic_details(anime_id)

        cache_key = f"{anime_id}:{provider or 'auto'}"

        with self._lock:
            if cache_key in self._detail_cache:
                return self._detail_cache[cache_key]

        meta = self.get_anime_meta(anime_id)
        meta = dict(meta)  # copy: never mutate the lru_cached metadata object
        pm = self._pm()
        names = [n for n in pm.available_providers if pm._providers.get(n)]
        providers = [{"name": n, "available": True} for n in names]

        # Instant fast path: parallel probe, first provider with episodes wins.
        episodes, chosen = self._first_episodes(names, anime_id, category, provider)

        meta["providers"] = providers
        meta["category"] = category
        meta["selected_provider"] = chosen
        meta["episodes"] = episodes

        self._cache_put(self._detail_cache, cache_key, dict(meta), 64)

        # Background: accurate availability + best episode list, then event.
        self._kick_detail_refresh(anime_id, provider, category, cache_key)
        return meta

    def _first_episodes(self, names, anime_id, category, provider=None):
        """Fast parallel episode probe.

        Returns ``(episodes, chosen_provider)`` as soon as a provider yields
        episodes (honouring an explicit ``provider`` pick), bounded by
        ``_DETAIL_QUICK_WINDOW`` plus a short grace for the explicit provider.
        Never raises; returns ``([], None)`` when nothing resolved.
        """
        names = [n for n in names if n]
        if not names:
            return [], None

        def _probe(n):
            return self._episode_list(n, anime_id, category)

        ex = ThreadPoolExecutor(max_workers=min(len(names), 6))
        futs = {ex.submit(_probe, n): n for n in names}
        completed = {}
        winner = None
        try:
            for fut in as_completed(list(futs), timeout=_DETAIL_QUICK_WINDOW):
                n = futs[fut]
                try:
                    eps = fut.result(timeout=0)
                except Exception:
                    eps = []
                completed[n] = eps
                if eps and (n == provider or provider is None):
                    winner = (list(eps), n)
                    break
        except TimeoutError:
            pass

        if winner is None and provider and provider in futs and provider not in completed:
            try:
                eps = futs[provider].result(timeout=1.5)
            except Exception:
                eps = []
            if eps:
                completed[provider] = eps
                winner = (list(eps), provider)

        ex.shutdown(wait=False)

        if winner is not None:
            return winner
        for n in names:
            eps = completed.get(n) or []
            if eps:
                return list(eps), n
        return [], None

    def _kick_detail_refresh(self, anime_id, provider, category, cache_key) -> None:
        """Start the background details worker (single-shot daemon thread)."""

        def worker():
            try:
                self._refresh_detail(anime_id, provider, category, cache_key)
            except Exception:
                pass

        threading.Thread(target=worker, daemon=True).start()

    def _refresh_detail(self, anime_id, provider, category, cache_key) -> None:
        """Accurate per-provider details probe run off the UI thread.

        Mirrors the old blocking ``get_anime_details`` flow (parallel probe with
        the full chosen-provider allowance) but delivers the result through the
        cache + ``details-refreshed`` event instead of blocking the caller.
        """
        pm = self._pm()
        names = [n for n in pm.available_providers if pm._providers.get(n)]

        def _probe(n, cancel_event):
            return self._episode_list(n, anime_id, category, abort_event=cancel_event)

        results_map = self._parallel_probe_detail(names, _probe, chosen=provider)

        providers = []
        chosen = None
        for n in names:
            eps = results_map.get(n) or []
            providers.append({"name": n, "available": len(eps) > 0})
            if chosen is None and eps:
                chosen = n
        if provider and results_map.get(provider):
            chosen = provider

        episodes = results_map.get(chosen) or []
        if not isinstance(episodes, list):
            episodes = []

        meta = self.get_anime_meta(anime_id)
        meta = dict(meta)
        meta["providers"] = providers
        meta["category"] = category
        meta["selected_provider"] = chosen
        meta["episodes"] = episodes
        self._cache_put(self._detail_cache, cache_key, dict(meta), 64)
        self._dispatch_event("details-refreshed", {
            "anime_id": str(anime_id),
            "category": category,
            **meta,
        })

    def _arabic_details(self, anime_id: str) -> Dict[str, Any]:
        """Details payload for the AR Sub track: metadata + Arabic-API episodes.

        The Arabic track exposes a single ``arabic_api`` provider (the Arabic
        API pipeline), so no English scraper probing happens here."""
        anime_id = str(anime_id or "")
        cache_key = f"{anime_id}:{ARABIC_PROVIDER}:{ARABIC_CATEGORY}"
        with self._lock:
            if cache_key in self._detail_cache:
                return self._detail_cache[cache_key]
        meta = self.get_anime_meta(anime_id)
        meta = dict(meta)  # copy: never mutate the lru_cached metadata object
        eps = self._arabic_episodes(anime_id)
        meta["providers"] = [{"name": ARABIC_PROVIDER, "available": len(eps) > 0}]
        meta["category"] = ARABIC_CATEGORY
        meta["selected_provider"] = ARABIC_PROVIDER if eps else None
        meta["episodes"] = eps
        self._cache_put(self._detail_cache, cache_key, dict(meta), 64)
        return meta

    def _parallel_probe_detail(self, names, fn, chosen=None):
        """Probe providers in parallel. Every future is bounded by
        ``_PROVIDER_TIMEOUT``; the chosen provider (if any) gets the full
        ``_CHOSEN_PROVIDER_TIMEOUT`` allowance for slow browser-backed scrapers.

        ``fn`` is called as ``fn(provider_name, cancel_event)`` so each probe
        can abort its (still queued) browser jobs the moment the chosen
        provider wins or the probe is finished — leftover background jobs must
        never keep occupying the shared browser worker.
        """
        names = [n for n in names if n]
        if not names:
            return {}
        # All providers probe in parallel under a strict quick window
        # (≈3.5s wall clock). The chosen provider (explicit or the first in
        # priority order) may keep running for the longer browser-backed
        # allowance — the UI is waiting on its full episode list anyway.
        default_chosen = chosen or names[0]
        abort_event = threading.Event()
        ex = ThreadPoolExecutor(max_workers=min(len(names), 6))
        outcomes: Dict[str, Any] = {}
        try:
            futs = {ex.submit(fn, n, abort_event): n for n in names}
            done, pending = wait(list(futs), timeout=_PROVIDER_TIMEOUT)
            for fut in done:
                n = futs[fut]
                try:
                    outcomes[n] = fut.result(timeout=0)
                except Exception:
                    outcomes[n] = []

            chosen_fut = next(
                (f for f, n in futs.items() if n == default_chosen), None
            )
            if chosen_fut in pending:
                try:
                    outcomes[default_chosen] = chosen_fut.result(
                        timeout=_CHOSEN_PROVIDER_TIMEOUT
                    )
                except Exception:
                    outcomes[default_chosen] = []

            # The chosen probe is done (or gave up): stop every other
            # still-running probe from submitting more browser work.
            abort_event.set()
            for n in names:
                if n not in outcomes:
                    outcomes[n] = []
        finally:
            ex.shutdown(wait=False, cancel_futures=True)
        return outcomes

    def _provider_anime_id(self, name: str, anime_id: str, category: str = "sub") -> Optional[str]:
        """Resolve the provider-specific anime id/slug for an AniList id.

        miruro is the only scraper whose ids *are* AniList ids, so it passes
        the id straight through. Every other provider needs a CLI-style title
        search first: ``provider.search(title)`` → pick the best hit → use its
        ``id`` for ``get_episodes()``/stream resolution. The resolved id is
        cached per (provider, AniList id) so subsequent episode clicks never
        re-search. Returns ``None`` (never raises) when no match exists.
        """
        if not name:
            return None
        name = str(name).lower()
        if name == "miruro":
            return str(anime_id or "")
        anime_id = str(anime_id or "")
        if not anime_id:
            return None
        cache_key = f"{name}:{anime_id}"
        with self._lock:
            if cache_key in self._provider_id_cache:
                return self._provider_id_cache.get(cache_key) or None
        resolved = None
        try:
            scraper = self._pm()._providers.get(name)
            if scraper is not None:
                meta = self._anilist_details(anime_id) or {}
                candidates = [
                    t for t in
                    (meta.get("title"), meta.get("romaji"), meta.get("native"))
                    if t
                ]
                shortlist = {}          # hid -> (score, pos, hit)
                for title in candidates:
                    try:
                        hits = scraper.search(title) or []
                    except Exception:
                        hits = []
                    for pos, h in enumerate(hits):
                        hid = str(h.get("id") or "")
                        if not hid:
                            continue
                        score = _hit_score(h.get("title"), title)
                        prev = shortlist.get(hid)
                        if prev is None:
                            shortlist[hid] = (score, pos, h)
                        else:
                            # Keep the best of title-score vs highest relevance
                            # position, so a main series with an odd title (e.g.
                            # AllAnime "1P" for One Piece) isn't lost.
                            if score > prev[0]:
                                shortlist[hid] = (score, prev[1], h)
                # Relevance floor: the first few results a provider returns are
                # strongly ordered by relevance. Give early positions a floor so
                # a 0.0-title-score main series (e.g. AllAnime "1P") still
                # competes on its episode count.
                floored = {}
                for hid, (score, pos, h) in shortlist.items():
                    if pos == 0:
                        score = max(score, 0.50)
                    elif pos == 1:
                        score = max(score, 0.42)
                    elif pos <= 3:
                        score = max(score, 0.30)
                    floored[hid] = (score, pos, h)
                # Re-rank shortlisted hits by their real episode count vs
                # AniList so a 1-ep movie/OVA never shadows a long-running TV
                # series. The candidate pool is the union of the strongest
                # title matches and the earliest search positions: a main
                # series can arrive either way (AllAnime "1P" for One Piece
                # only ranks first by relevance; AOT's exact-match series sits
                # near the end of search results). Counts are fetched
                # position-order-first — the upstream rate-limits long bursts,
                # so the pos-0 main series must be checked while responsive.
                by_pos = sorted(floored.values(), key=lambda t: t[1])
                title_top = sorted(floored.values(), key=lambda t: (t[0], -t[1]), reverse=True)
                pool_entries = []
                seen_pool = set()
                for cand in by_pos[:4] + title_top[:4]:
                    hid = cand[2].get("id")
                    if hid in seen_pool:
                        continue
                    seen_pool.add(hid)
                    pool_entries.append(cand)
                    if len(pool_entries) >= 6:
                        break
                pool_entries = sorted(pool_entries, key=lambda t: (t[1], -t[0]))
                # Re-sort fetch order: earliest positions first, then strongest
                # title matches, so pos-0 (the likeliest main series) is probed
                # before the host is exhausted.
                order = sorted(pool_entries, key=lambda t: (t[1] if t[1] <= 3 else 99, -t[0]))
                best_hit = None
                best_score = float("-inf")
                for score, pos, hit in order:
                    eps_len = self._provider_hit_episodes(name, hit, category)
                    if eps_len is not None:
                        score = _rank_hit_score(score, eps_len, meta)
                    if score > best_score:
                        best_score = score
                        best_hit = hit
                if best_hit and best_hit.get("id"):
                    resolved = str(best_hit["id"])
        except Exception:
            resolved = None
        with self._lock:
            self._cache_put(self._provider_id_cache, cache_key, resolved, 256)
        return resolved

    def _provider_hit_episodes(self, name, hit, category) -> Optional[int]:
        """Best-effort episode count for one candidate search hit (cached).

        Used to re-rank shortlisted hits by their real episode count so a
        1-episode movie/OVA doesn't get picked over the main TV series. The raw
        episode list is also stored so ``_episode_list`` can reuse it."""
        hid = str((hit or {}).get("id") or "")
        if not hid:
            return None
        cache_key = f"{name}:{hid}:{category}"
        with self._lock:
            if cache_key in self._hit_eps_cache:
                return self._hit_eps_cache.get(cache_key)
        raw_key = f"{name}::{hid}::{category}"
        with self._lock:
            raw = self._raw_eps_cache.get(raw_key)
        scraper = self._pm()._providers.get(name)
        count = None
        if raw is not None:
            count = len(raw)
        elif scraper is not None:
            try:
                if hasattr(scraper, "preferred_category"):
                    scraper.preferred_category = category
                eps = scraper.get_episodes(hid) or []
            except Exception:
                eps = []
            count = len(eps)
            if count == 0:
                # Empty lists are usually a transient upstream rate-limit blank,
                # not a truthful count — don't cache, so a retry gets a chance.
                count = None
                return None
            self._cache_put(self._raw_eps_cache, raw_key, list(eps), 128)
        with self._lock:
            self._cache_put(self._hit_eps_cache, cache_key, count, 128)
        return count

    def _episode_list(self, name, anime_id, category, abort_event=None):
        """Fetch + normalize the episode list for one provider, LRU-cached.

        Non-miruro providers resolve their id first via a CLI-style title
        search (miruro already speaks AniList ids), so the AniList integer is
        never handed directly to their ``get_episodes()``.
        """
        if not name:
            return []
        scraper = self._pm()._providers.get(name)
        if scraper is None:
            return []
        ep_key = f"{name}:{anime_id}:{category}"
        with self._lock:
            if ep_key in self._ep_cache:
                return self._ep_cache[ep_key]
        provider_anime_id = self._provider_anime_id(name, anime_id, category)
        if not provider_anime_id:
            return []
        raw_key = f"{name}::{provider_anime_id}::{category}"
        with self._lock:
            items = self._raw_eps_cache.get(raw_key)
        if items is None:
            if abort_event is not None and abort_event.is_set():
                return []
            try:
                if hasattr(scraper, "preferred_category"):
                    scraper.preferred_category = category
                items = _call_maybe_cancel(
                    scraper, "get_episodes", provider_anime_id,
                    abort_event=abort_event,
                ) or []
            except Exception:
                items = []
            if not items:
                # empty = transient upstream blank; don't poison the cache
                return []
            self._cache_put(self._raw_eps_cache, raw_key, list(items), 128)
        eps = []
        seen = set()
        for ep in items:
            ep_id = str(ep.get("id") or "")
            try:
                num = float(ep.get("episode_num"))
            except (TypeError, ValueError):
                continue
            key = (name, ep_id)
            if key in seen:
                continue
            seen.add(key)
            eps.append({"episode_num": num, "id": ep_id, "provider": name})
        eps.sort(key=lambda e: e["episode_num"])
        self._cache_put(self._ep_cache, ep_key, eps, 128)
        return eps

    # ------------------------------------------------------------------
    # Arabic Subtitle (AR Sub) pipeline
    #
    # The Arabic track is a separate pipeline backed by the Arabic API
    # (AnimeAPI), exactly like the CLI's "Arabic Sub" flow: search -> episodes
    # -> streaming servers -> MediaFire direct link. It never mixes with the
    # English scraper chain.
    # ------------------------------------------------------------------
    def _arabic_anime(self, anime_id: str) -> Optional[Dict]:
        """Resolve the Arabic-API anime (AnimeId + type) for an AniList id via
        a CLI-style title search. Cached; returns ``None`` when untitled/not
        found."""
        anime_id = str(anime_id or "")
        if not anime_id:
            return None
        cache_key = f"arabic:{anime_id}"
        with self._lock:
            if cache_key in self._arabic_anime_cache:
                return self._arabic_anime_cache.get(cache_key) or None
        resolved = None
        try:
            from .api import AnimeAPI
            title = self._anime_title(anime_id)
            if title:
                picked = _pick_arabic_hit((AnimeAPI().search_anime(title) or []), title)
                if picked is not None and getattr(picked, "id", ""):
                    resolved = {
                        "aid": str(picked.id),
                        "type": getattr(picked, "type", "") or "SERIES",
                    }
        except Exception:
            resolved = None
        with self._lock:
            self._cache_put(self._arabic_anime_cache, cache_key, resolved, 128)
        return resolved

    def _arabic_episodes(self, anime_id: str) -> List[Dict]:
        """AR Sub episode list from the Arabic API, LRU-cached.

        Returns ``[{"episode_num": float, "id": "{aid}|{number}|{type}",
        "provider": "arabic_api"}, ...]`` so playback can resolve the exact
        server/episode without a second title search."""
        anime_id = str(anime_id or "")
        if not anime_id:
            return []
        ep_key = f"{ARABIC_PROVIDER}:{anime_id}:{ARABIC_CATEGORY}"
        with self._lock:
            if ep_key in self._ep_cache:
                return self._ep_cache[ep_key]
        anime = self._arabic_anime(anime_id)
        if not anime:
            return []
        eps = []
        try:
            from .api import AnimeAPI
            raw = AnimeAPI().get_episodes(anime["aid"]) or []
            for ep in raw:
                try:
                    num = float(ep.display_num)
                except (TypeError, ValueError):
                    continue
                eps.append({
                    "episode_num": num,
                    "id": f'{anime["aid"]}|{ep.number}|{anime["type"]}',
                    "provider": ARABIC_PROVIDER,
                })
        except Exception:
            eps = []
        if not eps:
            # empty = transient upstream blank; don't poison the cache
            return []
        eps.sort(key=lambda e: e["episode_num"])
        self._cache_put(self._ep_cache, ep_key, eps, 128)
        return eps

    def _arabic_quality_key(self, resolution: str = "auto") -> str:
        """Map a resolution (or the user's default_quality setting) to an
        Arabic server key."""
        resolution = (resolution or "auto").strip().lower()
        if resolution in ("auto", "", "best", "highest"):
            try:
                from .settings import SettingsManager
                resolution = (SettingsManager().get("default_quality", "1080p") or "1080p").strip().lower()
            except Exception:
                resolution = "1080p"
        return _ARABIC_QUALITY_KEYS.get(resolution, "FRLink")

    @staticmethod
    def _extract_subtitle_tracks(server_data) -> List[str]:
        """Collect subtitle-track URLs from an Arabic server payload.

        The Arabic streams are normally hardsubbed (subtitles baked into the
        MediaFire mp4), but if the API ever returns external track URLs
        (``.srt``/``.vtt``/``.ass``/``.ssa``) they are passed to the player."""
        tracks: List[str] = []
        seen = set()

        def _walk(node):
            if node is None:
                return
            if isinstance(node, str):
                if node.startswith("http") and _SUBTITLE_EXT_RE.search(node):
                    if node not in seen:
                        seen.add(node)
                        tracks.append(node)
            elif isinstance(node, dict):
                for v in node.values():
                    _walk(v)
            elif isinstance(node, (list, tuple)):
                for v in node:
                    _walk(v)

        try:
            _walk(server_data)
        except Exception:
            tracks = []
        return tracks

    def _resolve_arabic_stream(self, anime_id: str, ep_num, resolution: str = "auto") -> Optional[Dict]:
        """Resolve an AR Sub stream through the Arabic API (CLI pipeline).

        Mirrors ``watch_together._resolve_arabic``/``cli.play_video``:
        ``get_streaming_servers`` -> pick quality server -> ``build_mediafire_url``
        -> ``extract_mediafire_direct``. Returns a stream dict carrying any
        detected external subtitle tracks. Never raises."""
        anime_id = str(anime_id or "")
        anime = self._arabic_anime(anime_id)
        if not anime:
            return None
        try:
            from .api import AnimeAPI
            api = AnimeAPI()
            target = str(int(float(ep_num)))
            # Prefer the Arabic API's exact episode number from the cached list.
            number = target
            for ep in self._arabic_episodes(anime_id):
                if str(int(float(ep["episode_num"]))) == target:
                    number = ep["id"].split("|")[1]
                    break
            ctx = {
                "anime": self._anime_title(anime_id) or "Anime",
                "episode": target,
                "provider": ARABIC_PROVIDER,
            }
            server_data = api.get_streaming_servers(
                anime["aid"], number, anime["type"], ctx
            )
            if not server_data:
                return None
            current_ep = server_data.get("CurrentEpisode") or {}
            server_key = self._arabic_quality_key(resolution)
            server_id = current_ep.get(server_key) or current_ep.get("FRLink")
            if not server_id:
                return None
            mf_url = api.build_mediafire_url(server_id)
            direct = api.extract_mediafire_direct(mf_url, ctx)
            if not direct or not str(direct).startswith(("http://", "https://")):
                return None
            return {
                "stream_url": str(direct),
                "headers": {},
                "subtitles": self._extract_subtitle_tracks(server_data),
                "provider": ARABIC_PROVIDER,
            }
        except Exception as exc:
            self._log_gui_resolve_error(ARABIC_PROVIDER, ep_num, exc, None,
                                        note="arabic resolve raised")
            return None

    def get_episodes(
        self,
        anime_id: str,
        provider: Optional[str] = None,
        category: str = "sub",
    ) -> List[Dict]:
        """Return episode list for an anime id: ``[{"episode_num": int,
        "id": str, "provider": str}, ...]``. Single-provider and LRU-cached."""
        anime_id = str(anime_id or "")
        if not anime_id:
            return []

        if category == ARABIC_CATEGORY:
            return list(self._arabic_episodes(anime_id))

        pm = self._pm()
        if provider and provider in pm._providers:
            return list(self._episode_list(provider, anime_id, category))

        # No provider requested: prefer the first cached list, else probe in
        # parallel and keep the first provider that has episodes.
        cache_pick = None
        with self._lock:
            for key, eps in self._ep_cache.items():
                if key.endswith(f":{anime_id}:{category}") and eps:
                    cache_pick = list(eps)
                    break
        if cache_pick:
            return cache_pick

        names = [n for n in pm.available_providers if pm._providers.get(n)]
        results_map = self._parallel_probe_detail(
            names,
            lambda n, cancel_event: self._episode_list(
                n, anime_id, category, abort_event=cancel_event
            ),
        )
        for name in names:
            eps = results_map.get(name) or []
            if eps:
                return list(eps)
        return []

    # ------------------------------------------------------------------
    # playback
    # ------------------------------------------------------------------
    def play_episode(
        self,
        anime_id: str,
        ep_num,
        player_choice: str = "mpv",
        provider: Optional[str] = None,
        category: str = "sub",
        resolution: str = "auto",
    ) -> Dict:
        """Resolve the best stream for (anime_id, ep) and launch the selected
        player. Returns ``{"ok": bool, "player": str, "url": str|None,
        "error": str|None}``.

        ``provider`` selects a specific source and ``category`` the sub/dub
        track, matching the server pill the user picked in the details view.
        ``resolution`` (``"auto"``/``"1080p"``/``"720p"``/``"480p"``) selects
        the stream quality: the Arabic track switches the quality server, the
        English HLS track is pre-filtered to the best matching rendition.

        When a Watch Together room is active the host player is bound to the
        room's IPC socket / rc port so playback syncs (guests join automatically
        from the host broadcast). Guests cannot start playback on their own.
        """
        anime_id = str(anime_id or "")
        title = ""
        guest = self._watch_guest
        host = self._watch_host
        if guest is not None and getattr(guest, "is_active", False):
            return {"ok": False, "error": "You are a guest — the host controls playback in the room."}
        try:
            episodes = self.get_episodes(anime_id, provider, category)
        except Exception:
            episodes = []
        if not episodes:
            return {"ok": False, "error": "No episodes found for this anime."}
        try:
            ep_num = float(ep_num)
        except (TypeError, ValueError):
            ep_num = float(episodes[0]["episode_num"])
        ep = next(
            (e for e in episodes if float(e["episode_num"]) == ep_num),
            episodes[0],
        )
        ep_id = ep["id"]
        provider = provider or ep.get("provider")
        meta = self._anilist_details(anime_id) or {}
        title = meta.get("title") or self._anime_title(anime_id)

        try:
            stream = self._resolve_stream(anime_id, ep_num, provider, category, resolution)
        except Exception as exc:
            return {"ok": False, "error": f"Stream resolution failed: {exc}"}

        url = (stream or {}).get("stream_url")
        if not url:
            return {"ok": False, "error": "No playable stream URL was found."}

        headers = (stream or {}).get("headers") or {}
        subtitles = (stream or {}).get("subtitles") or []
        player_choice = (player_choice or "mpv").lower()

        # Watch Together: bind the launched player to the room's transport and
        # broadcast the load so guests auto-join. The host player kind always
        # wins over the dropdown so the sync transport matches.
        ipc_socket = None
        rc_port = None
        if host is not None and getattr(host, "is_active", False):
            player_choice = getattr(host, "player_kind", "mpv") or "mpv"
            if player_choice == "vlc":
                rc_port = host.rc_port
            else:
                ipc_socket = host.socket_path
            try:
                host.notify_load(
                    title, int(ep_num),
                    self._watch_language_label(category),
                    url=url, headers=headers,
                )
            except Exception:
                pass

        # Player preferences from settings: custom aspect-ratio override and the
        # app's custom keyboard hotkeys (enforced on the native mpv window).
        aspect = None
        custom_hotkeys = False
        try:
            from .settings import SettingsManager
            _settings = SettingsManager()
            aspect = str(_settings.get("mpv_aspect_ratio", "auto") or "auto")
            custom_hotkeys = bool(_settings.get("mpv_custom_keys", True))
        except Exception:
            pass

        # Continue Watching: sample mpv time-pos/duration via IPC during playback
        # and persist progress after the player exits (best-effort).
        progress_holder = {"pos": None, "dur": None}

        def _progress_cb(pos, dur):
            progress_holder["pos"] = pos
            progress_holder["dur"] = dur

        try:
            self._player_mgr().play_with_quality_fallback(
                url,
                title=f"{title} - Ep {int(ep_num)}",
                player_type=player_choice,
                headers=headers,
                subtitles=subtitles,
                ipc_socket=ipc_socket,
                rc_port=rc_port,
                aspect=aspect,
                custom_hotkeys=custom_hotkeys,
                progress_cb=_progress_cb,
                resolution=resolution,
            )
        except Exception as exc:
            if host is not None and getattr(host, "is_active", False):
                try:
                    host.notify_stop()
                except Exception:
                    pass
            return {"ok": False, "error": f"Failed to launch {player_choice}: {exc}"}

        # Persist playback progress for the Continue Watching row.
        try:
            poster = (meta or {}).get("poster") or ""
            self._history_mgr().record_progress(
                anime_id,
                int(ep_num),
                title,
                poster=poster,
                position=progress_holder.get("pos"),
                total=progress_holder.get("dur"),
            )
        except Exception:
            pass

        if host is not None and getattr(host, "is_active", False):
            try:
                host.notify_stop()
            except Exception:
                pass
        return {"ok": True, "player": player_choice, "url": url}

    def get_continue_watching(self, limit: int = 12) -> List[Dict]:
        """Return continue-watching items (from local playback history),
        newest-first. Each entry carries anime_id, title, episode, poster and
        progress (0..1) for the warm-orange progress bars."""
        try:
            return self._history_mgr().get_continue_watching(limit=int(limit or 12))
        except Exception:
            return []

    def record_progress(self, anime_id: str, episode_num, title: str = "", poster: str = "", position=None, total=None) -> bool:
        """Persist playback progress for one title (Continue Watching)."""
        try:
            self._history_mgr().record_progress(
                str(anime_id or ""), episode_num, title, poster=poster,
                position=position, total=total,
            )
            return True
        except Exception:
            return False

    # ------------------------------------------------------------------
    # Bookmark / My List bridges
    # ------------------------------------------------------------------
    def toggle_bookmark(self, anime_id: str, title: str = "", poster: str = "", year=None) -> bool:
        """Add or remove a title from My List. Returns the new state."""
        try:
            return self._history_mgr().toggle_bookmark(
                str(anime_id or ""), title=title, poster=poster, year=year
            )
        except Exception:
            return False

    def is_bookmarked(self, anime_id: str) -> bool:
        try:
            return self._history_mgr().is_bookmarked(str(anime_id or ""))
        except Exception:
            return False

    def get_bookmarks(self, limit: int = 50) -> List[Dict]:
        """My List items newest-first: anime_id, title, poster, year, added."""
        try:
            return self._history_mgr().get_bookmarks(limit=int(limit or 50))
        except Exception:
            return []

    # ------------------------------------------------------------------
    # Frontend-facing settings (pre-roll ad buffer, playback prefs)
    # ------------------------------------------------------------------
    def get_settings(self) -> Dict:
        """Return the app settings the UI needs (pre-roll ad slot, playback)."""
        try:
            from .settings import SettingsManager
            s = SettingsManager()
            return {
                "preroll_enabled": bool(s.get("preroll_enabled", False)),
                "preroll_video_url": str(s.get("preroll_video_url", "") or ""),
                "preroll_seconds": int(s.get("preroll_seconds", 5) or 5),
                "default_quality": str(s.get("default_quality", "1080p") or "1080p"),
                "mpv_aspect_ratio": str(s.get("mpv_aspect_ratio", "auto") or "auto"),
                "mpv_custom_keys": bool(s.get("mpv_custom_keys", True)),
            }
        except Exception:
            return {
                "preroll_enabled": False,
                "preroll_video_url": "",
                "preroll_seconds": 5,
                "default_quality": "1080p",
                "mpv_aspect_ratio": "auto",
                "mpv_custom_keys": True,
            }

    @staticmethod
    def _watch_language_label(category) -> str:
        if category == ARABIC_CATEGORY:
            return "Arabic Sub"
        if category == "dub":
            return "English Dub"
        return "English Sub"

    def _anime_title(self, anime_id: str) -> str:
        meta = self._anilist_details(anime_id) or {}
        return meta.get("title") or meta.get("romaji") or "Anime"

    def _resolve_stream(
        self,
        anime_id: str,
        ep_num,
        provider: Optional[str],
        category: str = "sub",
        resolution: str = "auto",
    ):
        """Resolve a stream dict for a given episode via the provider chain.

        ``resolution`` (``"auto"``/``"1080p"``/``"720p"``/``"480p"``) selects
        the quality: the Arabic track picks the matching quality server here;
        the English HLS track is filtered at player launch instead.

        For a specific provider the episode list is fetched through the
        CLI-style id mapping (title search for non-miruro scrapers), so the
        AniList integer is never passed to another provider's resolver. The
        chosen provider is resolved **directly and alone** — no concurrent full
        chain runs in the background (which previously launched Playwright for
        every unrelated provider on each click). The auto chain is only used as
        a strictly sequential last resort when the chosen provider produced no
        usable stream, or directly when the user asked for ``auto``. Each stage
        is bounded by ``_CHOSEN_PROVIDER_TIMEOUT``. Never raises.
        """
        pm = self._pm()
        anime_id = str(anime_id or "")

        # AR Sub is a separate pipeline backed by the Arabic API — it never
        # mixes with (or falls back to) the English scraper chain.
        if category == ARABIC_CATEGORY:
            return self._resolve_arabic_stream(anime_id, ep_num, resolution)

        def _chosen(abort_event):
            """Episode-list + get_stream_url for the explicit provider."""
            if not provider:
                return None
            try:
                scraper = pm._providers.get(provider)
                if scraper is None:
                    return None
                if abort_event is not None and abort_event.is_set():
                    return None
                if hasattr(scraper, "preferred_category"):
                    scraper.preferred_category = category
                eps = self._episode_list(provider, anime_id, category,
                                         abort_event=abort_event)
                if not eps:
                    self._log_gui_resolve_error(
                        provider, ep_num, None, {"episodes": []},
                        note="no episode list (search/CF blocked)",
                    )
                    return None
                target = str(int(float(ep_num)))
                for ep in eps or []:
                    if str(int(float(ep["episode_num"]))) == target:
                        if abort_event is not None and abort_event.is_set():
                            return None
                        try:
                            result = _call_maybe_cancel(
                                scraper, "get_stream_url", ep["id"],
                                abort_event=abort_event,
                            )
                        except Exception as exc:
                            self._log_gui_resolve_error(
                                provider, ep_num, exc, None,
                                note="get_stream_url raised",
                            )
                            return None
                        if self._is_usable_stream(result):
                            return result
                        self._log_gui_resolve_error(
                            provider, ep_num, None, result,
                            note="unusable stream (None/garbage URL)",
                        )
                        return None
                return None
            except Exception as exc:
                self._log_gui_resolve_error(provider, ep_num, exc, None,
                                            note="episode-list/resolver error")
                return None

        def _auto(abort_event):
            """Global fallback through the manager chain (CLI-style title search)."""
            import asyncio
            try:
                url, headers, name = asyncio.run(
                    pm.resolve_stream(
                        self._anime_title(anime_id) or "Anime",
                        ep_num,
                        mode=category,
                        language="english",
                        provider="auto",
                        quiet=True,
                        abort_event=abort_event,
                    )
                )
                if self._is_usable_stream({"stream_url": url, "headers": headers}):
                    return {"stream_url": url, "headers": headers}
                self._log_gui_resolve_error(
                    "auto", ep_num, None, {"stream_url": url, "headers": headers, "via": name},
                    note="auto chain produced unusable stream",
                )
            except Exception as exc:
                self._log_gui_resolve_error("auto", ep_num, exc, None,
                                            note="auto chain raised")
            return None

        def _run_bounded(fn, label):
            """Run ``fn(abort_event)`` on one worker, bounded by
            _CHOSEN_PROVIDER_TIMEOUT.

            An abort event is set the moment the stage finishes OR times out,
            so any leftover work (the abandoned auto chain, a stuck browser
            job) stops submitting new jobs to the shared browser worker.
            """
            from concurrent.futures import ThreadPoolExecutor, TimeoutError as _FutTimeout
            abort_event = threading.Event()
            ex = ThreadPoolExecutor(max_workers=1)
            try:
                fut = ex.submit(fn, abort_event)
                try:
                    with timed(label):
                        return fut.result(timeout=_CHOSEN_PROVIDER_TIMEOUT)
                except _FutTimeout:
                    sys.stderr.write(f"[TIMING] {label} exceeded "
                                     f"{_CHOSEN_PROVIDER_TIMEOUT}s — continuing\n")
                    return None
            finally:
                abort_event.set()
                ex.shutdown(wait=False)

        # Direct execution: a specific provider is resolved ALONE (no concurrent
        # full-chain fallback that would otherwise launch Playwright for every
        # unrelated provider in the background). The auto chain only runs as a
        # strictly sequential last resort once the chosen provider failed, or
        # directly when the user asked for auto.
        if provider:
            chosen = _run_bounded(_chosen, "gui:chosen")
            if self._is_usable_stream(chosen):
                return chosen
            fallback = _run_bounded(_auto, "gui:auto:fallback")
            if self._is_usable_stream(fallback):
                return fallback
            return None

        return _run_bounded(_auto, "gui:auto")

    @staticmethod
    def _is_usable_stream(result) -> bool:
        """A stream dict is usable only when it carries a real media URL."""
        url = (result or {}).get("stream_url")
        if not url or not isinstance(url, str):
            return False
        try:
            from .scrapers.embeds import _is_media_url
            return _is_media_url(url)
        except Exception:
            return True

    @staticmethod
    def _log_gui_resolve_error(provider, ep_num, exc, result, note: str = "") -> None:
        """Emit a structured [GUI RESOLVE ERROR] line for the resolve pipeline."""
        exc_txt = f"{type(exc).__name__}: {exc}" if exc is not None else "none"
        payload = json.dumps(result, default=str)[:400] if result is not None else "none"
        sys.stderr.write(
            f"[GUI RESOLVE ERROR] provider={provider or 'auto'} "
            f"episode={ep_num} note={note} exception={exc_txt} "
            f"returned={payload}\n"
        )

    # ------------------------------------------------------------------
    # Watch Together
    # ------------------------------------------------------------------
    def host_room(self, player_choice: str = "mpv") -> Dict:
        """Open a Watch Together lobby as host. Returns room code + status."""
        try:
            from . import watch_together
            host = watch_together.WatchHost(player_kind=player_choice)
            if not host.start():
                return {"ok": False, "error": "Could not connect to Supabase Realtime."}
            self._watch_host = host
            return {"ok": True, "code": host.code, "role": "host"}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    def join_room(self, code: str, player_choice: str = "mpv") -> Dict:
        """Join a Watch Together room as guest. Returns status."""
        code = str(code or "").strip()
        if len(code) != 6 or not code.isdigit():
            return {"ok": False, "error": "Room codes are 6 digits."}
        try:
            from . import watch_together
            guest = watch_together.WatchGuest(code, player_kind=player_choice)
            if not guest.start():
                return {"ok": False, "error": "Could not connect to the room."}
            self._watch_guest = guest
            return {"ok": True, "code": code, "role": "guest"}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    def leave_room(self) -> Dict:
        """Leave a Watch Together room (host or guest) and clean up."""
        result = {"ok": True}
        if self._watch_host is not None:
            try:
                self._watch_host.stop()
            except Exception as exc:
                result = {"ok": False, "error": str(exc)}
            self._watch_host = None
        if self._watch_guest is not None:
            try:
                self._watch_guest.stop()
            except Exception as exc:
                result = {"ok": False, "error": str(exc)}
            self._watch_guest = None
        return result

    def watch_status(self) -> Dict:
        """Return current Watch Together session state for the status bar."""
        if self._watch_host is not None:
            return {"active": True, "role": "host", "code": self._watch_host.code}
        if self._watch_guest is not None:
            return {"active": True, "role": "guest", "code": self._watch_guest.code}
        return {"active": False, "role": None, "code": None}

    def room_members(self) -> List[Dict]:
        """Return the current Watch Together roster (``[{"name", "role"}...]``)
        so the status bar can show who is in the room."""
        if self._watch_host is not None:
            return [
                {"name": name, "role": role}
                for name, role in self._watch_host.members.items()
            ]
        if self._watch_guest is not None:
            return [
                {"name": name, "role": role}
                for name, role in self._watch_guest.members.items()
            ]
        return []


def _index_html_path() -> str:
    """Return the filesystem path to the bundled index.html.

    Falls back to the checked-in path inside the source tree when installed
    from a wheel (pywebview ``file://`` needs a real path)."""
    if _INDEX_HTML.exists():
        return str(_INDEX_HTML)
    # Try common install locations relative to the package.
    for cand in (
        Path(__file__).parent / "ui" / "index.html",
        Path(sys.prefix) / "ani_cli_arabic" / "ui" / "index.html",
    ):
        if cand.exists():
            return str(cand)
    raise FileNotFoundError(
        "Unable to locate ani_cli_arabic/ui/index.html; reinstall the package."
    )


def run_gui(debug: bool = False) -> None:
    """Launch the desktop GUI window (blocking)."""
    if not _HAS_WEBVIEW:
        sys.stderr.write(
            "[!] pywebview is required for the GUI. Install with: "
            "pip install pywebview\n"
        )
        return
    api = JSApi()
    window = webview.create_window(
        f"AniNova AR {APP_VERSION}",
        _index_html_path(),
        js_api=api,
        width=1100,
        height=760,
        min_size=(760, 540),
        text_select=True,
    )
    try:
        webview.start(debug=debug, gui=None)
    except Exception as exc:  # pragma: no cover - GUI backend failures
        sys.stderr.write(f"[!] Failed to start GUI: {exc}\n")
    finally:
        try:
            api.leave_room()
        except Exception:
            pass


def main() -> None:
    """Entry point for ``python -m ani_cli_arabic.gui``."""
    import argparse
    parser = argparse.ArgumentParser(prog="ani-cli-arabic-gui")
    parser.add_argument("--debug", action="store_true", help="Enable webview debug/devtools")
    args = parser.parse_args()
    run_gui(debug=args.debug)


if __name__ == "__main__":
    main()
