"""Stream-embed gateway extraction helpers shared across scrapers.

Supports modern embed hosts (kwik, vidstreaming, gogo server, doodstream,
filemoon, megacloud, streamtape). Some embeds require a real browser to pass
Cloudflare/JS challenges; `resolve_embed()` routes to Playwright when the
plain HTTP pass fails.
"""
import re
from typing import Dict, List, Optional

USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/126.0.0.0 Safari/537.36"
)

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
            if url and url not in candidates:
                candidates.append(url)

    # Prefer a clean HLS manifest, then any valid direct media link.
    m3u8 = [u for u in candidates if ".m3u8" in u.lower()]
    mp4 = [u for u in candidates if ".mp4" in u.lower()]
    for pick in (m3u8 or []) + (mp4 or []):
        if is_valid_stream_url(pick):
            return pick
    return ""


def _resolve_via_browser(embed_url: str, ref_url: str) -> str:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return ""

    found = []

    def maybe(elem):
        u = elem if isinstance(elem, str) else getattr(elem, "url", "")
        if isinstance(u, str) and (".m3u8" in u or ".mp4" in u) and u not in found:
            found.append(u)

    def clean(url: str) -> str:
        u = _unescape(url.strip().rstrip('"').rstrip("'"))
        return u if is_valid_stream_url(u) else ""

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-blink-features=AutomationControlled"],
        )
        ctx = browser.new_context(
            user_agent=USER_AGENT,
            viewport={"width": 1280, "height": 720},
        )
        ctx.add_init_script(
            "Object.defineProperty(navigator,'webdriver',{get:()=>undefined})"
        )
        page = ctx.new_page()
        page.on("request", maybe)
        page.on("response", maybe)
        page.route(
            "**/*",
            lambda r: r.abort()
            if r.request.resource_type in ("image", "font", "stylesheet", "ping")
            else r.continue_(),
        )
        try:
            page.goto(embed_url, wait_until="domcontentloaded", timeout=25000)
            page.wait_for_timeout(8000)
        except Exception:
            pass
        content = ""
        try:
            content = page.content()
        except Exception:
            pass
        browser.close()

    if found:
        return clean(found[0])
    return extract_media_url(content)


def resolve_embed(embed_url: str, referer: Optional[str] = None) -> Dict:
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
            timeout=httpx.Timeout(12.0, connect=8.0),
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

    media = _resolve_via_browser(url, referer or url)
    if media:
        return {
            "stream_url": media,
            "headers": {"Referer": referer or url, "User-Agent": USER_AGENT},
        }
    return {"stream_url": None, "headers": {}}