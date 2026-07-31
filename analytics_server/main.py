import os
from typing import Any, Dict, Optional

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
