import base64
import gzip
import json
import sys
import threading
import time
from typing import Dict, List, Optional

import httpx

from .base import BaseScraper

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)

ANILIST_URL = "https://graphql.anilist.co"
MIRURO_BASE = "https://www.miruro.tv"
MIRURO_PIPE = f"{MIRURO_BASE}/api/secure/pipe"

_SEARCH_QUERY = """\
query ($page: Int, $perPage: Int, $search: String) {
  Page(page: $page, perPage: $perPage) {
    media(search: $search, type: ANIME, sort: [SEARCH_MATCH, POPULARITY_DESC]) {
      id
      title { romaji english native }
    }
  }
}"""

_MAX_RETRIES = 3
_RETRY_DELAY = 2.0
_REQUEST_INTERVAL = 1.0

_RETRYABLE_STATUS = {429, 500, 502, 503, 504}

_BLOCKED_RESOURCE_TYPES = {
    "image", "font", "stylesheet", "media", "ping",
}

_TRACKING_FRAGMENTS = (
    "googletagmanager",
    "google-analytics",
    "googleadservices",
    "doubleclick",
    "facebook",
    "fbcdn",
    "hotjar",
    "mixpanel",
    "clarity.ms",
    "quantcast",
)

_SEARCH_CLIENT = httpx.Client(
    headers={"Content-Type": "application/json", "User-Agent": USER_AGENT},
    timeout=10.0,
)


def _block_heavy_resource(route):
    rt = route.request.resource_type
    u = route.request.url
    if rt in _BLOCKED_RESOURCE_TYPES:
        route.abort()
    elif any(t in u for t in _TRACKING_FRAGMENTS):
        route.abort()
    else:
        route.continue_()


def _encode_pipe(payload: dict) -> str:
    return base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip("=")


def _search_score(query: str, title: str) -> float:
    q = (query or "").lower().strip()
    t = (title or "").lower().strip()
    if not q or not t:
        return 0.0
    if t == q:
        return 1.0
    if q in t or t in q:
        return 0.9
    q_words = set(q.split())
    t_words = set(t.split())
    if not q_words:
        return 0.0
    overlap = len(q_words & t_words)
    if not overlap:
        return 0.0
    return overlap / len(q_words)


def _decode_pipe(raw: str) -> dict:
    raw += "=" * (4 - len(raw) % 4)
    compressed = base64.urlsafe_b64decode(raw)
    return json.loads(gzip.decompress(compressed).decode("utf-8"))


