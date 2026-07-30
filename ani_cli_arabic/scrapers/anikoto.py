import re
import sys
import time
from typing import Dict, List

from .base import BaseScraper

BASE_URL = "https://anikototv.to"
USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/126.0.0.0 Safari/537.36"
)


class AnikotoScraper(BaseScraper):

    @property
    def name(self) -> str:
        return "anikoto"

    def _pw_html(self, url: str, wait_sel: str = "") -> str:
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
                if wait_sel:
                    try:
                        page.wait_for_selector(wait_sel, timeout=8000)
                    except Exception:
                        pass
                time.sleep(2)
                result = page.content()
            except Exception:
                pass
            browser.close()
        return result

    def search(self, query: str) -> List[Dict]:
        import urllib.parse
        html = self._pw_html(
            f"{BASE_URL}/filter?keyword={urllib.parse.quote(query)}",
            wait_sel=".item",
        )
        if not html:
            return []

        seen = set()
        results = []
        for m in re.finditer(r'href="([^"]*)/ep-\d+"', html):
            full = m.group(1)
            if full in seen:
                continue
            seen.add(full)
            slug = full.rsplit("/", 1)[-1] if "/" in full else full
            name = slug.rsplit("-", 1)[0].replace("-", " ").title() if "-" in slug else slug
            results.append({"title": name, "id": slug})
        return results

    def get_episodes(self, anime_id: str) -> List[Dict]:
        html = self._pw_html(
            f"{BASE_URL}/watch/{anime_id}/ep-1",
            wait_sel=".episodes",
        )
        if not html:
            return []
        nums = sorted(set(float(m) for m in re.findall(r"/ep-(\d+)", html)))
        return [
            {"episode_num": n, "id": f"{anime_id}/{n}"}
            for n in nums
        ]

    def get_stream_url(self, episode_id: str) -> Dict:
        parts = episode_id.split("/", 1)
        show_id = parts[0]
        ep_str = str(int(float(parts[1])))

        html = self._pw_html(
            f"{BASE_URL}/watch/{show_id}/ep-{ep_str}",
            wait_sel="iframe",
        )
        if not html:
            return {"stream_url": None, "headers": {}}

        for m in re.finditer(r'<iframe[^>]*src="([^"]+)"', html, re.IGNORECASE):
            embed_url = m.group(1)
            if not embed_url.startswith("http"):
                continue
            video = self._resolve_embed(embed_url)
            if video:
                return {"stream_url": video, "headers": {"Referer": embed_url, "User-Agent": USER_AGENT}}

        return {"stream_url": None, "headers": {}}

    def _resolve_embed(self, url: str) -> str:
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
