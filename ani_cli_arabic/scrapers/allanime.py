"""AllAnime (allanime.* / allmanga.to) scraper.

Search and episode-listing use AllAnime's public GraphQL API (verified working).
Episode source URLs are gated behind the AllAnime ``client-crypto`` AES-GCM
handshake. This implementation performs the full handshake in pure Python:

1. ``aa-boot`` HMAC token from the build-mask (``x-aa-boot`` header).
2. Fresh bootstrap via ``GET client-crypto/v1/bootstrap`` -> ``partB`` + ``epoch``.
3. AES-256-GCM key = ``partB XOR mask``.
4. ``aaReq`` = base64( 0x01 || iv[12] || aes_gcm(payload) || tag[16] ) where
   the payload is ``{v, ts, epoch, buildId, qh, k}`` and the IV is the first
   12 bytes of ``SHA-256(epoch:buildId:qh:ts:k)``.
5. GraphQL POST with the site's exact episode query text, ``extensions``
   carrying ``persistedQuery`` + ``aaReq``, plus the ``x-build-id`` header.
6. Decrypt the returned ``tobeparsed`` blob (AES-256-GCM, tag in last 16
   bytes) to recover the real ``sourceUrls`` JSON.

``--``-prefixed sourceUrls are AllAnime's hex remap obfuscation pointing at the
``/apivtwo/clock`` resolver; they are decoded via the mapping table but only
kept if they resolve to a playable URL.
"""
import base64
import hashlib
import hmac
import json
import sys
import time
from typing import Dict, List, Optional

import httpx

from .base import BaseScraper
from . import embeds

# Build id baked into the mkissa/allanime web client.
BUILD_ID = "81"
# Content lane for episodes (the ``k`` query param on the bootstrap endpoint).
_LANE = "k7"
# 32-byte mask derived from the client bundle for build 81 (see `ev("81")`).
_MASK = bytes.fromhex(
    "1c51425b45d71a76c58adb6b52fe3e766d615bb48a252327b7c74323ea37658b"
)
# Epoch length and switch-window used to compute the signed epoch.
_EPOCH_MS = 259200000
_SWITCH_MS = 86400000

API_BASE = "https://api.mkissa.net/api"
BOOTSTRAP_URL = "https://api.mkissa.net/client-crypto/v1/bootstrap?buildId=81&k=k7"
REFERRER = "https://mkissa.to"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "Chrome/124.0.0.0 Safari/537.36"
)

# The site's exact episode query text. The persisted-query hash the API accepts
# is the SHA-256 of this exact text (used both as ``extensions.persistedQuery.
# sha256Hash`` and as the ``qh`` inside the ``aaReq`` payload).
_EPISODE_QUERY = """\
query(
$showId: String!
$translationType: VaildTranslationTypeEnumType!
$episodeString: String!
) {
episode(
showId: $showId
translationType: $translationType
episodeString: $episodeString
) {
episodeString
uploadDate
sourceUrls
thumbnail
notes
show{


_id
name
englishName
nativeName
slugTime

thumbnail

tbObj {
  u
  sm
  md
  ts
}

lastEpisodeInfo
lastEpisodeDate
type
season
score
airedStart
availableEpisodes
episodeDuration
episodeCount
# lastUpdateStart
lastUpdateEnd
characterCount

description
broadcastInterval
banner
characters
availableEpisodesDetail
nameOnlyString
characters
isAdult
relatedShows
relatedMangas
altNames
disqusIds
}
pageStatus{
_id
notes
pageId
showId

views
userScoreCount
userScoreAverValue
likesCount
commentCount
dislikesCount
boostsCount
reviewCount

}
episodeInfo{
notes
thumbnails

tbObj {
  u
  sm
  md
  ts
}

vidInforssub
uploadDates
vidInforsdub
vidInforsraw
description
}
versionFix
}
}
"""

_EPISODE_QUERY_HASH = hashlib.sha256(_EPISODE_QUERY.encode()).hexdigest()

_SEARCH_QUERY = """\
query($search: SearchInput $limit: Int $page: Int \
$translationType: VaildTranslationTypeEnumType \
$countryOrigin: VaildCountryOriginEnumType) { \
shows(search: $search limit: $limit page: $page \
translationType: $translationType \
countryOrigin: $countryOrigin) { \
edges { _id name availableEpisodes __typename } } }"""

_SHOW_QUERY = """\
query ($showId: String!) { show( _id: $showId ) { \
_id name availableEpisodesDetail availableEpisodes }}"""

