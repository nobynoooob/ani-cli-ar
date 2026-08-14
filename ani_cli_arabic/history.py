import json
import threading
from pathlib import Path
from datetime import datetime
from .storage import atomic_write_json

class HistoryManager:
    # Maximum history entries to maintain reasonable file size and load times
    MAX_HISTORY_SIZE = 100
    
    def __init__(self):
        self.history_file = self._get_history_path()
        self.history = self._load_history()
        self._lock = threading.RLock()

    def _get_history_path(self) -> Path:
        home_dir = Path.home()
        db_dir = home_dir / ".ani-cli-arabic" / "database"
        db_dir.mkdir(parents=True, exist_ok=True)
        return db_dir / "history.json"

    def _load_history(self) -> dict:
        if not self.history_file.exists():
            return {}
        try:
            with open(self.history_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
                if not isinstance(data, dict):
                    return {}
                return data
        except (json.JSONDecodeError, IOError, OSError):
            return {}

    def save_history(self):
        try:
            if len(self.history) > self.MAX_HISTORY_SIZE:
                bookmarks = self.history.pop(self._BOOKMARKS_KEY, {})
                sorted_items = sorted(
                    self.history.items(),
                    key=lambda x: x[1].get('last_updated', ''),
                    reverse=True
                )
                self.history = dict(sorted_items[:self.MAX_HISTORY_SIZE])
                if bookmarks:
                    self.history[self._BOOKMARKS_KEY] = bookmarks

            atomic_write_json(self.history_file, self.history, indent=4, ensure_ascii=False)
        except (IOError, OSError, ValueError, TypeError) as e:
            import sys
            print(f"Warning: Failed to save history: {e}", file=sys.stderr)

    def mark_watched(self, anime_id, episode_num, anime_title):
        with self._lock:
            self.history[str(anime_id)] = {
                'episode': str(episode_num),
                'title': anime_title,
                'last_updated': datetime.now().isoformat()
            }
        self.save_history()

    def record_progress(self, anime_id, episode_num, anime_title, poster="", position=None, total=None):
        """Record playback progress for continue-watching feature.
        Args:
            position: current playback position in seconds (float or None)
            total: total duration in seconds (float or None)
        Stores progress fraction (0..1) if both position and total are valid.
        """
        progress = None
        if position is not None and total is not None and total > 0:
            progress = max(0.0, min(position / total, 1.0))
        with self._lock:
            self.history[str(anime_id)] = {
                'episode': str(episode_num),
                'title': anime_title,
                'poster': poster,
                'position': position,
                'total': total,
                'progress': progress,
                'last_updated': datetime.now().isoformat()
            }
        self.save_history()

    def get_last_watched(self, anime_id):
        with self._lock:
            data = self.history.get(str(anime_id))
        if data:
            return data.get('episode')
        return None

    def get_history(self, limit=100):
        """Return watch history as a list (newest first) of
        ``{"anime_id", "title", "episode", "poster", "last_updated", ...}``."""
        with self._lock:
            items = []
            for anime_id, data in self.history.items():
                if anime_id == self._BOOKMARKS_KEY or not isinstance(data, dict):
                    continue
                items.append({
                    'anime_id': anime_id,
                    'title': data.get('title', 'Unknown'),
                    'episode': data.get('episode', '?'),
                    'poster': data.get('poster', ''),
                    'position': data.get('position'),
                    'total': data.get('total'),
                    'progress': data.get('progress'),
                    'last_updated': data.get('last_updated', ''),
                })
            items.sort(key=lambda x: x['last_updated'], reverse=True)
            return items[:limit]
    
    def get_continue_watching(self, limit=12):
        """Get continue-watching items sorted by most recently updated.
        Returns list of dicts with keys: anime_id, title, episode, poster,
        position, total, progress (0..1 or None), last_updated.
        """
        with self._lock:
            items = []
            for anime_id, data in self.history.items():
                if anime_id == "_bookmarks" or not isinstance(data, dict):
                    continue
                items.append({
                    'anime_id': anime_id,
                    'title': data.get('title', 'Unknown'),
                    'episode': data.get('episode', '?'),
                    'poster': data.get('poster', ''),
                    'position': data.get('position'),
                    'total': data.get('total'),
                    'progress': data.get('progress'),
                    'last_updated': data.get('last_updated', '')
                })
            # Sort by last_updated, most recent first
            items.sort(key=lambda x: x['last_updated'], reverse=True)
            return items[:limit]

    # ------------------------------------------------------------------
    # bookmarks ("My List")
    # ------------------------------------------------------------------
    _BOOKMARKS_KEY = "_bookmarks"

    def _bookmarks(self) -> dict:
        with self._lock:
            b = self.history.get(self._BOOKMARKS_KEY)
            if not isinstance(b, dict):
                b = {}
                self.history[self._BOOKMARKS_KEY] = b
            return b

    def is_bookmarked(self, anime_id) -> bool:
        return str(anime_id) in self._bookmarks()

    def toggle_bookmark(self, anime_id, title="", poster="", year=None) -> bool:
        """Add or remove a title from My List. Returns the new state
        (True = bookmarked, False = removed)."""
        anime_id = str(anime_id or "")
        if not anime_id:
            return False
        with self._lock:
            bookmarks = self._bookmarks()
            if anime_id in bookmarks:
                del bookmarks[anime_id]
                state = False
            else:
                bookmarks[anime_id] = {
                    "title": title or anime_id,
                    "poster": poster or "",
                    "year": year,
                    "added": datetime.now().isoformat(),
                }
                state = True
        self.save_history()
        return state

    def remove_bookmark(self, anime_id) -> None:
        anime_id = str(anime_id or "")
        with self._lock:
            bookmarks = self._bookmarks()
            if anime_id in bookmarks:
                del bookmarks[anime_id]
        self.save_history()

    def get_bookmarks(self, limit=50):
        """My List items sorted by most recently added, newest first."""
        with self._lock:
            bookmarks = self._bookmarks()
            items = [
                {
                    "anime_id": aid,
                    "title": d.get("title") or aid,
                    "poster": d.get("poster") or "",
                    "year": d.get("year"),
                    "added": d.get("added") or "",
                }
                for aid, d in bookmarks.items()
            ]
        items.sort(key=lambda x: x["added"], reverse=True)
        return items[:limit]