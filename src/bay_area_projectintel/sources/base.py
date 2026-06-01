from __future__ import annotations

from collections.abc import Iterable
from typing import Protocol

from bay_area_projectintel.models import RawRecord


class BaseSource(Protocol):
    name: str

    def fetch(self, since: str | None = None, limit: int | None = None) -> Iterable[RawRecord]:
        ...
