"""Configuration shared by the CLI and future graphical entry point."""

import os
import tomllib
from dataclasses import dataclass
from pathlib import Path

from klinix.errors import KlinixError


@dataclass(frozen=True)
class Settings:
    database: Path
    recommended: bool = False
    optional: bool = False

    @classmethod
    def load(cls, path: Path | None = None) -> "Settings":
        state = Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local/state")
        default_database = state / "klinix/catalog.sqlite3"
        if path is None:
            return cls(default_database)
        try:
            with path.open("rb") as stream:
                data = tomllib.load(stream)
        except (OSError, ValueError) as exc:
            raise KlinixError(f"Cannot read configuration {path}: {exc}") from exc
        unknown = data.keys() - {"database", "recommended", "optional"}
        if unknown:
            raise KlinixError(f"Unknown configuration options: {', '.join(sorted(unknown))}")
        database = data.get("database", str(default_database))
        recommended = data.get("recommended", False)
        optional = data.get("optional", False)
        if not isinstance(database, str) or not database.strip():
            raise KlinixError("database must be a nonempty path")
        if not isinstance(recommended, bool) or not isinstance(optional, bool):
            raise KlinixError("recommended and optional must be booleans")
        return cls(path.parent / Path(database).expanduser(), recommended, optional)
