import re
import base64
import sys
from typing import Dict, List

import httpx

from .base import BaseScraper

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
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return ""

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=["--disable-blink-features=AutomationControlled", "--no-sandbox"],
        )
        ctx = browser.new_context(
            user_agent=USER_AGENT,
            viewport={"width": 1280, "height": 720},
        )
        ctx.add_init_script(
            "Object.defineProperty(navigator,'webdriver',{get:()=>undefined})"
        )
        page = ctx.new_page()
        found = []

        def on_response(resp):
            url = resp.url
            if ".m3u8" in url and url not in found:
                found.append(url)

        page.on("response", on_response)

        def block_route(route):
            rt = route.request.resource_type
            if rt in ["image", "font", "stylesheet", "ping"]:
                route.abort()
            else:
                route.continue_()

        page.route("**/*", block_route)

        try:
            page.goto(embed_url, wait_until="commit", timeout=10000)
            page.wait_for_timeout(8000)
        except Exception:
            pass

        browser.close()

    return found[0] if found else ""


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
        if not ep_nums:
            return []
        return [{"title": name, "id": slug}]

    def get_episodes(self, anime_id: str) -> List[Dict]:
        try:
            resp = _CLIENT.get(f"{BASE_URL}/category/{anime_id}")
        except Exception:
            return []
        nums = sorted(set(
            float(e) for e in re.findall(rf"{anime_id}-episode-(\d+(?:\.\d+)?)", resp.text)
        ))
        return [
            {"episode_num": n, "id": f"{anime_id}/{n}"}
            for n in nums
        ]

    def get_stream_url(self, episode_id: str) -> Dict:
        parts = episode_id.split("/", 1)
        show_id = parts[0]
        ep_str = str(int(float(parts[1])))
        url = f"{BASE_URL}/{show_id}-episode-{ep_str}-english-subbed/"

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
                    "headers": {"Referer": embed_url, "User-Agent": USER_AGENT},
                }

        return {"stream_url": None, "headers": {}}
