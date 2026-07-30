import re
import sys
import time
from typing import Dict, List

import requests

from .base import BaseScraper

BASE_URL = "https://animepahe.su"
USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/126.0.0.0 Safari/537.36"
)


class AnimePaheScraper(BaseScraper):

    @property
    def name(self) -> str:
        return "animepahe"

    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": USER_AGENT})

    def _pw_json(self, url: str, target: str = "") -> dict:
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            return {}

        result = {}
        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=True,
                args=["--disable-blink-features=AutomationControlled", "--no-sandbox"],
            )
            ctx = browser.new_context(
                user_agent=USER_AGENT, viewport={"width": 1280, "height": 720},
            )
            ctx.add_init_script(
                "Object.defineProperty(navigator,'webdriver',{get:()=>undefined})"
            )
            page = ctx.new_page()
            page.on("response", lambda r: (
                result.update({"data": r.json()})
                if target in r.url and r.ok else None
            ))
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=12000)
                deadline = time.time() + 10
                while time.time() < deadline and "data" not in result:
                    time.sleep(0.3)
            except Exception:
                pass
            browser.close()
        return result.get("data", {})

    def search(self, query: str) -> List[Dict]:
        import urllib.parse
        data = self._pw_json(
            f"{BASE_URL}/api?m=search&l=8&q={urllib.parse.quote(query)}",
            target="/api",
        )
        if not isinstance(data, dict):
            return []
        return [
            {"title": item.get("title", ""), "id": str(item.get("id"))}
            for item in data.get("data", [])
        ]

    def get_episodes(self, anime_id: str) -> List[Dict]:
        data = self._pw_json(
            f"{BASE_URL}/api?m=release&id={anime_id}&sort=episode_asc&page=1",
            target="/api",
        )
        if not isinstance(data, dict):
            return []
        return [
            {"episode_num": float(item.get("episode", 0)), "id": f"{anime_id}/{item.get('session', item.get('id', ''))}"}
            for item in data.get("data", [])
            if item.get("episode") is not None
        ]

    def get_stream_url(self, episode_id: str) -> Dict:
        parts = episode_id.split("/", 2)
        anime_id = parts[0]
        session = parts[1] if len(parts) > 1 else ""

        if not session:
            eps = self.get_episodes(anime_id)
            if not eps:
                return {"stream_url": None, "headers": {}}
            session = eps[0]["id"].split("/", 1)[1] if "/" in eps[0]["id"] else ""

        import urllib.parse
        data = self._pw_json(
            f"{BASE_URL}/api?m=links&id={session}&p=kwok",
            target="/api",
        )
        if not isinstance(data, dict):
            return {"stream_url": None, "headers": {}}

        for item in data.get("data", []):
            kwik_url = item.get("link", "")
            if not kwik_url:
                continue
            video = self._resolve_kwik(kwik_url)
            if video:
                return {"stream_url": video, "headers": {"Referer": kwik_url, "User-Agent": USER_AGENT}}

        return {"stream_url": None, "headers": {}}

    def _resolve_kwik(self, url: str) -> str:
        html = self._pw_html(url)
        if not html:
            return ""
        for pat in [
            r'(https?://[^"\'<>\s]+\.(?:mp4|m3u8)[^"\'<>\s]*)',
            r'file["\']?\s*[:=]\s*["\']([^"\']+)',
        ]:
            for m in re.findall(pat, html, re.IGNORECASE):
                m = m.strip().rstrip('"').rstrip("'")
                if m.startswith("http") and (".m3u8" in m or ".mp4" in m):
                    return m
        return ""

    def _pw_html(self, url: str) -> str:
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            return ""
        result = ""
        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=True,
                args=["--disable-blink-features=AutomationControlled", "--no-sandbox"],
            )
            ctx = browser.new_context(
                user_agent=USER_AGENT, viewport={"width": 1280, "height": 720},
            )
            ctx.add_init_script(
                "Object.defineProperty(navigator,'webdriver',{get:()=>undefined})"
            )
            page = ctx.new_page()
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=12000)
                time.sleep(3)
                result = page.content()
            except Exception:
                pass
            browser.close()
        return result
