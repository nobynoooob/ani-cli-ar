from abc import ABC, abstractmethod
from typing import Dict, List, Optional


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
