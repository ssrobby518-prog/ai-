"""Abstract base class for news source plugins."""

from __future__ import annotations

from abc import ABC, abstractmethod

from schemas.models import RawItem


class NewsSource(ABC):
    """Base class that all source plugins must implement."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Human-readable source name."""
        ...

    @abstractmethod
    def fetch(self) -> list[RawItem]:
        """Fetch items from this source. Must never raise."""
        ...