class MiruroScraper(BaseScraper):

    _last_request_time = 0.0
    _rate_limit_lock = threading.Lock()

    _PROVIDER_PRIORITY = ["pewe", "kiwi", "bee", "bonk", "ally", "moo", "hop"]

    preferred_category = "sub"

    @property
    def name(self) -> str:
        return "miruro"

    @classmethod
    def _respect_rate_limit(cls):
        with cls._rate_limit_lock:
            elapsed = time.time() - cls._last_request_time
            if elapsed < _REQUEST_INTERVAL:
                time.sleep(_REQUEST_INTERVAL - elapsed)
            cls._last_request_time = time.time()

    def _pipe_fetch(self, payload: dict) -> Optional[dict]:
        from playwright.sync_api import sync_playwright

        last_error = None
        for attempt in range(_MAX_RETRIES):
            if attempt > 0:
                delay = _RETRY_DELAY * attempt
                time.sleep(delay)

            self._respect_rate_limit()

            try:
                with sync_playwright() as pw:
                    browser = pw.chromium.launch(
                        headless=True,
                        args=["--no-sandbox", "--disable-dev-shm-usage"],
                    )
                    try:
                        context = browser.new_context(user_agent=USER_AGENT)
                        try:
                            page = context.new_page()
                            page.route("**/*", _block_heavy_resource)
                            page.goto(MIRURO_BASE, wait_until="networkidle", timeout=25000)
                            encoded = _encode_pipe(payload)
                            js = f"""
                            (async () => {{
                                const r = await fetch("{MIRURO_PIPE}?e={encoded}");
                                return {{status: r.status, text: await r.text()}};
                            }})()
                            """
                            result = page.evaluate(js)
                            status = result.get("status")
                            if status in _RETRYABLE_STATUS:
                                last_error = f"status {status}"
                                continue
                            if status != 200:
                                return None
                            return _decode_pipe(result["text"].strip())
                        finally:
                            context.close()
                    finally:
                        browser.close()
            except Exception as e:
                last_error = repr(e)

        return None

    def search(self, query: str) -> List[Dict]:
        results = self._search_anilist(query)
        if results:
            return results
        sys.stderr.write("[!] AniList search unavailable, falling back to miruro pipe search.\n")
        return self._search_pipe(query)

    def _search_anilist(self, query: str) -> List[Dict]:
        try:
            r = _SEARCH_CLIENT.post(
                ANILIST_URL,
                json={
                    "query": _SEARCH_QUERY,
                    "variables": {"search": query, "page": 1, "perPage": 25},
                },
            )
            if r.status_code != 200:
                return []
            data = r.json()
            media = data.get("data", {}).get("Page", {}).get("media", [])
            return [
                {
                    "title": m["title"].get("romaji") or m["title"].get("english") or "",
                    "id": str(m["id"]),
                }
                for m in media if m.get("id")
            ]
        except Exception:
            return []

    def _search_pipe(self, query: str) -> List[Dict]:
        try:
            payload = {
                "path": "search",
                "method": "GET",
                "query": {"query": query},
                "body": None,
                "version": "0.1.0",
            }
            data = self._pipe_fetch(payload)
            if not isinstance(data, list):
                return []
            scored = []
            for item in data:
                if not isinstance(item, dict):
                    continue
                t = item.get("title") or {}
                best_score = 0.0
                best_title = ""
                for key in ("romaji", "english", "native"):
                    title = t.get(key) or ""
                    score = _search_score(query, title)
                    if score > best_score:
                        best_score = score
                        best_title = title
                aid = item.get("id")
                if not aid:
                    continue
                if best_score > 0.0:
                    scored.append((best_score, best_title, str(aid)))
            scored.sort(key=lambda x: x[0], reverse=True)
            return [{"title": t, "id": a} for _, t, a in scored[:25]]
        except Exception:
            return []

    def get_episodes(self, anime_id: str) -> List[Dict]:
        try:
            anilist_id = int(anime_id)
        except (ValueError, TypeError):
            return []
        try:
            payload = {
                "path": "episodes",
                "method": "GET",
                "query": {"anilistId": anilist_id},
                "body": None,
                "version": "0.1.0",
            }
            data = self._pipe_fetch(payload)
            if not data:
                return []
            providers = data.get("providers", {})
            raw = {}
            providers_sorted = sorted(
                providers.items(),
                key=lambda x: self._PROVIDER_PRIORITY.index(x[0])
                if x[0] in self._PROVIDER_PRIORITY
                else len(self._PROVIDER_PRIORITY),
            )
            for pname, pdata in providers_sorted:
                if not isinstance(pdata, dict):
                    continue
                eps = pdata.get("episodes", {})
                if isinstance(eps, dict):
                    categories = ("dub", "sub") if self.preferred_category == "dub" else ("sub", "dub")
                    for category in categories:
                        ep_list = eps.get(category, [])
                        if not isinstance(ep_list, list):
                            continue
                        for ep in ep_list:
                            if not isinstance(ep, dict):
                                continue
                            ep_num = ep.get("number")
                            ep_id = ep.get("id", "")
                            if ep_num is None or not ep_id:
                                continue
                            if ep_num not in raw:
                                raw[ep_num] = {
                                    "eid": ep_id,
                                    "provider": pname,
                                    "cat": category,
                                }
            return [
                {
                    "episode_num": float(ep_num),
                    "id": json.dumps({
                        "eid": meta["eid"],
                        "provider": meta["provider"],
                        "anilist_id": anilist_id,
                        "category": meta["cat"],
                    }),
                }
                for ep_num, meta in sorted(raw.items())
            ]
        except Exception:
            return []

    def get_stream_url(self, episode_id: str) -> Dict:
        try:
            meta = json.loads(episode_id)
            raw_eid = meta.get("eid", "")
            provider = meta.get("provider", "")
            anilist_id = meta.get("anilist_id", 0)
            category = meta.get("category", "sub")
        except (json.JSONDecodeError, TypeError):
            return {"stream_url": None, "headers": {}}
        if not raw_eid or not provider:
            return {"stream_url": None, "headers": {}}
        try:
            payload = {
                "path": "sources",
                "method": "GET",
                "query": {
                    "episodeId": raw_eid,
                    "provider": provider,
                    "anilistId": anilist_id,
                    "category": category,
                },
                "body": None,
                "version": "0.1.0",
            }
            data = self._pipe_fetch(payload)
            if not data:
                return {"stream_url": None, "headers": {}}
            streams = data.get("streams", [])
            if not streams:
                return {"stream_url": None, "headers": {}}
            for s in streams:
                url = s.get("url", "")
                if url and (".m3u8" in url or ".mp4" in url):
                    ref = s.get("referer", MIRURO_BASE)
                    return {
                        "stream_url": url,
                        "headers": {"Referer": ref, "User-Agent": USER_AGENT},
                    }
            url = streams[0].get("url", "")
            if url:
                ref = streams[0].get("referer", MIRURO_BASE)
                return {
                    "stream_url": url,
                    "headers": {"Referer": ref, "User-Agent": USER_AGENT},
                }
            return {"stream_url": None, "headers": {}}
        except Exception:
            return {"stream_url": None, "headers": {}}
