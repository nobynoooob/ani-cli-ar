"""HiAnime (hianime.to / aniwatch) scraper.

Uses HiAnime's internal `/ajax/` endpoints which are the same protocol consumed
by the official frontend. These endpoints are behind Cloudflare so we fetch
them inside a real browser context (mirroring the Miruro scraper's approach),
falling back to plain HTTP when the domain is not CF-gated.
"""
import re
import sys
from typing import Dict, List, Optional

import httpx

from .base import BaseScraper
from .embeds import resolve_embed

BASE_URL = "https://hianime.to"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)

_CLIENT = httpx.Client(
    headers={
        "User-Agent": USER_AGENT,
        "Referer": BASE_URL + "/",
        "Accept": "application/json, text/plain, */*",
        "X-Requested-With": "XMLHttpRequest",
    },
    timeout=httpx.Timeout(12.0, connect=8.0),
    follow_redirects=True,
)


def _clean_url(url: str) -> str:
    if url.startswith("//"):
        return "https:" + url
    return url


def _extract_card_html(html: str, base: str) -> str:
    """Return HTML fragment for ajax search suggest (rendered card)."""
    return html or ""


class HiAnimeScraper(BaseScraper):

    @property
    def name(self) -> str:
        return "hianime"

    def _fetch_ajax(self, path: str) -> str:
        """Fetch a HiAnime ajax endpoint, via browser when Cloudflare blocks HTTP."""
        url = f"{BASE_URL}{path}"
        # Plain HTTP attempt first.
        try:
            r = _CLIENT.get(url)
            if r.status_code == 200 and "abstract" not in r.text[:60]:
                return r.text
        except Exception:
            pass
        # Browser fallback (CF challenge).
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            return ""
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True, args=["--no-sandbox"])
            ctx = browser.new_context(user_agent=USER_AGENT)
            page = ctx.new_page()
            try:
                page.goto(BASE_URL, wait_until="domcontentloaded", timeout=20000)
                page.wait_for_timeout(2000)
                data = page.evaluate(
                    "async (u) => { const r = await fetch(u); return { s: r.status, t: await r.text() }; }",
                    url,
                )
                if data and data.get("s") == 200:
                    return data.get("t", "")
            except Exception:
                pass
            browser.close()
        return ""

    def search(self, query: str) -> List[Dict]:
        import urllib.parse
        html = self._fetch_ajax(f"/ajax/search/suggest?keyword={urllib.parse.quote(query)}")
        # The ajax response is a `{html: ...}` JSON containing card anchors.
        m = re.search(r'\{\s*"html"\s*:\s*"(.*)', html, re.DOTALL)
        raw = None
        try:
            import json
            raw = json.loads(html).get("html", "")
        except Exception:
            raw = m.group(1) if m else ""
        results = []
        seen = set()
        for link in re.findall(r'href="([^"]+)"\s*[^>]*>?\s*<h3[^>]*>([^<]+)</h3>', raw):
            href, title = link
            if not href.startswith("http"):
                href = BASE_URL + href
            match = re.search(r"/watch/([^/?#]+)", href)
            aid = (match.group(1) if match else "") or href.rstrip("/").rsplit("/", 1)[-1]
            if aid and aid not in seen:
                seen.add(aid)
                results.append({"title": title.strip(), "id": aid})
        return results

    def get_episodes(self, anime_id: str) -> List[Dict]:
        html = self._fetch_ajax(f"/ajax/v2/episode/list/{anime_id}")
        try:
            import json
            data = json.loads(html)
        except Exception:
            return []
        ul = (data.get("html") or "") if isinstance(data, dict) else ""
        ids = re.findall(r'data-id="([^"]+)"', ul)
        out = []
        for eid in ids:
            out.append({"episode_num": len(out) + 1, "id": eid})
        return out

    def get_stream_url(self, episode_id: str) -> Dict:
        # episode_id is the episode data-id from the ajax list.
        server_html = self._fetch_ajax(f"/ajax/v2/episode/servers?episodeId={episode_id}")
        try:
            import json
            data = json.loads(server_html)
        except Exception:
            return {"stream_url": None, "headers": {}}
        servers = (data.get("html") or "") if isinstance(data, dict) else ""

        # Pick a megacloud/gogo server id (highest priority).
        sid = None
        for m in re.finditer(r'data-id="(\d+)"[^>]*data-type="(\d+)"', servers):
            if sid is None:
                sid = m.group(1)
        if not sid:
            m = re.search(r'data-id="([^"]+)"', servers)
            sid = m.group(1) if m else None
        if not sid:
            return {"stream_url": None, "headers": {}}

        srcs = self._fetch_ajax(f"/ajax/v2/episode/sources?id={sid}")
        try:
            import json
            data = json.loads(srcs)
        except Exception:
            return {"stream_url": None, "headers": {}}
        if not isinstance(data, dict):
            return {"stream_url": None, "headers": {}}
        if data.get("link"):
            return {
                "stream_url": _clean_url(data["link"]),
                "headers": {"Referer": BASE_URL + "/", "User-Agent": USER_AGENT},
            }
        # Some responses embed a track/src object.
        src = (data.get("sources") or [{}])[0].get("file") if data.get("sources") else ""
        if src:
            return {
                "stream_url": _clean_url(src),
                "headers": {"Referer": BASE_URL + "/", "User-Agent": USER_AGENT},
            }
        return {"stream_url": None, "headers": {}}