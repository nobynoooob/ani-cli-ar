import re
import base64
import sys
from typing import Dict, List

import httpx

from .base import BaseScraper
from .embeds import resolve_embed

BASE_URL = "https://gogoanime.co.za"
USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/126.0.0.0 Safari/537.36"
)

_CLIENT = httpx.Client(
    headers={"User-Agent": USER_AGENT, "Referer": BASE_URL + "/"},
    timeout=httpx.Timeout(8.0, connect=5.0),
    follow_redirects=True,
)


def _title_to_slug(title: str) -> str:
    slug = title.lower().strip()
    slug = re.sub(r'[^a-z0-9\s-]', "", slug)
    slug = re.sub(r"[\s]+", "-", slug)
    slug = re.sub(r"-+", "-", slug)
    return slug.strip("-")


def _extract_video(html: str) -> str:
    for pat in [
        re.compile(r'(https?://[^"\'<>\s]+\.(?:mp4|m3u8)[^"\'<>\s]*)'),
        re.compile(r'file["\']?\s*[:=]\s*["\']([^"\']+)'),
    ]:
        for m in pat.findall(html):
            m = m.strip().rstrip('"').rstrip("'")
            if m.startswith("http") and (".m3u8" in m or ".mp4" in m):
                return m
    return ""


def _extract_embeds(html: str) -> list:
    seen = set()
    embeds = []
    for m in re.finditer(r'<iframe[^>]*src=["\']((?:https?://)[^"\']+)["\']', html, re.IGNORECASE):
        url = m.group(1).strip()
        if url not in seen:
            seen.add(url)
            embeds.append(url)
    for dh in re.findall(r'data-hash=["\']([^"\']+)["\']', html):
        try:
            decoded = base64.b64decode(dh).decode("utf-8", errors="replace")
            for m in re.finditer(r'<iframe[^>]*src=["\']((?:https?://)[^"\']+)["\']', decoded, re.IGNORECASE):
                url = m.group(1).strip()
                if url not in seen:
                    seen.add(url)
                    embeds.append(url)
        except Exception:
            continue
    return embeds


def _resolve_vidwish(embed_url: str) -> str:
    """Resolve an embed (kwik/vidstreaming/gogo-server) to a playable URL."""
    result = resolve_embed(embed_url, referer=BASE_URL + "/")
    return result.get("stream_url") or ""


class GogoAnimeScraper(BaseScraper):

    @property
    def name(self) -> str:
        return "gogoanime"

    def search(self, query: str) -> List[Dict]:
        slug = _title_to_slug(query)
        try:
            resp = _CLIENT.get(f"{BASE_URL}/category/{slug}")
            if resp.status_code != 200 or len(resp.text) < 1000:
                return []
        except Exception:
            return []

        title_m = re.search(
            r'<h1[^>]*class=["\']?[^"\']*title[^"\']*["\']?>([^<]+)</h1>',
            resp.text,
        )
        name = title_m.group(1).strip() if title_m else slug

        ep_nums = sorted(set(
            float(e) for e in re.findall(rf"{slug}-episode-(\d+(?:\.\d+)?)", resp.text)
        ))
        if ep_nums:
            return [{"title": name, "id": slug}]

        # Some modern mirrors only render the total episode count in the
        # episode_page widget / AJAX list, not inline, so also accept the slug
        # being present as a valid "found" title.
        if slug in resp.text:
            return [{"title": name, "id": slug}]
        return []

    def get_episodes(self, anime_id: str) -> List[Dict]:
        try:
            resp = _CLIENT.get(f"{BASE_URL}/category/{anime_id}")
        except Exception:
            return []
        # Discover the live episode host (e.g. gogoanime.com.ro) from hrefs.
        hosts = set()
        for m in re.finditer(r'href="(https?://[^"]*-episode-\d+(?:\.\d+)?/)"', resp.text):
            href = m.group(1)
            from urllib.parse import urlparse
            hosts.add(f"{urlparse(href).scheme}://{urlparse(href).netloc}")
        host = next(iter(hosts)) if hosts else BASE_URL

        nums = sorted(set(
            float(e) for e in re.findall(rf"{anime_id}-episode-(\d+(?:\.\d+)?)", resp.text)
        ))
        return [
            {"episode_num": n, "id": f"{host}/{anime_id}/{n}"}
            for n in nums
        ]

    def get_stream_url(self, episode_id: str) -> Dict:
        # episode_id is a full URL: {scheme}://{host}/{show_id}/{episode_num}
        from urllib.parse import urlparse
        try:
            parsed = urlparse(episode_id)
            netloc = parsed.netloc or BASE_URL.replace("https://", "")
            path_parts = [p for p in parsed.path.split("/") if p]
            if len(path_parts) < 2:
                return {"stream_url": None, "headers": {}}
            show_id = path_parts[0]
            ep_str = str(int(float(path_parts[1])))
        except (ValueError, TypeError):
            return {"stream_url": None, "headers": {}}
        url = f"{parsed.scheme}://{netloc}/{show_id}-episode-{ep_str}/"

        try:
            resp = _CLIENT.get(url)
            if resp.status_code != 200:
                return {"stream_url": None, "headers": {}}
        except Exception:
            return {"stream_url": None, "headers": {}}

        embed_urls = _extract_embeds(resp.text)
        for embed_url in embed_urls:
            video = _resolve_vidwish(embed_url)
            if video:
                return {
                    "stream_url": video,
                    "headers": {"Referer": url, "User-Agent": USER_AGENT},
                }

        return {"stream_url": None, "headers": {}}
