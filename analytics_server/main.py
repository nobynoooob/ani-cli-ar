import os
from collections import Counter
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, Optional

try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))
except ImportError:
    pass

import requests
from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel
from supabase import create_client, Client


class MonitorPayload(BaseModel):
    fingerprint: str
    timestamp: str
    action: str
    details: Dict[str, Any] = {}


app = FastAPI(title="ani-cli-arabic Analytics Server")

SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "")
AUTH_KEY = os.environ.get("ANALYTICS_AUTH_KEY", "")
TABLE_NAME = os.environ.get("ANALYTICS_TABLE", "usage_logs")

_supabase: Optional[Client] = None


def get_supabase() -> Client:
    global _supabase
    if _supabase is None:
        if not SUPABASE_URL or not SUPABASE_KEY:
            raise RuntimeError("SUPABASE_URL and SUPABASE_KEY must be set")
        _supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
    return _supabase


def _check_auth(x_auth_key: Optional[str]) -> None:
    if not AUTH_KEY:
        return
    if not x_auth_key or x_auth_key != AUTH_KEY:
        raise HTTPException(status_code=401, detail="Invalid or missing X-Auth-Key")


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/monitor")
def monitor(
    payload: MonitorPayload,
    x_auth_key: Optional[str] = Header(default=None),
):
    _check_auth(x_auth_key)

    row = {
        "fingerprint": payload.fingerprint,
        "timestamp": payload.timestamp,
        "action": payload.action,
        "details": payload.details,
    }

    try:
        result = get_supabase().table(TABLE_NAME).insert(row).execute()
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to insert: {exc}")

    return {"status": "ok", "inserted": len(result.data) if result.data else 0}


@app.get("/stats")
def stats(
    fingerprint: str = "",
    limit: int = 500,
    x_auth_key: Optional[str] = Header(default=None),
):
    """Return an aggregated streaming-history summary for a fingerprint."""
    _check_auth(x_auth_key)
    limit = max(1, min(int(limit), 2000))

    try:
        headers = {
            "apikey": SUPABASE_KEY,
            "Authorization": f"Bearer {SUPABASE_KEY}",
        }
        params = {
            "action": "eq.video_play",
            "select": "timestamp,details",
            "order": "timestamp.desc",
            "limit": str(limit),
        }
        if fingerprint:
            params["fingerprint"] = f"eq.{fingerprint}"
            headers["x-fingerprint"] = fingerprint

        resp = requests.get(
            f"{SUPABASE_URL}/rest/v1/{TABLE_NAME}",
            params=params,
            headers=headers,
            timeout=10,
        )
        resp.raise_for_status()
        rows = resp.json()
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Failed to query telemetry: {exc}")

    if not isinstance(rows, list):
        raise HTTPException(status_code=502, detail="Unexpected response from telemetry store")

    total = len(rows)
    titles = Counter()
    players = Counter()
    providers = Counter()
    qualities = Counter()
    recent_7d = 0
    cutoff = datetime.now(timezone.utc) - timedelta(days=7)
    last_played: Optional[datetime] = None
    last_title = None
    last_episode = None

    for row in rows:
        details = row.get("details") or {}
        if not isinstance(details, dict):
            details = {}

        title = str(details.get("anime") or "Unknown")
        episode = str(details.get("episode") or "")
        titles[title] += 1
        players[str(details.get("player") or "unknown")] += 1
        providers[str(details.get("provider") or "unknown")] += 1
        qualities[str(details.get("quality") or "unknown")] += 1

        ts = row.get("timestamp")
        if ts:
            try:
                dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                if dt > cutoff:
                    recent_7d += 1
                if last_played is None or dt > last_played:
                    last_played = dt
                    last_title = title
                    last_episode = episode
            except Exception:
                pass

    return {
        "source": "remote",
        "fingerprint": fingerprint,
        "total_plays": total,
        "unique_titles": len(titles),
        "recent_7d": recent_7d,
        "last_played": last_played.isoformat() if last_played else None,
        "last_title": last_title,
        "last_episode": last_episode,
        "top_titles": [
            {"title": title, "count": count}
            for title, count in titles.most_common(10)
        ],
        "by_player": dict(players),
        "by_provider": dict(providers),
        "by_quality": dict(qualities),
    }
