import asyncio
import json
import sys
from typing import Dict, List, Optional, Tuple

from .base import BaseScraper
from .gogoanime import GogoAnimeScraper
from .mkissa import MkissaScraper
from .api_provider import ApiScraper
from .miruro import MiruroScraper

ENGLISH_PROVIDERS = ["miruro", "api", "mkissa", "gogoanime"]
ARABIC_PROVIDERS = ["arabic_api_primary", "arabic_api_backup"]

_PROVIDER_ORDER = {
    "english": ENGLISH_PROVIDERS,
    "arabic": ARABIC_PROVIDERS,
}

_PROVIDER_TIMEOUT = 30.0


def get_provider_list(language: str = "english") -> List[str]:
    lang_clean = (language or "english").lower()
    return list(_PROVIDER_ORDER.get(lang_clean, ENGLISH_PROVIDERS))


class ProviderManager:

    def __init__(self, preferred_provider: Optional[str] = None):
        self._providers: Dict[str, BaseScraper] = {}
        self._preferred = preferred_provider
        self._register_defaults()

    def _register_defaults(self):
        self.register("miruro", MiruroScraper())
        self.register("api", ApiScraper())
        self.register("mkissa", MkissaScraper())
        self.register("gogoanime", GogoAnimeScraper())

    def register(self, name: str, scraper: BaseScraper):
        self._providers[name] = scraper

    @property
    def available_providers(self) -> List[str]:
        return list(self._providers.keys())

    def _get_ordered_providers(
        self, language: str, provider: Optional[str] = None,
    ):
        provider_clean = (provider or "auto").lower()
        order = _PROVIDER_ORDER.get(language, ENGLISH_PROVIDERS)

        if provider_clean != "auto":
            if provider_clean in self._providers:
                yield provider_clean, self._providers[provider_clean]
            for name in order:
                if name != provider_clean and name in self._providers:
                    yield name, self._providers[name]
            return

        if self._preferred and self._preferred in self._providers:
            yield self._preferred, self._providers[self._preferred]
        for name in order:
            if name != self._preferred and name in self._providers:
                yield name, self._providers[name]

    async def resolve_stream(
        self,
        anime_title: str,
        episode_num,
        mode: str = "sub",
        language: str = "english",
        provider: str = "auto",
    ) -> Tuple[Optional[str], Dict, Optional[str]]:
        lang_clean = (language or "english").lower()
        provider_clean = (provider or "auto").lower()

        if lang_clean not in _PROVIDER_ORDER:
            lang_clean = "english"

        for name, scraper in self._get_ordered_providers(lang_clean, provider_clean):
            sys.stderr.write(f"[?] Attempting provider: {name}...\n")
            try:
                result = await asyncio.wait_for(
                    asyncio.to_thread(
                        self._try_provider, scraper, anime_title, episode_num, mode
                    ),
                    timeout=_PROVIDER_TIMEOUT,
                )
                if result:
                    url, headers = result
                    sys.stderr.write(f"[✓] Stream found via {name}!\n")
                    return url, headers, name
                sys.stderr.write(f"[✗] {name} returned no stream.\n")
            except asyncio.TimeoutError:
                sys.stderr.write(f"[✗] {name} timed out after {_PROVIDER_TIMEOUT}s.\n")
            except Exception:
                sys.stderr.write(f"[✗] {name} errored, skipping to next provider.\n")

        if lang_clean == "english":
            sys.stderr.write(
                f"No working English streams found for '{anime_title}' ep {episode_num}. "
                "Please try another provider or check your network.\n"
            )
        return None, {}, None

    @staticmethod
    def _try_provider(
        scraper: BaseScraper, anime_title: str, episode_num, mode: str = "sub"
    ) -> Optional[Tuple[str, Dict]]:
        mode_clean = (mode or "sub").lower()
        if mode_clean not in ("sub", "dub"):
            mode_clean = "sub"

        try:
            results = scraper.search(anime_title)
        except Exception:
            return None
        if not results:
            return None

        anime_id = results[0]["id"]

        try:
            if hasattr(scraper, "preferred_category"):
                scraper.preferred_category = mode_clean
            eps = scraper.get_episodes(anime_id)
        except Exception:
            return None
        if not eps:
            return None

        target = str(int(float(episode_num)))
        ep_id = None
        for ep in eps:
            if str(int(float(ep["episode_num"]))) == target:
                ep_id = ep["id"]
                break
        if not ep_id:
            ep_id = eps[0]["id"]

        if mode_clean == "dub":
            try:
                meta = json.loads(ep_id)
                if meta.get("category", "sub") != "dub":
                    sys.stderr.write(
                        "[!] Dub not available for this title, falling back to Sub.\n"
                    )
            except (json.JSONDecodeError, TypeError):
                pass

        try:
            stream = scraper.get_stream_url(ep_id)
        except Exception:
            return None
        if stream.get("stream_url"):
            return stream["stream_url"], stream.get("headers", {})
        return None
