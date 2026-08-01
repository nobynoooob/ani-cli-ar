import platform
import hashlib
import sys
import threading
import traceback as _traceback
from typing import Optional
from urllib.parse import urlsplit
import requests
from datetime import datetime, timezone
from .api import _get_analytics_endpoint_config
from .config import CURRENT_VERSION

_MAX_TRACEBACK_LINES = 6

class MonitoringSystem:
    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(MonitoringSystem, cls).__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if hasattr(self, '_initialized') and self._initialized:
            return
        self._initialized = True
        self.user_fingerprint = self._generate_fingerprint()

    def _generate_fingerprint(self) -> str:
        try:
            components = [
                platform.node(),
                platform.machine(),
                platform.system(),
                platform.release(),
                platform.processor()
            ]
            
            raw_str = "|".join(str(c) for c in components)
            return hashlib.sha256(raw_str.encode()).hexdigest()[:16]
        except Exception:
            return "unknown_user"

    def _send_data(self, action: str, details: dict):
        """Send analytics data only if user has opted in."""
        try:
            from .settings import SettingsManager
            settings = SettingsManager()
            if not settings.get('analytics'):
                return
        except Exception:
            return
        
        def worker():
            try:
                endpoint_url, auth_secret = _get_analytics_endpoint_config()
                
                payload = {
                    "fingerprint": self.user_fingerprint,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "action": action,
                    "details": details
                }
                
                headers = {
                    'Content-Type': 'application/json',
                    'X-Auth-Key': auth_secret,
                    'User-Agent': 'AniCliAr-Monitor/1.0'
                }
                
                requests.post(
                    f"{endpoint_url}/monitor", 
                    json=payload, 
                    headers=headers, 
                    timeout=3
                )
            except Exception:
                pass

        thread = threading.Thread(target=worker, daemon=True)
        thread.start()

    def track_app_start(self):
        self._send_data("app_start", {
            "version": CURRENT_VERSION,
            "os": platform.system()
        })

    def track_video_play(self, anime_title: str, episode: str, mode: str = "stream", player: str = "", provider: str = "", quality: str = ""):
        self._send_data("video_play", {
            "anime": anime_title,
            "episode": episode,
            "mode": mode,
            "player": player or "",
            "provider": provider or "",
            "quality": quality or ""
        })

    @staticmethod
    def _host_of(url) -> str:
        try:
            return urlsplit(str(url)).hostname or str(url)
        except Exception:
            return str(url)

    @staticmethod
    def _truncate_traceback(formatted) -> str:
        try:
            joined = "".join(formatted).rstrip()
        except Exception:
            return str(formatted)
        lines = joined.splitlines()
        if len(lines) > _MAX_TRACEBACK_LINES:
            return "\n".join(lines[-_MAX_TRACEBACK_LINES:])
        return joined

    def track_error(self, error_msg: str = "", context: dict = None,
                    exception: BaseException = None, exc_info: tuple = None):
        """Report a diagnostic error event.

        All extraction is local and cheap; the network send happens on a
        background thread and fails silently when offline.
        """
        details: dict = {}

        exc_type = exc_val = exc_tb = None
        if exception is not None:
            exc_type = type(exception)
            exc_val = exception
            if getattr(exception, "__traceback__", None) is not None:
                exc_tb = exception.__traceback__
        elif exc_info is not None and isinstance(exc_info, tuple) and exc_info[0] is not None:
            exc_type, exc_val, exc_tb = exc_info

        if exc_type is not None:
            details["exception_type"] = getattr(exc_type, "__name__", str(exc_type))
            details["error_msg"] = error_msg or str(exc_val) or ""
            if exc_tb is not None:
                try:
                    formatted = _traceback.format_exception(exc_type, exc_val, exc_tb)
                except Exception:
                    formatted = None
                if formatted:
                    details["traceback"] = self._truncate_traceback(formatted)
        else:
            details["error_msg"] = error_msg or ""

        if isinstance(context, dict):
            for key, value in context.items():
                if value is not None and str(value) != "":
                    details[key] = value

        if exc_val is not None:
            if details.get("http_status") is None:
                status = getattr(exc_val, "status_code", None)
                if status is None and getattr(exc_val, "response", None) is not None:
                    status = exc_val.response.status_code
                if status:
                    details["http_status"] = int(status)

            if not details.get("server_url") and not details.get("stream_url"):
                url = getattr(exc_val, "url", None)
                if url is None and getattr(exc_val, "request", None) is not None:
                    url = exc_val.request.url
                if url:
                    details["server_url"] = self._host_of(url)

        self._send_data("error", details)

    def fetch_stats(self, limit: int = 500) -> Optional[dict]:
        """Fetch aggregated streaming history from the remote telemetry endpoint.

        Returns None if analytics are disabled, the endpoint is unreachable, or
        no playback data is available for this device.
        """
        try:
            from .settings import SettingsManager
            settings = SettingsManager()
            if not settings.get('analytics'):
                return None
        except Exception:
            return None

        try:
            endpoint_url, auth_secret = _get_analytics_endpoint_config()

            headers = {
                'X-Auth-Key': auth_secret,
                'User-Agent': 'AniCliAr-Monitor/1.0'
            }

            resp = requests.get(
                f"{endpoint_url}/stats",
                params={"fingerprint": self.user_fingerprint, "limit": limit},
                headers=headers,
                timeout=8,
            )
            if resp.status_code != 200:
                return None
            data = resp.json()
            return data if isinstance(data, dict) else None
        except Exception:
            return None

# Global instance
monitor = MonitoringSystem()
