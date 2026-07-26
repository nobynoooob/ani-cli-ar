import json
import re
import time
import hashlib
import base64
from typing import Optional
from urllib.parse import urljoin

import requests
from Cryptodome.Cipher import AES

ALLANIME_CDN = "https://cdn.mkissa.net/all/mk/_app/immutable"
ALLANIME_REFR = "https://mkissa.to"
ALLANIME_BASE = "allanime.day"
ALLANIME_API = "https://api.mkissa.net"
ALLANIME_QUERY_HASH = "f4662f4b7510b26795dd53ef824a0bf1740fbbc5d1273fab18222ac831bca8d0"
KEYGEN_URL = "https://raw.githubusercontent.com/sdaqo/anipy-cli/refs/heads/key-gen/scripts/keygen/keygen.json"
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:150.0) Gecko/20100101 Firefox/150.0"

PROVIDER_PRIORITY = ["Yt-mp4", "S-Mp4", "Uv-mp4", "Ak", "Default"]

_HEX_MAP = {
    "79": "A", "7a": "B", "7b": "C", "7c": "D", "7d": "E", "7e": "F", "7f": "G",
    "70": "H", "71": "I", "72": "J", "73": "K", "74": "L", "75": "M", "76": "N", "77": "O",
    "68": "P", "69": "Q", "6a": "R", "6b": "S", "6c": "T", "6d": "U", "6e": "V", "6f": "W",
    "60": "X", "61": "Y", "62": "Z",
    "59": "a", "5a": "b", "5b": "c", "5c": "d", "5d": "e", "5e": "f", "5f": "g",
    "50": "h", "51": "i", "52": "j", "53": "k", "54": "l", "55": "m", "56": "n", "57": "o",
    "48": "p", "49": "q", "4a": "r", "4b": "s", "4c": "t", "4d": "u", "4e": "v", "4f": "w",
    "40": "x", "41": "y", "42": "z",
    "08": "0", "09": "1", "0a": "2", "0b": "3", "0c": "4", "0d": "5", "0e": "6", "0f": "7",
    "00": "8", "01": "9",
    "15": "-", "16": ".", "67": "_", "46": "~", "02": ":", "17": "/", "07": "?",
    "1b": "#", "63": "[", "65": "]", "78": "@", "19": "!", "1c": "$", "1e": "&",
    "10": "(", "11": ")", "12": "*", "13": "+", "14": ",", "03": ";", "05": "=", "1d": "%",
}

def _decode_hex_url(encoded: str) -> str:
    if not encoded.startswith("--"):
        return encoded
    hex_str = encoded[2:]
    out = []
    for i in range(0, len(hex_str), 2):
        pair = hex_str[i:i+2]
        out.append(_HEX_MAP.get(pair, "?"))
    result = "".join(out)
    result = result.replace("/clock", "/clock.json")
    return result


