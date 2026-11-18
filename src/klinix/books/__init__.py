"""Book provider interfaces."""

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from klinix.recipes import Book


@dataclass(frozen=True)
class Release:
    version: str
    ref: str
    revision: str
    channel: str


class BookProvider(Protocol):
    def discover(self) -> tuple[Release, ...]: ...

    def import_local(self, path: Path, expected_revision: str | None = None) -> Book: ...
