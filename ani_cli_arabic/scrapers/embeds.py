"""Stream-embed gateway extraction helpers shared across scrapers.

Supports modern embed hosts (kwik, vidstreaming, gogo server, doodstream,
filemoon, megacloud, streamtape). Some embeds require a real browser to pass
Cloudflare/JS challenges; `resolve_embed()` routes to Playwright when the
plain HTTP pass fails.
"""
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Callable, Dict, List, Optional

USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/126.0.0.0 Safari/537.36"
)

# Aggressive timeout for embed-page HTTP extraction and direct stream
# validation. Fail fast (2.5-3s) so the first parallel worker wins instead of
# waiting on slow hosters.
_HTTP_TIMEOUT = 3.0
_CONNECT_TIMEOUT = 2.0

# A valid stream link must be a clean http(s) URL. Raw JSON/dict metadata blobs
# (flashvars, escaped ``{"url": ...}`` values) are identifiable by braces.
_JSON_METADATA_CHARS = ("{", "}")


def is_valid_stream_url(url: str) -> bool:
    """Return True only for a clean, playable http(s) stream link.

    Accepts any http(s) URL (with or without a ``.m3u8``/``.mp4`` extension,
    including query strings, tokens and dynamic endpoints). Only rejects raw
    player-metadata dicts/JSON blobs (which contain ``{``/``}``) and non-http
    links so the caller can fall back to the next provider instead of launching
    the player with garbage.
    """
    if not url or not isinstance(url, str):
        return False
    url = url.strip().strip('"').strip("'")
    if not (url.startswith("http://") or url.startswith("https://")):
        return False
    if any(ch in url for ch in _JSON_METADATA_CHARS):
        return False
    return True

_MEDIA_RE = re.compile(r'(https?://[^"\'<>\s]+\.(?:m3u8|mp4)[^"\'<>\s]*)', re.IGNORECASE)
_FILE_RE = re.compile(r'file["\']?\s*[:=]\s*["\']([^"\']+)', re.IGNORECASE)
_SRC_RE = re.compile(r'src["\']?\s*[:=]\s*["\']([^"\']+\.(?:m3u8|mp4)[^"\']*)', re.IGNORECASE)
_QUALITY_URL_RE = re.compile(r'"?url"?\s*:\s*"(https?://[^"]+\.(?:m3u8|mp4)[^"]*)"', re.IGNORECASE)


def _is_media_url(url: str) -> bool:
    """True when ``url`` looks like a real media stream link.

    Requires the ``.m3u8``/``.mp4`` marker to live in the URL *path* (after the
    host), never in the hostname itself. Hosts that contain the substring
    (e.g. ``www.mp4upload.com/embed-....html``) used to be misclassified as
    playable streams and launched the player against a dead embed page.
    """
    if not url or not isinstance(url, str):
        return False
    url = url.strip().strip('"').strip("'")
    if not (url.startswith("http://") or url.startswith("https://")):
        return False
    try:
        from urllib.parse import urlsplit
        parts = urlsplit(url)
        path = parts.path or ""
        # Also accept query strings carrying the marker (?file=....m3u8).
        query = parts.query or ""
        return bool(
            re.search(r"\.m3u8(?:\?|$|&)", path, re.IGNORECASE)
            or re.search(r"\.mp4(?:\?|$|&)", path, re.IGNORECASE)
            or re.search(r"\.m3u8(?:\?|$|&)", query, re.IGNORECASE)
            or re.search(r"\.mp4(?:\?|$|&)", query, re.IGNORECASE)
        )
    except Exception:
        return any(m in url for m in (".m3u8", ".mp4"))
# ok.ru embeds expose the playable HLS manifest in their metadata JSON.
_HLS_MANIFEST_RE = re.compile(r'"hlsManifestUrl"\s*:\s*"(https?://[^"\\]+\.m3u8[^"\\]*)"', re.IGNORECASE)
# ok.ru HTML JS-escapes quotes as \&quot; and params as \\u0026
_HLS_MANIFEST_ESC_RE = re.compile(r'\\?&quot;hlsManifestUrl\\?&quot;:\s*\\?&quot;(https?://.*?)\\?&quot;', re.IGNORECASE)
# ok.ru per-quality entries look like {"name":"hd","url":"https://...","seekSchema":...}
_OK_QUALITY_RE = re.compile(r'\{"name"\s*:\s*"(?:hd|full|sd|mobile)"\s*,\s*"url"\s*:\s*"(https?://[^"\\]+)"', re.IGNORECASE)


def _unescape(url: str) -> str:
    url = url.replace("\\u0026", "&")
    url = url.replace("\\&quot;", '"')
    url = url.replace("&quot;", '"')
    url = url.replace("\\&", "&")
    url = url.replace("&amp;", "&")
    url = url.replace("\\/", "/")
    return url


def extract_media_url(html: str) -> str:
    html = html or ""
    candidates: List[str] = []
    for pat in (_HLS_MANIFEST_RE, _HLS_MANIFEST_ESC_RE, _OK_QUALITY_RE, _MEDIA_RE, _QUALITY_URL_RE, _SRC_RE, _FILE_RE):
        for m in pat.finditer(html):
            url = _unescape((m.group(1) or "").strip().rstrip('"').rstrip("'"))
            if url and url not in candidates and _is_media_url(url):
                candidates.append(url)

    # Prefer a clean HLS manifest, then any valid direct media link.
    m3u8 = [u for u in candidates if ".m3u8" in u.lower()]
    mp4 = [u for u in candidates if ".mp4" in u.lower()]
    for pick in (m3u8 or []) + (mp4 or []):
        if is_valid_stream_url(pick):
            return pick
    return ""


