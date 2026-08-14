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
from ._http_log import LoggingClient

BASE_URL = "https://hianime.to"
# Some networks block the canonical domain; prioritize mirrors that actually
# answer the ajax protocol. Dead/parked mirrors fall back to the default.
_MIRRORS = ["https://hianime.to", "https://hi-anime.co"]
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)

_CLIENT = LoggingClient(
    "hianime",
    headers={
        "User-Agent": USER_AGENT,
        "Referer": BASE_URL + "/",
        "Accept": "application/json, text/plain, */*",
        "X-Requested-With": "XMLHttpRequest",
    },
    timeout=httpx.Timeout(5.0, connect=3.0),
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

    requires_browser = True

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._base_url = BASE_URL

    @property
    def name(self) -> str:
        return "hianime"

    def _pick_mirror(self, path: str) -> str:
        """Return a mirror that actually answers (JSON) for ``path``.

        The current base URL is tested first; if it comes back empty/blocked,
        alternates are probed so a dead or CF-gated canonical domain is
        transparently replaced by a working mirror.
        """
        ordered = [self._base_url] + [
            m for m in _MIRRORS if m != self._base_url
        ]
        try:
            import httpx as _hx
            for mirror in ordered:
                try:
                    r = _hx.get(
                        f"{mirror}{path}",
                        headers={"User-Agent": USER_AGENT, "Referer": mirror + "/"},
                        timeout=4.0,
                        follow_redirects=True,
                    )
                    # A real HiAnime ajax endpoint returns JSON; parked/CF pages
                    # return HTML (or a challenge), so require JSON-looking text.
                    if (
                        r.status_code == 200
                        and r.text.lstrip().startswith(("{", "[", '"'))
                    ):
                        return mirror
                except Exception:
                    continue
        except Exception:
            pass
        return self._base_url

    def _fetch_ajax(self, path: str, cancel_event=None) -> str:
        """Fetch a HiAnime ajax endpoint, via browser when Cloudflare blocks HTTP."""
        self._base_url = self._pick_mirror(path)
        url = f"{self._base_url}{path}"
        # Plain HTTP attempt first.
        try:
            r = _CLIENT.get(url)
            if r.status_code == 200 and "abstract" not in r.text[:60]:
                return r.text
        except Exception:
            pass
        # Browser fallback (CF challenge) via the shared lazy runtime. Skip it
        # entirely when the resolution has been aborted so this slow job never
        # occupies the shared browser worker.
        if cancel_event is not None and cancel_event.is_set():
            return ""
        from ._browser import browser_page
        from ._http_log import timed

        def _work(page):
            page.goto(self._base_url, wait_until="domcontentloaded", timeout=8000)
            page.wait_for_timeout(1000)
            return page.evaluate(
                "async (u) => { const r = await fetch(u); return { s: r.status, t: await r.text() }; }",
                url,
                timeout=8000,
            )

        try:
            with timed("hianime:browser:fetch"):
                data = browser_page(
                    _work, user_agent=USER_AGENT, timeout=15.0,
                    cancel_event=cancel_event,
                )
        except Exception:
            data = None
        if data and data.get("s") == 200:
            return data.get("t", "")
        return ""

    def search(self, query: str, cancel_event=None) -> List[Dict]:
        import urllib.parse
        html = self._fetch_ajax(f"/ajax/search/suggest?keyword={urllib.parse.quote(query)}", cancel_event=cancel_event)
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
                href = self._base_url + href
            match = re.search(r"/watch/([^/?#]+)", href)
            aid = (match.group(1) if match else "") or href.rstrip("/").rsplit("/", 1)[-1]
            if aid and aid not in seen:
                seen.add(aid)
                results.append({"title": title.strip(), "id": aid})
        return results

    def get_episodes(self, anime_id: str, cancel_event=None) -> List[Dict]:
        html = self._fetch_ajax(f"/ajax/v2/episode/list/{anime_id}", cancel_event=cancel_event)
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

    def get_stream_url(self, episode_id: str, cancel_event=None) -> Dict:
        # episode_id is the episode data-id from the ajax list.
        server_html = self._fetch_ajax(f"/ajax/v2/episode/servers?episodeId={episode_id}", cancel_event=cancel_event)
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

        srcs = self._fetch_ajax(f"/ajax/v2/episode/sources?id={sid}", cancel_event=cancel_event)
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
                "headers": {"Referer": self._base_url + "/", "User-Agent": USER_AGENT},
            }
        # Some responses embed a track/src object.
        src = (data.get("sources") or [{}])[0].get("file") if data.get("sources") else ""
        if src:
            return {
                "stream_url": _clean_url(src),
                "headers": {"Referer": self._base_url + "/", "User-Agent": USER_AGENT},
            }
        return {"stream_url": None, "headers": {}}