_CLIENT = httpx.Client(
    headers={"User-Agent": USER_AGENT, "Referer": REFERRER, "Content-Type": "application/json"},
    timeout=httpx.Timeout(30.0, connect=8.0),
)

# AllAnime's `--` hex remap: each two-hex-digit value maps to one output char.
_HEX_REMAP = {
    "79": "A", "7a": "B", "7b": "C", "7c": "D", "7d": "E", "7e": "F", "7f": "G",
    "70": "H", "71": "I", "72": "J", "73": "K", "74": "L", "75": "M", "76": "N",
    "77": "O", "68": "P", "69": "Q", "6a": "R", "6b": "S", "6c": "T", "6d": "U",
    "6e": "V", "6f": "W", "60": "X", "61": "Y", "62": "Z",
    "59": "a", "5a": "b", "5b": "c", "5c": "d", "5d": "e", "5e": "f", "5f": "g",
    "50": "h", "51": "i", "52": "j", "53": "k", "54": "l", "55": "m", "56": "n",
    "57": "o", "48": "p", "49": "q", "4a": "r", "4b": "s", "4c": "t", "4d": "u",
    "4e": "v", "4f": "w", "40": "x", "41": "y", "42": "z",
    "08": "0", "09": "1", "0a": "2", "0b": "3", "0c": "4", "0d": "5", "0e": "6",
    "0f": "7", "00": "8", "01": "9",
    "15": "-", "16": ".", "67": "_", "46": "~", "02": ":", "17": "/", "07": "?",
    "1b": "#", "63": "[", "65": "]", "78": "@", "19": "!", "1c": "$", "1e": "&",
    "10": "(", "11": ")", "12": "*", "13": "+", "14": ",", "03": ";", "05": "=",
    "1d": "%",
}


def _epoch_for(now_ms: int) -> int:
    """Current epoch id; near the switch boundary the client uses epoch - 1."""
    t = now_ms // _EPOCH_MS
    if t > 0 and now_ms - t * _EPOCH_MS < _SWITCH_MS:
        return t - 1
    return t


def _boot_token(mask: bytes, epoch: int) -> str:
    """Compute the ``x-aa-boot`` header (HMAC chain over the build mask)."""
    h = hmac.new(mask, b"aa-boot:" + BUILD_ID.encode(), hashlib.sha256).digest()
    payload = f"{BUILD_ID}:mkissa:mkissa.to:{epoch}:{_LANE}".encode()
    return hmac.new(h, payload, hashlib.sha256).hexdigest()


def _derive_key(part_b: str) -> bytes:
    """AES-256 key = ``partB`` XOR build mask."""
    pb = base64.b64decode(part_b)
    return bytes(a ^ b for a, b in zip(pb, _MASK))


def _make_aa_req(key: bytes, epoch: int, qh: str, ts: int) -> str:
    """Encrypt the ``aaReq`` payload; WebCrypto layout (ct+tag appended)."""
    payload = json.dumps(
        {"v": 1, "ts": ts, "epoch": epoch, "buildId": BUILD_ID, "qh": qh, "k": _LANE},
        separators=(",", ":"),
    ).encode()
    iv = hashlib.sha256(f"{epoch}:{BUILD_ID}:{qh}:{ts}:{_LANE}".encode()).digest()[:12]
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    enc = Cipher(algorithms.AES(key), modes.GCM(iv)).encryptor()
    ct = enc.update(payload) + enc.finalize()
    return base64.b64encode(b"\x01" + iv + ct + enc.tag).decode()


def _decrypt_tobeparsed(tobeparsed: str, key: bytes) -> Optional[Dict]:
    """Decrypt the ``tobeparsed`` blob (prefix[1] || iv[12] || ct || tag[16])."""
    try:
        raw = base64.b64decode(tobeparsed)
        if len(raw) < 1 + 12 + 16:
            return None
        iv, tag, ct = raw[1:13], raw[-16:], raw[13:-16]
        from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
        dec = Cipher(algorithms.AES(key), modes.GCM(iv, tag)).decryptor()
        return json.loads((dec.update(ct) + dec.finalize()).decode("utf-8"))
    except Exception:
        return None


def _decode_obfuscated(url: str) -> str:
    """Decode a ``--``-prefixed sourceUrl via the hex remap table."""
    if not url.startswith("--"):
        return url
    hexed = url[2:]
    out = []
    for i in range(0, len(hexed) - 1, 2):
        out.append(_HEX_REMAP.get(hexed[i:i + 2].lower(), "?"))
    return "".join(out)