def _resolve_via_browser(embed_url: str, ref_url: str, cancel_event=None) -> str:
    # Uses the shared lazy browser runtime (browser launched once, reused).
    if cancel_event is not None and cancel_event.is_set():
        return ""
    from ._browser import browser_page
    from ._http_log import timed

    found = []

    def maybe(elem):
        u = elem if isinstance(elem, str) else getattr(elem, "url", "")
        if isinstance(u, str) and _is_media_url(u) and u not in found:
            found.append(u)

    def clean(url: str) -> str:
        u = _unescape(url.strip().rstrip('"').rstrip("'"))
        return u if _is_media_url(u) else ""

    def _work(page):
        page.on("request", maybe)
        page.on("response", maybe)
        page.route(
            "**/*",
            lambda r: r.abort()
            if r.request.resource_type in ("image", "font", "stylesheet", "ping")
            else r.continue_(),
        )
        try:
            page.goto(embed_url, wait_until="domcontentloaded", timeout=5000)
            page.wait_for_timeout(1500)
        except Exception:
            pass
        try:
            return page.content()
        except Exception:
            return ""

    try:
        with timed("embed:browser:resolve"):
            content = browser_page(
                _work,
                user_agent=USER_AGENT,
                viewport={"width": 1280, "height": 720},
                init_script=(
                    "Object.defineProperty(navigator,'webdriver',{get:()=>undefined})"
                ),
                timeout=12.0,
                cancel_event=cancel_event,
            ) or ""
    except Exception:
        content = ""

    if found:
        return clean(found[0])
    return extract_media_url(content)


def resolve_embed(embed_url: str, referer: Optional[str] = None, cancel_event=None) -> Dict:
    """Resolve an embed URL to a playable stream dict.

    Returns ``{"stream_url": ..., "headers": {...}}`` or ``{"stream_url": None, "headers": {}}``.
    """
    url = embed_url
    if url.startswith("//"):
        url = "https:" + url
    try:
        import httpx
        r = httpx.get(
            url,
            headers={
                "User-Agent": USER_AGENT,
                "Referer": referer or url,
                "Accept": "*/*",
            },
            timeout=httpx.Timeout(_HTTP_TIMEOUT, connect=_CONNECT_TIMEOUT),
            follow_redirects=True,
        )
        if r.status_code == 200:
            media = extract_media_url(r.text)
            if media:
                return {
                    "stream_url": media,
                    "headers": {"Referer": referer or r.url, "User-Agent": USER_AGENT},
                }
    except Exception:
        pass

    if cancel_event is not None and cancel_event.is_set():
        return {"stream_url": None, "headers": {}}
    media = _resolve_via_browser(url, referer or url, cancel_event=cancel_event)
    if media:
        return {
            "stream_url": media,
            "headers": {"Referer": referer or url, "User-Agent": USER_AGENT},
        }
    return {"stream_url": None, "headers": {}}


def _safe_resolve(resolver: Callable[[str], str], url: str) -> str:
    try:
        return resolver(url) or ""
    except Exception:
        return ""


def probe_embeds(
    embed_urls: List[str],
    referer: Optional[str] = None,
    resolver: Optional[Callable[[str], str]] = None,
    timeout: float = _HTTP_TIMEOUT,
    max_workers: int = 6,
    cancel_event=None,
) -> str:
    """Probe several embed/source URLs in parallel, return the first playable URL.

    Each ``embed_urls`` entry is resolved concurrently (up to ``max_workers``);
    the first result that passes :func:`_is_media_url` wins and is returned
    immediately. ``resolver`` is a ``(url) -> stream_url`` callable returning
    "" on failure; when omitted each URL goes through :func:`resolve_embed`
    (``referer`` is forwarded). The entire probe is bounded by ``timeout``
    seconds via ``as_completed`` so slow hosters (browser/CF fallbacks) never
    stall the caller — you get the fastest winner, guaranteed return within
    ``timeout``. ``cancel_event`` (optional) aborts still-pending probes as
    soon as it is set (a winner has been found, or the resolution was aborted).

    Returns "" when nothing playable surfaces before the deadline.
    """
    if not embed_urls:
        return ""

    def default_resolver(url: str) -> str:
        result = resolve_embed(url, referer=referer, cancel_event=cancel_event)
        return (result or {}).get("stream_url") or ""

    resolver = resolver or default_resolver

    def cancellable(url: str) -> str:
        if cancel_event is not None and cancel_event.is_set():
            return ""
        return _safe_resolve(resolver, url)

    executor = ThreadPoolExecutor(max_workers=max(max_workers, 1))
    try:
        futures = {
            executor.submit(cancellable, u): u for u in embed_urls
        }
        try:
            # as_completed yields the fastest winner as it arrives; its own
            # timeout guarantees a return even when EVERY worker hangs (the
            # deadline is measured across the whole iteration, not per future).
            for fut in as_completed(futures, timeout=timeout):
                try:
                    url = fut.result() or ""
                except Exception:
                    continue
                if url and _is_media_url(url):
                    return url
        except Exception:
            pass
    finally:
        executor.shutdown(wait=False, cancel_futures=True)
    return ""