class AllAnimeScraper:
    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": USER_AGENT})
        self._aes_key: Optional[str] = None
        self._epoch: Optional[int] = None
        self._static_key: Optional[str] = None

    # ------------------------------------------------------------------ #
    #  Key derivation – replicates bash fetch_keys() + keygen fallback   #
    # ------------------------------------------------------------------ #

    def _fetch_keys_from_page(self):
        html = self.session.get(ALLANIME_REFR, timeout=15).text

        m = re.search(r'"epoch":(\d+)', html)
        if not m:
            raise RuntimeError("epoch not found on page")
        self._epoch = int(m.group(1))

        m = re.search(r'"partB":"([^"]*)"', html)
        if not m:
            raise RuntimeError("partB not found on page")
        part_b_b64 = m.group(1)

        m = re.search(rf'{re.escape(ALLANIME_CDN)}/entry/app\.[A-Za-z0-9_.-]+\.js', html)
        if not m:
            raise RuntimeError("app.js URL not found on page")
        app_js_url = m.group(0)

        app_js = self.session.get(app_js_url, timeout=15).text
        chunk_refs = re.findall(r'"[.][.]/chunks/[A-Za-z0-9_.-]+\.js"', app_js)
        js_parts = []
        for ref in chunk_refs[:5]:
            url = f"{ALLANIME_CDN}/{ref.strip('\"').lstrip('../')}"
            try:
                js_parts.append(self.session.get(url, timeout=15).text)
            except requests.RequestException:
                pass
        js_body = "".join(js_parts)

        m = re.search(r'[0-9a-f]{64}', js_body)
        if not m:
            raise RuntimeError("mask hex not found in JS chunks")
        aa_mask_hex = m.group(0)

        part_b_hex = base64.b64decode(part_b_b64).hex()
        key_hex = ""
        for i in range(0, 128, 2):
            m_byte = int(aa_mask_hex[i:i+2], 16)
            p_byte = int(part_b_hex[i:i+2], 16)
            key_hex += f"{m_byte ^ p_byte:02x}"
        self._aes_key = key_hex

    def _fetch_keys_from_keygen(self):
        resp = requests.get(KEYGEN_URL, timeout=15)
        resp.raise_for_status()
        kg = resp.json()
        self._epoch = kg["epoch"]
        self._aes_key = kg["key"]
        self._static_key = kg.get("static_key")

    def _ensure_keys(self):
        if self._aes_key is not None:
            return
        try:
            self._fetch_keys_from_page()
        except Exception:
            self._fetch_keys_from_keygen()

    # ---------------------------------------------------------------- #
    #  aaReq token generation – matches bash get_aa_req()               #
    # ---------------------------------------------------------------- #

    def _build_aa_req(self) -> str:
        self._ensure_keys()
        ts = int(time.time() * 1000) // 300000 * 300000
        payload = {"v": 1, "ts": ts, "epoch": self._epoch, "qh": ALLANIME_QUERY_HASH}
        iv = hashlib.sha256(
            f"{self._epoch}:{ALLANIME_QUERY_HASH}:{ts}".encode()
        ).digest()[:12]
        cipher = AES.new(bytes.fromhex(self._aes_key), AES.MODE_GCM, nonce=iv)
        ct, tag = cipher.encrypt_and_digest(
            json.dumps(payload, separators=(",", ":")).encode()
        )
        return base64.b64encode(b"\x01" + iv + ct + tag).decode()

    # ---------------------------------------------------------------- #
    #  tobeparsed decryption – matches bash process_tobeparsed()        #
    # ---------------------------------------------------------------- #

    def _decrypt_tobeparsed(self, tbp_b64: str):
        raw = base64.b64decode(tbp_b64)
        iv, ct, tag = raw[1:13], raw[13:-16], raw[-16:]
        candidates = [bytes.fromhex(self._aes_key)]
        if self._static_key:
            candidates.append(self._static_key.encode())
        for key in candidates:
            try:
                plain = AES.new(key, AES.MODE_GCM, nonce=iv).decrypt_and_verify(ct, tag)
                return json.loads(plain.decode("utf-8"))
            except (ValueError, KeyError):
                continue
        raise ValueError("tobeparsed could not be decrypted")

    # ---------------------------------------------------------------- #
    #  Public API – search / episodes / video                           #
    # ---------------------------------------------------------------- #

    def search(self, query: str, mode: str = "sub"):
        gql = (
            "query($search: SearchInput $limit: Int $page: Int "
            "$translationType: VaildTranslationTypeEnumType "
            "$countryOrigin: VaildCountryOriginEnumType) { "
            "shows(search: $search limit: $limit page: $page "
            "translationType: $translationType "
            "countryOrigin: $countryOrigin) { "
            "edges { _id name availableEpisodes airedStart __typename } } }"
        )
        resp = self.session.post(
            f"{ALLANIME_API}/api",
            json={
                "variables": {
                    "search": {"allowAdult": False, "allowUnknown": False, "query": query},
                    "limit": 40,
                    "page": 1,
                    "translationType": mode,
                    "countryOrigin": "ALL",
                },
                "query": gql,
            },
            headers={"Content-Type": "application/json", "Referer": ALLANIME_REFR},
            timeout=15,
        )
        data = resp.json()
        out = []
        for edge in data.get("data", {}).get("shows", {}).get("edges", []):
            out.append({
                "id": edge["_id"],
                "name": edge["name"],
                "available_episodes": edge.get("availableEpisodes", {}),
            })
        return out

    def get_episodes(self, show_id: str, mode: str = "sub"):
        gql = "query($showId: String!) { show(_id: $showId) { _id availableEpisodesDetail } }"
        resp = self.session.post(
            f"{ALLANIME_API}/api",
            json={"variables": {"showId": show_id}, "query": gql},
            headers={"Content-Type": "application/json", "Referer": ALLANIME_REFR},
            timeout=15,
        )
        data = resp.json()
        detail = data.get("data", {}).get("show", {}).get("availableEpisodesDetail", {})
        eps = detail.get(mode, detail.get("sub", []))
        return sorted(float(e) for e in eps)

    def get_video_sources(self, show_id: str, episode_num, mode: str = "sub"):
        aa_req = self._build_aa_req()
        variables = json.dumps({
            "showId": show_id,
            "translationType": mode,
            "episodeString": str(int(float(episode_num))),
        })
        extensions = json.dumps({
            "persistedQuery": {"version": 1, "sha256Hash": ALLANIME_QUERY_HASH},
            "aaReq": aa_req,
        })
        resp = self.session.get(
            f"{ALLANIME_API}/api",
            params={"variables": variables, "extensions": extensions},
            headers={"Referer": ALLANIME_REFR, "Origin": ALLANIME_REFR},
            timeout=15,
        )
        payload = resp.json()

        data = payload.get("data", {})
        if "tobeparsed" in data:
            try:
                data = self._decrypt_tobeparsed(data["tobeparsed"])
            except ValueError:
                return []

        sources = []
        episode = data.get("episode") if isinstance(data, dict) else None
        if episode is None:
            return sources

        for src in episode.get("sourceUrls", []):
            if src.get("sourceName") in PROVIDER_PRIORITY:
                sources.append({
                    "source_name": src["sourceName"],
                    "source_url": src["sourceUrl"],
                    "priority": src.get("priority", 0),
                    "type": src.get("type", ""),
                    "stype": src.get("stype", ""),
                })

        sources.sort(key=lambda x: -x["priority"])
        return sources

    def _fetch_provider_links(self, decoded_path: str) -> list:
        url = f"https://{ALLANIME_BASE}{decoded_path}"
        try:
            resp = self.session.get(
                url,
                headers={"Referer": ALLANIME_REFR, "User-Agent": USER_AGENT},
                timeout=15,
            )
            data = resp.json()
        except Exception:
            return []

        links = []
        for entry in data.get("links", []):
            raw = entry.get("rawUrls", {})
            vids = raw.get("vids", [])
            for vid in vids:
                vid_url = vid.get("url", "")
                height = vid.get("height", 0)
                links.append({
                    "url": vid_url,
                    "resolution": height,
                    "headers": {"Referer": ALLANIME_REFR, "User-Agent": USER_AGENT},
                })

            link = entry.get("link", "")
            if not vids and link:
                resolution = 0
                rs = entry.get("resolutionStr", "")
                if rs:
                    try:
                        resolution = int(rs.replace("p", "").split()[0])
                    except (ValueError, IndexError):
                        resolution = 0
                links.append({
                    "url": link,
                    "resolution": resolution,
                    "headers": {
                        "Referer": entry.get("headers", {}).get("Referer", ALLANIME_REFR),
                        "User-Agent": USER_AGENT,
                    },
                })
        return links

    def resolve_stream_url(self, show_id: str, episode_num, mode: str = "sub"):
        sources = self.get_video_sources(show_id, episode_num, mode)
        if not sources:
            return None, {}
        src = sources[0]
        headers = {"Referer": ALLANIME_REFR, "User-Agent": USER_AGENT}

        if "tools.fast4speed.rsvp" in src["source_url"]:
            return src["source_url"], headers

        if src.get("stype") == "t":
            return src["source_url"], headers

        decoded_path = _decode_hex_url(src["source_url"])
        provider_links = self._fetch_provider_links(decoded_path)
        if not provider_links:
            return src["source_url"], headers

        provider_links.sort(key=lambda x: -x["resolution"])
        best = provider_links[0]
        return best["url"], best["headers"]
