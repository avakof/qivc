"""Data-layer agents and the DataAgent abstract base class."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class DataAgent[T](ABC):
    """Contract every data agent must satisfy."""

    @abstractmethod
    async def fetch(self, **kwargs: Any) -> T:
        """Fetch and return typed data. Raises QivcDataError on failure."""

    @property
    @abstractmethod
    def source_name(self) -> str:
        """Stable string identifier used in audit logs."""