class AniThemeScraper(BaseScraper):
    """AllAnime GraphQL scraper with the client-crypto episode handshake."""

    @property
    def name(self) -> str:
        return "allanime"

    def _bootstrap(self) -> Optional[dict]:
        try:
            epoch = _epoch_for(int(time.time() * 1000))
            r = _CLIENT.get(
                BOOTSTRAP_URL,
                headers={
                    "x-build-id": BUILD_ID,
                    "x-aa-boot": _boot_token(_MASK, epoch),
                    "Origin": REFERRER,
                    "Referer": REFERRER + "/",
                },
            )
            if r.status_code != 200:
                return None
            data = r.json()
            if data.get("partB") and data.get("epoch") is not None:
                return data
            return None
        except Exception:
            return None

    def _post(self, query: str, variables: dict, aa: Optional[dict] = None) -> Optional[dict]:
        payload = {"query": query, "variables": variables}
        if aa is not None:
            payload["extensions"] = aa["extensions"]
        try:
            r = _CLIENT.post(API_BASE, json=payload, headers={"x-build-id": BUILD_ID})
            if r.status_code != 200:
                return None
            return r.json()
        except Exception:
            return None

    def search(self, query: str) -> List[Dict]:
        data = self._post(_SEARCH_QUERY, {
            "search": {"allowAdult": False, "allowUnknown": False, "query": query},
            "limit": 20,
            "page": 1,
            "translationType": "sub",
            "countryOrigin": "ALL",
        })
        if not data:
            return []
        edges = data.get("data", {}).get("shows", {}).get("edges", [])
        return [
            {"title": e.get("name", ""), "id": e.get("_id", "")}
            for e in edges if e.get("_id")
        ]

    def get_episodes(self, anime_id: str) -> List[Dict]:
        data = self._post(_SHOW_QUERY, {"showId": anime_id})
        if not data:
            return []
        show = data.get("data", {}).get("show", {}) or {}
        detail = show.get("availableEpisodesDetail") or {}
        eps = detail.get("sub") or detail.get("dub") or []
        out = []
        for e in eps:
            try:
                out.append({"episode_num": float(e), "id": f"{anime_id}/{e}"})
            except (TypeError, ValueError):
                continue
        return out

    def get_stream_url(self, episode_id: str) -> Dict:
        parts = episode_id.split("/", 1)
        show_id = parts[0]
        ep_str = parts[1] if len(parts) > 1 else "1"

        boot = self._bootstrap()
        if not boot:
            return {"stream_url": None, "headers": {}}

        key = _derive_key(boot["partB"])
        now_ms = int(time.time() * 1000)
        ts = (now_ms // 300000) * 300000
        aa_req = _make_aa_req(key, boot["epoch"], _EPISODE_QUERY_HASH, ts)

        aa = {
            "extensions": {
                "persistedQuery": {"version": 1, "sha256Hash": _EPISODE_QUERY_HASH},
                "k": _LANE,
                "aaReq": aa_req,
            }
        }
        data = self._post(_EPISODE_QUERY, {
            "showId": show_id,
            "translationType": "sub",
            "episodeString": ep_str,
        }, aa=aa)
        if not data:
            return {"stream_url": None, "headers": {}}

        # AA_CRYPTO_* error ladder: none should surface if bootstrap is fresh.
        errors = data.get("errors") or []
        if errors:
            return {"stream_url": None, "headers": {}}

        tobeparsed = (data.get("data") or {}).get("tobeparsed")
        if tobeparsed:
            decrypted = _decrypt_tobeparsed(tobeparsed, key)
            if not decrypted:
                return {"stream_url": None, "headers": {}}
            ep = decrypted.get("episode") or {}
            urls = ep.get("sourceUrls") or []
        else:
            ep = (data.get("data") or {}).get("episode") or {}
            urls = ep.get("sourceUrls") or []

        direct = []
        embeds_srcs = []
        for u in urls:
            src = _decode_obfuscated(u.get("sourceUrl") or "").strip()
            if not src:
                continue
            if src.startswith("http") and (".m3u8" in src or ".mp4" in src):
                direct.append(src)
            elif src.startswith("http"):
                embeds_srcs.append(src)
            elif src.startswith("/apivtwo/clock"):
                # AllAnime clock resolver: try the api host directly.
                embeds_srcs.append("https://api.mkissa.net" + src)

        if direct:
            return {
                "stream_url": direct[0],
                "headers": {"Referer": REFERRER, "User-Agent": USER_AGENT},
            }

        for src in embeds_srcs:
            resolved = embeds.resolve_embed(src, referer=REFERRER)
            if resolved.get("stream_url"):
                return resolved
        return {"stream_url": None, "headers": {}}
