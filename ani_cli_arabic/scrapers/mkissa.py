import re
import hashlib
import base64
import sys
import time
from typing import Dict, List, Optional

import requests

from .base import BaseScraper

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

_PLAYWRIGHT_TIMEOUT = 25.0


class MkissaScraper(BaseScraper):

    @property
    def name(self) -> str:
        return "mkissa"

    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": USER_AGENT, "Referer": REFERRER})

    def search(self, query: str) -> List[Dict]:
        resp = self.session.post(
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
        resp = self.session.post(
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

    def get_stream_url(self, episode_id: str) -> Dict:
        parts = episode_id.split("/", 1)
        show_id = parts[0]
        ep_no = parts[1] if len(parts) > 1 else "1"

        stream = self._try_playwright_extract(show_id, ep_no)
        if stream:
            return stream

        return {"stream_url": None, "headers": {}}

    def _try_playwright_extract(self, show_id: str, ep_no: str) -> Optional[Dict]:
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            return None

        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=True,
                args=["--disable-blink-features=AutomationControlled", "--no-sandbox"],
            )
            ctx = browser.new_context(
                user_agent=USER_AGENT,
                viewport={"width": 1280, "height": 720},
                locale="en-US",
            )
            ctx.add_init_script(
                "Object.defineProperty(navigator,'webdriver',{get:()=>undefined})"
            )
            page = ctx.new_page()
            found_m3u8 = []

            def on_response(resp):
                url = resp.url
                if ".m3u8" in url and url not in found_m3u8:
                    found_m3u8.append(url)

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
                page.goto(url, wait_until="commit", timeout=10000)
                deadline = time.time() + _PLAYWRIGHT_TIMEOUT
                while time.time() < deadline:
                    if found_m3u8:
                        browser.close()
                        return {
                            "stream_url": found_m3u8[0],
                            "headers": {
                                "Referer": REFERRER,
                                "User-Agent": USER_AGENT,
                            },
                        }
                    page.wait_for_timeout(1000)
            except Exception:
                pass

            browser.close()

        if found_m3u8:
            return {
                "stream_url": found_m3u8[0],
                "headers": {"Referer": REFERRER, "User-Agent": USER_AGENT},
            }
        return None
