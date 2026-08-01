from abc import ABC, abstractmethod
import re
from typing import Dict, List, Optional


def quality_rank(value) -> int:
    """Rank a quality label (e.g. '1080p', '720') highest-first. Returns 0
    when unparseable, so unknown-quality sources sort below known ones."""
    if value is None:
        return 0
    m = re.search(r"(\d{3,4})", str(value))
    if not m:
        return 0
    try:
        return int(m.group(1))
    except ValueError:
        return 0


class BaseScraper(ABC):

    @abstractmethod
    def search(self, query: str) -> List[Dict]:
        """Search for anime by title.
        Returns [{'title': str, 'id': str}]
        """
        ...

    @abstractmethod
    def get_episodes(self, anime_id: str) -> List[Dict]:
        """Get episode list for an anime.
        Returns [{'episode_num': int|float, 'id': str}]
        """
        ...

    @abstractmethod
    def get_stream_url(self, episode_id: str) -> Dict:
        """Resolve a stream URL for a given episode ID.
        Returns {'stream_url': str, 'headers': dict}
        """
        ...

    @property
    @abstractmethod
    def name(self) -> str:
        ...
