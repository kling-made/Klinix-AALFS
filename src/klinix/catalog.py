"""SQLite storage for immutable book and recipe snapshots."""

import hashlib
import json
import sqlite3
from collections import defaultdict
from contextlib import contextmanager
from dataclasses import asdict
from pathlib import Path

from klinix.dependencies import DependencyGraph
from klinix.errors import KlinixError
from klinix.recipes import Artifact, Book, Dependency, DependencyKind, Package, Pin, Recipe, Step

SCHEMA_VERSION = 1
APPLICATION_ID = 0x4B4C4E58
SCHEMA = """
CREATE TABLE book (
    id INTEGER PRIMARY KEY,
    family TEXT NOT NULL,
    edition TEXT NOT NULL,
    version TEXT NOT NULL,
    revision TEXT NOT NULL,
    fingerprint TEXT NOT NULL,
    instruction_license TEXT NOT NULL,
    UNIQUE (family, edition, revision)
);
CREATE TABLE package (
    book_id INTEGER NOT NULL REFERENCES book(id),
    name TEXT NOT NULL,
    version TEXT NOT NULL,
    PRIMARY KEY (book_id, name, version)
);
CREATE TABLE artifact (
    book_id INTEGER NOT NULL REFERENCES book(id),
    position INTEGER NOT NULL,
    name TEXT NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('source', 'patch')),
    url TEXT NOT NULL,
    algorithm TEXT NOT NULL,
    checksum TEXT NOT NULL,
    PRIMARY KEY (book_id, url),
    UNIQUE (book_id, position)
);
CREATE TABLE recipe (
    book_id INTEGER NOT NULL REFERENCES book(id),
    id TEXT NOT NULL,
    title TEXT NOT NULL,
    phase TEXT NOT NULL,
    position INTEGER NOT NULL,
    path TEXT NOT NULL,
    package_name TEXT,
    package_version TEXT,
    PRIMARY KEY (book_id, id),
    UNIQUE (book_id, position),
    CHECK ((package_name IS NULL) = (package_version IS NULL)),
    FOREIGN KEY (book_id, package_name, package_version) REFERENCES package(book_id, name, version)
);
CREATE TABLE step (
    book_id INTEGER NOT NULL,
    recipe_id TEXT NOT NULL,
    position INTEGER NOT NULL,
    text TEXT NOT NULL,
    role TEXT NOT NULL,
    remap TEXT NOT NULL,
    manual INTEGER NOT NULL CHECK (manual IN (0, 1)),
    PRIMARY KEY (book_id, recipe_id, position),
    FOREIGN KEY (book_id, recipe_id) REFERENCES recipe(book_id, id)
);
CREATE TABLE recipe_artifact (
    book_id INTEGER NOT NULL,
    recipe_id TEXT NOT NULL,
    position INTEGER NOT NULL,
    url TEXT NOT NULL,
    PRIMARY KEY (book_id, recipe_id, position),
    FOREIGN KEY (book_id, recipe_id) REFERENCES recipe(book_id, id),
    FOREIGN KEY (book_id, url) REFERENCES artifact(book_id, url)
);
CREATE TABLE dependency (
    book_id INTEGER NOT NULL,
    recipe_id TEXT NOT NULL,
    position INTEGER NOT NULL,
    parent_id TEXT NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('required', 'recommended', 'optional')),
    PRIMARY KEY (book_id, recipe_id, parent_id),
    UNIQUE (book_id, recipe_id, position),
    FOREIGN KEY (book_id, recipe_id) REFERENCES recipe(book_id, id),
    FOREIGN KEY (book_id, parent_id) REFERENCES recipe(book_id, id)
);
"""


