import asyncio
import json
import sys
from typing import Dict, List, Optional, Tuple

from .base import BaseScraper
from .gogoanime import GogoAnimeScraper
from .mkissa import MkissaScraper
from .api_provider import ApiScraper
from .miruro import MiruroScraper
from .hianime import HiAnimeScraper
from .allanime import AniThemeScraper

ENGLISH_PROVIDERS = ["miruro", "hianime", "allanime", "api", "mkissa", "gogoanime"]
ARABIC_PROVIDERS = ["arabic_api_primary", "arabic_api_backup"]

# Sentinal values for the "Ask Every Time" provider preference. When the user
# picks this in settings, playback prompts interactively per session.
PROVIDER_ASK = "ask"
PROVIDER_ASK_VALUES = {"ask", "ask_every_time", "ask every time"}


def is_provider_ask(value) -> bool:
    """True if the setting means 'Ask Every Time' (any normalized spelling)."""
    return (value or "").strip().lower() in PROVIDER_ASK_VALUES


def normalize_provider(value) -> str:
    """Map a settings provider value to a safe provider choice (default 'auto').

    The 'ask' sentinel never reaches the resolver chain directly: it is mapped
    to 'auto' so callers that skip the interactive prompt fall back gracefully.
    """
    v = (value or "auto").strip().lower()
    if v in PROVIDER_ASK_VALUES:
        return "auto"
    return v or "auto"

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
        self.register("hianime", HiAnimeScraper())
        self.register("allanime", AniThemeScraper())
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
            for name, scraper in self._providers.items():
                if name != provider_clean and name not in order:
                    yield name, scraper
            return

        if self._preferred and self._preferred in self._providers:
            yield self._preferred, self._providers[self._preferred]
        for name in order:
            if name != self._preferred and name in self._providers:
                yield name, self._providers[name]
        for name, scraper in self._providers.items():
            if name != self._preferred and name not in order:
                yield name, scraper

    async def resolve_stream(
        self,
        anime_title: str,
        episode_num,
        mode: str = "sub",
        language: str = "english",
        provider: str = "auto",
        quiet: bool = False,
    ) -> Tuple[Optional[str], Dict, Optional[str]]:
        lang_clean = (language or "english").lower()
        provider_clean = (provider or "auto").lower()

        if lang_clean not in _PROVIDER_ORDER:
            lang_clean = "english"

        def _log(msg: str):
            if not quiet:
                sys.stderr.write(msg)

        for name, scraper in self._get_ordered_providers(lang_clean, provider_clean):
            _log(f"[?] Attempting provider: {name}...\n")
            context = {
                "anime": anime_title,
                "episode": str(episode_num),
                "provider": name,
                "mode": mode or "sub",
                "translation_mode": (mode or "sub").lower(),
            }
            try:
                result = await asyncio.wait_for(
                    asyncio.to_thread(
                        self._try_provider, scraper, anime_title, episode_num, mode
                    ),
                    timeout=_PROVIDER_TIMEOUT,
                )
                if result:
                    url, headers = result
                    _log(f"[✓] Stream found via {name}!\n")
                    return url, headers, name
                _log(f"[✗] {name} returned no stream.\n")
            except asyncio.TimeoutError:
                _log(f"[✗] {name} timed out after {_PROVIDER_TIMEOUT}s.\n")
                self._report_error(f"{name} timed out after {_PROVIDER_TIMEOUT}s", context)
            except Exception:
                _log(f"[✗] {name} errored, skipping to next provider.\n")
                self._report_error(f"{name} raised an unexpected error", context,
                                   exc_info=sys.exc_info())

        if lang_clean == "english":
            _log(
                f"No working English streams found for '{anime_title}' ep {episode_num}. "
                "Please try another provider or check your network.\n"
            )
        self._report_error(
            "No working stream found after trying all providers",
            {"anime": anime_title, "episode": str(episode_num), "language": lang_clean,
             "providers": ",".join(name for name, _ in self._get_ordered_providers(lang_clean, provider_clean))},
        )
        return None, {}, None

    @staticmethod
    def _report_error(error_msg: str, context: dict, exception: BaseException = None,
                      exc_info: tuple = None):
        """Report a scraper-stage failure to telemetry without blocking the caller."""
        try:
            from ..monitoring import monitor
            monitor.track_error(error_msg, context, exception=exception, exc_info=exc_info)
        except Exception:
            pass

    @staticmethod
    def _try_provider(
        scraper: BaseScraper, anime_title: str, episode_num, mode: str = "sub"
    ) -> Optional[Tuple[str, Dict]]:
        mode_clean = (mode or "sub").lower()
        if mode_clean not in ("sub", "dub"):
            mode_clean = "sub"

        context = {
            "anime": anime_title,
            "episode": str(episode_num),
            "provider": scraper.name,
            "mode": mode_clean,
            "translation_mode": mode_clean,
        }

        try:
            results = scraper.search(anime_title)
        except Exception:
            ProviderManager._report_error("Scraper search failed", context,
                                          exc_info=sys.exc_info())
            return None
        if not results:
            ProviderManager._report_error("Scraper search returned no results", context)
            return None

        anime_id = results[0]["id"]

        try:
            if hasattr(scraper, "preferred_category"):
                scraper.preferred_category = mode_clean
            eps = scraper.get_episodes(anime_id)
        except Exception:
            ProviderManager._report_error("Scraper episode list failed",
                                          dict(context, anime_id=str(anime_id)),
                                          exc_info=sys.exc_info())
            return None
        if not eps:
            ProviderManager._report_error("Scraper episode list returned no results",
                                          dict(context, anime_id=str(anime_id)))
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
            ProviderManager._report_error("Scraper stream resolution failed",
                                          dict(context, episode_id=str(ep_id)),
                                          exc_info=sys.exc_info())
            return None
        if stream.get("stream_url"):
            return stream["stream_url"], stream.get("headers", {})
        ProviderManager._report_error("Scraper returned no stream URL",
                                      dict(context, episode_id=str(ep_id)))
        return None
