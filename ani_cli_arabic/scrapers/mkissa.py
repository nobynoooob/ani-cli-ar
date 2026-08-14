import re
import hashlib
import base64
import sys
import time
from typing import Dict, List, Optional

import requests

from .base import BaseScraper
from ._http_log import LoggingRequestsSession

API_BASE = "https://api.mkissa.net"
REFERRER = "https://mkissa.to"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:150.0) "
    "Gecko/20100101 Firefox/150.0"
)

_SEARCH_GQL = """\
query($search: SearchInput $limit: Int $page: Int \
$translationType: VaildTranslationTypeEnumType \
$countryOrigin: VaildCountryOriginEnumType) { \
shows(search: $search limit: $limit page: $page \
translationType: $translationType \
countryOrigin: $countryOrigin) { \
edges { _id name availableEpisodes airedStart __typename } } }"""

_EPISODES_GQL = """\
query ($showId: String!) { show( _id: $showId ) { \
_id availableEpisodesDetail }}"""

# Turnstile-gated episode page rarely yields a stream; keep the browser
# fallback short so it can't eat the whole provider allowance.
_PLAYWRIGHT_TIMEOUT = 8.0

_SESSION = LoggingRequestsSession("mkissa")
_SESSION.headers.update({"User-Agent": USER_AGENT, "Referer": REFERRER})


class MkissaScraper(BaseScraper):

    requires_browser = True

    @property
    def name(self) -> str:
        return "mkissa"

    def search(self, query: str) -> List[Dict]:
        resp = _SESSION.post(
            f"{API_BASE}/api",
            json={
                "variables": {
                    "search": {"allowAdult": False, "allowUnknown": False, "query": query},
                    "limit": 40,
                    "page": 1,
                    "translationType": "sub",
                    "countryOrigin": "ALL",
                },
                "query": _SEARCH_GQL,
            },
            headers={"Content-Type": "application/json", "Origin": REFERRER},
            timeout=15,
        )
        data = resp.json()
        results = []
        for edge in data.get("data", {}).get("shows", {}).get("edges", []):
            results.append({
                "title": edge["name"],
                "id": edge["_id"],
            })
        return results

    def get_episodes(self, anime_id: str) -> List[Dict]:
        resp = _SESSION.post(
            f"{API_BASE}/api",
            json={"variables": {"showId": anime_id}, "query": _EPISODES_GQL},
            headers={"Content-Type": "application/json", "Origin": REFERRER},
            timeout=15,
        )
        data = resp.json()
        detail = data.get("data", {}).get("show", {}).get("availableEpisodesDetail", {})
        eps = detail.get("sub", detail.get("dub", []))
        return [
            {"episode_num": float(e), "id": f"{anime_id}/{e}"}
            for e in eps
        ]

    def get_stream_url(self, episode_id: str, cancel_event=None) -> Dict:
        # mkissa.to and allanime share the same GraphQL backend (api.mkissa.net)
        # and anime ``_id`` values, so reuse allanime's verified client-crypto
        # handshake as the primary extraction path. The Turnstile-protected
        # episode page is only used as a last-resort fallback (it is captcha
        # gated on most networks, so it rarely produces a stream).
        try:
            from .allanime import AniThemeScraper
            stream = AniThemeScraper().get_stream_url(episode_id, cancel_event=cancel_event)
            if stream and stream.get("stream_url"):
                return stream
        except Exception:
            pass

        if cancel_event is not None and cancel_event.is_set():
            return {"stream_url": None, "headers": {}}
        parts = episode_id.split("/", 1)
        show_id = parts[0]
        ep_no = parts[1] if len(parts) > 1 else "1"
        stream = self._try_playwright_extract(show_id, ep_no, cancel_event=cancel_event)
        if stream and stream.get("stream_url"):
            return stream

        return {"stream_url": None, "headers": {}}

    def _try_playwright_extract(self, show_id: str, ep_no: str, cancel_event=None) -> Optional[Dict]:
        # Uses the shared lazy browser runtime (browser launched once, reused).
        if cancel_event is not None and cancel_event.is_set():
            return None
        from ._browser import browser_page
        from ._http_log import timed

        found = []

        def _work(page):
            def on_response(resp):
                url = resp.url
                if ".m3u8" in url and url not in found:
                    found.append(url)

            page.on("response", on_response)

            def block_route(route):
                rt = route.request.resource_type
                u = route.request.url
                if rt in ["image", "font", "stylesheet", "ping"]:
                    route.abort()
                elif any(x in u for x in ["google", "analytics", "facebook", "statlytic"]):
                    route.abort()
                else:
                    route.continue_()

            page.route("**/*", block_route)

            url = f"{REFERRER}/anime/{show_id}/ep-{ep_no}"
            try:
                page.goto(url, wait_until="commit", timeout=5000)
                deadline = time.time() + _PLAYWRIGHT_TIMEOUT
                while time.time() < deadline:
                    if found:
                        return found[0]
                    page.wait_for_timeout(1000)
            except Exception:
                pass
            return found[0] if found else ""

        try:
            with timed("mkissa:playwright:extract"):
                url = browser_page(
                    _work,
                    user_agent=USER_AGENT,
                    viewport={"width": 1280, "height": 720},
                    init_script=(
                        "Object.defineProperty(navigator,'webdriver',{get:()=>undefined})"
                    ),
                    timeout=_PLAYWRIGHT_TIMEOUT + 10.0,
                    cancel_event=cancel_event,
                )
        except Exception:
            url = None

        if url:
            return {
                "stream_url": url,
                "headers": {"Referer": REFERRER, "User-Agent": USER_AGENT},
            }
        return None
