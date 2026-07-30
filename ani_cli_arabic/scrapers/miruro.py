import base64
import gzip
import json
import threading
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


def _encode_pipe(payload: dict) -> str:
    return base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip("=")


def _decode_pipe(raw: str) -> dict:
    raw += "=" * (4 - len(raw) % 4)
    compressed = base64.urlsafe_b64decode(raw)
    return json.loads(gzip.decompress(compressed).decode("utf-8"))


class MiruroScraper(BaseScraper):

    _pw = None
    _browser = None
    _lock = threading.Lock()

    _PROVIDER_PRIORITY = ["pewe", "kiwi", "bee", "bonk", "ally", "moo", "hop"]

    @property
    def name(self) -> str:
        return "miruro"

    @staticmethod
    def _get_shared_browser():
        with MiruroScraper._lock:
            if MiruroScraper._browser is None:
                from playwright.sync_api import sync_playwright
                MiruroScraper._pw = sync_playwright().start()
                MiruroScraper._browser = MiruroScraper._pw.chromium.launch(
                    headless=True,
                    args=["--no-sandbox", "--disable-dev-shm-usage"],
                )
            return MiruroScraper._browser

    def _pipe_fetch(self, payload: dict) -> Optional[dict]:
        browser = self._get_shared_browser()
        context = browser.new_context(user_agent=USER_AGENT)
        try:
            page = context.new_page()
            page.goto(MIRURO_BASE, wait_until="networkidle", timeout=25000)
            encoded = _encode_pipe(payload)
            js = f"""
            (async () => {{
                const r = await fetch("{MIRURO_PIPE}?e={encoded}");
                return {{status: r.status, text: await r.text()}};
            }})()
            """
            result = page.evaluate(js)
            if result.get("status") != 200:
                return None
            return _decode_pipe(result["text"].strip())
        finally:
            context.close()

    def search(self, query: str) -> List[Dict]:
        try:
            r = httpx.post(
                ANILIST_URL,
                json={
                    "query": _SEARCH_QUERY,
                    "variables": {"search": query, "page": 1, "perPage": 25},
                },
                headers={"Content-Type": "application/json", "User-Agent": USER_AGENT},
                timeout=10.0,
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
                    for category in ("sub", "dub"):
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
