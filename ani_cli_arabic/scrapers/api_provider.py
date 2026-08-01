import os
from typing import Dict, List, Optional, Tuple

import httpx

from .base import BaseScraper, quality_rank

USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/126.0.0.0 Safari/537.36"
)

DEFAULT_API_BASE = os.environ.get("ANI_API_BASE_URL", "")

_API_BASES = [
    "https://api.consumet.org",
    "https://consumet-org-api.vercel.app",
    "https://anime-api-eta-pied.vercel.app",
    "https://consumet-api-ivory.vercel.app",
]

_PROVIDERS = ["gogoanime", "aniwatch", "zoro"]

_TIMEOUT = httpx.Timeout(8.0, connect=5.0)

_CLIENT = httpx.Client(
    headers={"User-Agent": USER_AGENT},
    timeout=_TIMEOUT,
    follow_redirects=True,
)


class ApiScraper(BaseScraper):

    _cached_base = ""
    _cached_prov = ""

    @property
    def name(self) -> str:
        return "api"

    def __init__(self):
        self._base_url, self._active_prov = self._resolve()

    @staticmethod
    def _resolve() -> Tuple[str, str]:
        if DEFAULT_API_BASE:
            return DEFAULT_API_BASE.rstrip("/"), ""
        return "", ""

    def _discover(self) -> bool:
        if self._base_url:
            if self.__class__._cached_base:
                self._base_url = self.__class__._cached_base
                self._active_prov = self.__class__._cached_prov
                return True
            if self._test_base(self._base_url):
                self.__class__._cached_base = self._base_url
                self.__class__._cached_prov = self._active_prov
                return True
            return False
        if self.__class__._cached_base:
            self._base_url = self.__class__._cached_base
            self._active_prov = self.__class__._cached_prov
            return True
        for base in _API_BASES:
            if self._test_base(base):
                self._base_url = base
                self.__class__._cached_base = base
                self.__class__._cached_prov = self._active_prov
                return True
        return False

    def _test_base(self, base: str) -> bool:
        for prov in _PROVIDERS:
            try:
                r = _CLIENT.get(f"{base}/anime/{prov}/naruto", timeout=5.0)
                if r.status_code == 200:
                    self._active_prov = prov
                    return True
            except Exception:
                continue
        return False

    def search(self, query: str) -> List[Dict]:
        if not self._discover():
            return []
        try:
            r = _CLIENT.get(
                f"{self._base_url}/anime/{self._active_prov}/{query}",
                params={"page": 1},
            )
            if r.status_code != 200:
                return []
            data = r.json()
            results = data.get("results", [])
            return [
                {"title": a.get("title", ""), "id": a.get("id", "")}
                for a in results if a.get("id")
            ]
        except Exception:
            return []

    def get_episodes(self, anime_id: str) -> List[Dict]:
        if not self._discover():
            return []
        try:
            r = _CLIENT.get(
                f"{self._base_url}/anime/{self._active_prov}/info/{anime_id}"
            )
            if r.status_code != 200:
                return []
            data = r.json()
            eps = data.get("episodes", [])
            return [
                {"episode_num": float(e.get("number", i + 1)), "id": e.get("id", "")}
                for i, e in enumerate(eps) if e.get("id")
            ]
        except Exception:
            return []

    def get_stream_url(self, episode_id: str) -> Dict:
        if not self._discover():
            return {"stream_url": None, "headers": {}}
        data = None
        for attempt in range(2):
            try:
                r = _CLIENT.get(
                    f"{self._base_url}/anime/{self._active_prov}/watch/{episode_id}"
                )
                if r.status_code != 200:
                    if attempt == 0:
                        continue
                    return {"stream_url": None, "headers": {}}
                data = r.json()
                break
            except Exception:
                if attempt == 0:
                    continue
                return {"stream_url": None, "headers": {}}
        if not data:
            return {"stream_url": None, "headers": {}}
        sources = data.get("sources", [])
        if not sources:
            return {"stream_url": None, "headers": {}}
        sources = sorted(
            sources, key=lambda s: quality_rank(s.get("quality")), reverse=True
        )
        source = next((s for s in sources if s.get("url")), None)
        if not source:
            return {"stream_url": None, "headers": {}}
        url = source.get("url", "")
        headers: Dict[str, str] = {"User-Agent": USER_AGENT}
        source_headers = source.get("headers")
        if isinstance(source_headers, dict):
            ref = source_headers.get("Referer")
            if ref:
                headers["Referer"] = str(ref)
        headers.setdefault("Referer", f"{self._base_url}/")
        return {"stream_url": url, "headers": headers}