class Catalog:
    def __init__(self, path: Path) -> None:
        self.path = path

    @contextmanager
    def _connect(self, *, write: bool = False):
        try:
            if write:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                connection = sqlite3.connect(self.path, isolation_level=None)
            else:
                uri = self.path.absolute().as_uri() + "?mode=ro"
                connection = sqlite3.connect(uri, isolation_level=None, uri=True)
            try:
                connection.row_factory = sqlite3.Row
                connection.execute("PRAGMA foreign_keys = ON")
                connection.execute("PRAGMA trusted_schema = OFF")
                connection.execute("BEGIN IMMEDIATE" if write else "BEGIN")
                self._ensure_schema(connection, write)
                yield connection
                connection.commit()
            finally:
                connection.close()
        except (OSError, sqlite3.Error) as exc:
            raise KlinixError(f"Cannot access catalog {self.path}: {exc}") from exc

    @staticmethod
    def _ensure_schema(connection: sqlite3.Connection, write: bool) -> None:
        version = connection.execute("PRAGMA user_version").fetchone()[0]
        application = connection.execute("PRAGMA application_id").fetchone()[0]
        if version == SCHEMA_VERSION and application == APPLICATION_ID:
            return
        if not version and not application and write and not connection.execute(
            "SELECT 1 FROM sqlite_schema",
        ).fetchone():
            for statement in SCHEMA.strip().split(";"):
                if statement.strip():
                    connection.execute(statement)
            connection.execute(f"PRAGMA application_id = {APPLICATION_ID}")
            connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
            return
        if application != APPLICATION_ID:
            raise KlinixError("The selected database is not a Klinix catalog")
        raise KlinixError(f"Unsupported catalog schema {version}; expected {SCHEMA_VERSION}")

    def add(self, book: Book) -> bool:
        graph = DependencyGraph(book.recipes)
        graph.resolve(graph.recipes)
        fingerprint = hashlib.sha256(
            json.dumps(asdict(book), sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        pin = book.pin
        with self._connect(write=True) as db:
            existing = db.execute(
                "SELECT fingerprint FROM book WHERE family=? AND edition=? AND revision=?",
                (pin.family, pin.edition, pin.revision),
            ).fetchone()
            if existing:
                if existing["fingerprint"] != fingerprint:
                    raise KlinixError(f"Catalog content conflicts with existing revision {pin.revision}")
                return False

            result = db.execute(
                """INSERT INTO book (family, edition, version, revision, fingerprint, instruction_license)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (pin.family, pin.edition, pin.version, pin.revision, fingerprint, book.instruction_license),
            )
            book_id = result.lastrowid
            packages = sorted({
                (recipe.package.name, recipe.package.version)
                for recipe in book.recipes if recipe.package is not None
            })
            db.executemany(
                "INSERT INTO package VALUES (?, ?, ?)",
                ((book_id, name, version) for name, version in packages),
            )
            db.executemany(
                "INSERT INTO artifact VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    (book_id, i, item.name, item.kind, item.url, item.algorithm, item.checksum)
                    for i, item in enumerate(book.artifacts)
                ),
            )
            db.executemany(
                "INSERT INTO recipe VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    (book_id, recipe.id, recipe.title, recipe.phase, recipe.order, recipe.path,
                     recipe.package.name if recipe.package else None,
                     recipe.package.version if recipe.package else None)
                    for recipe in book.recipes
                ),
            )
            db.executemany(
                "INSERT INTO step VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    (book_id, recipe.id, i, step.text, step.role, step.remap, step.manual)
                    for recipe in book.recipes for i, step in enumerate(recipe.steps)
                ),
            )
            db.executemany(
                "INSERT INTO recipe_artifact VALUES (?, ?, ?, ?)",
                (
                    (book_id, recipe.id, i, url)
                    for recipe in book.recipes for i, url in enumerate(recipe.materials)
                ),
            )
            db.executemany(
                "INSERT INTO dependency VALUES (?, ?, ?, ?, ?)",
                (
                    (book_id, recipe.id, i, edge.recipe_id, edge.kind)
                    for recipe in book.recipes for i, edge in enumerate(recipe.dependencies)
                ),
            )
        return True

    def pins(self) -> tuple[Pin, ...]:
        with self._connect() as db:
            rows = db.execute("SELECT family, edition, version, revision FROM book ORDER BY id")
            return tuple(Pin(*row) for row in rows)

    def load(self, revision: str) -> Book:
        with self._connect() as db:
            rows = db.execute(
                "SELECT id, instruction_license, family, edition, version, revision FROM book WHERE revision = ?",
                (revision,),
            ).fetchall()
            if len(rows) != 1:
                issue = "Unknown" if not rows else "Ambiguous"
                raise KlinixError(f"{issue} catalog revision {revision!r}; see 'klinix books list'")
            book_id, license_text, *pin = rows[0]
            return self._read_book(db, book_id, Pin(*pin), license_text)

    @staticmethod
    def _read_book(db: sqlite3.Connection, book_id: int, pin: Pin, license_text: str) -> Book:
        artifacts = tuple(
            Artifact(*row) for row in db.execute(
                "SELECT name, kind, url, algorithm, checksum FROM artifact WHERE book_id=? ORDER BY position",
                (book_id,),
            )
        )
        steps = defaultdict(list)
        for recipe_id, text, role, remap, manual in db.execute(
            "SELECT recipe_id, text, role, remap, manual FROM step WHERE book_id=? ORDER BY position",
            (book_id,),
        ):
            steps[recipe_id].append(Step(text, role, remap, bool(manual)))
        materials = defaultdict(list)
        for recipe_id, url in db.execute(
            "SELECT recipe_id, url FROM recipe_artifact WHERE book_id=? ORDER BY position", (book_id,),
        ):
            materials[recipe_id].append(url)
        dependencies = defaultdict(list)
        for recipe_id, parent, kind in db.execute(
            "SELECT recipe_id, parent_id, kind FROM dependency WHERE book_id=? ORDER BY position", (book_id,),
        ):
            dependencies[recipe_id].append(Dependency(parent, DependencyKind(kind)))

        recipes = tuple(
            Recipe(
                recipe_id, title, phase, order, path, Package(name, version) if name else None,
                tuple(materials[recipe_id]), tuple(steps[recipe_id]), tuple(dependencies[recipe_id]),
            )
            for recipe_id, title, phase, order, path, name, version in db.execute(
                """SELECT id, title, phase, position, path, package_name, package_version
                   FROM recipe WHERE book_id=? ORDER BY position""",
                (book_id,),
            )
        )
        return Book(pin, artifacts, recipes, license_text)
