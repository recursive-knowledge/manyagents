"""``Collection[T]`` — one generic container for agents/packets/posts/distills.

``list / get / search(regex) / remove / __getitem__ / __len__ / __iter__``.
Items are identified by a ``.id`` attribute (frozen value objects).
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Iterator
from typing import Generic, Protocol, TypeVar


class _HasId(Protocol):
    id: str


T = TypeVar("T", bound=_HasId)


class Collection(Generic[T]):
    """An ordered, id-addressable collection of frozen value objects."""

    def __init__(self, items: Iterable[T] = ()) -> None:
        self._items: list[T] = list(items)

    def list(self) -> list[T]:
        return list(self._items)

    def get(self, id: str) -> T | None:
        return next((it for it in self._items if it.id == id), None)

    def search(self, pattern: str) -> Collection[T]:
        """Items whose ``id`` matches the regex ``pattern``."""
        rx = re.compile(pattern)
        return Collection(it for it in self._items if rx.search(it.id))

    def remove(self, id: str) -> bool:
        """Drop the item with ``id``; returns whether one was removed."""
        for i, it in enumerate(self._items):
            if it.id == id:
                del self._items[i]
                return True
        return False

    def __getitem__(self, index: int) -> T:
        return self._items[index]

    def __len__(self) -> int:
        return len(self._items)

    def __iter__(self) -> Iterator[T]:
        return iter(self._items)

    def __repr__(self) -> str:
        return f"Collection({self._items!r})"
