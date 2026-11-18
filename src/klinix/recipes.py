"""Validated metadata shared by book providers, catalogs, and planners."""

import re
from dataclasses import dataclass
from enum import StrEnum
from urllib.parse import urlsplit

from klinix.errors import KlinixError

REVISION = re.compile(r"(?:[a-f0-9]{40}|[a-f0-9]{64})")
RECIPE_ID = re.compile(r"chapter[0-9]{2}/[a-z0-9][a-z0-9_.+-]*")
PACKAGE_NAME = re.compile(r"[a-z0-9][a-z0-9_.+-]*")


class DependencyKind(StrEnum):
    REQUIRED = "required"
    RECOMMENDED = "recommended"
    OPTIONAL = "optional"


@dataclass(frozen=True)
class Pin:
    family: str
    edition: str
    version: str
    revision: str

    def __post_init__(self) -> None:
        if not REVISION.fullmatch(self.revision):
            raise KlinixError("Book revision must be a full Git object ID")
        if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9.+-]{0,100}", self.version):
            raise KlinixError(f"Invalid book version {self.version!r}")


@dataclass(frozen=True)
class Artifact:
    name: str
    kind: str
    url: str
    algorithm: str
    checksum: str

    def __post_init__(self) -> None:
        try:
            url = urlsplit(self.url)
            valid_url = url.hostname and url.username is None and url.password is None
        except ValueError:
            valid_url = False
        if not valid_url or url.scheme not in {"https", "http", "ftp"}:
            raise KlinixError(f"Unsupported or invalid material URL for {self.name}")
        if any(char.isspace() or ord(char) < 32 for char in self.url):
            raise KlinixError(f"Invalid whitespace in material URL for {self.name}")
        digest_size = {"md5": 32, "sha256": 64, "sha512": 128}.get(self.algorithm)
        if not digest_size or not re.fullmatch(f"[a-f0-9]{{{digest_size}}}", self.checksum):
            raise KlinixError(f"Invalid {self.algorithm} checksum for {self.name}")
        if self.kind not in {"source", "patch"} or not self.name.strip():
            raise KlinixError("A material must have a name and a supported kind")


@dataclass(frozen=True)
class Dependency:
    recipe_id: str
    kind: DependencyKind = DependencyKind.REQUIRED

    def __post_init__(self) -> None:
        if not RECIPE_ID.fullmatch(self.recipe_id) or not isinstance(self.kind, DependencyKind):
            raise KlinixError(f"Invalid dependency {self.recipe_id!r} ({self.kind!r})")


@dataclass(frozen=True)
class Step:
    text: str
    role: str
    remap: str
    manual: bool

    def __post_init__(self) -> None:
        if not self.text.strip() or len(self.text) > 65536:
            raise KlinixError("A reference step must contain 1–65536 characters of command text")


@dataclass(frozen=True)
class Package:
    name: str
    version: str

    def __post_init__(self) -> None:
        if not PACKAGE_NAME.fullmatch(self.name) or not self.version.strip():
            raise KlinixError(f"Invalid package name or version for {self.name!r}")


@dataclass(frozen=True)
class Recipe:
    id: str
    title: str
    phase: str
    order: int
    path: str
    package: Package | None
    materials: tuple[str, ...]
    steps: tuple[Step, ...]
    dependencies: tuple[Dependency, ...] = ()

    def __post_init__(self) -> None:
        if not RECIPE_ID.fullmatch(self.id) or self.order < 0:
            raise KlinixError(f"Invalid recipe ID or order for {self.id!r}")
        if self.path != self.id + ".xml":
            raise KlinixError(f"Invalid source path {self.path!r} for {self.id}")
        if not self.title.strip() or not self.phase.strip() or not self.steps:
            raise KlinixError(f"{self.id} requires a title, phase, and reference steps")
        if len(set(self.materials)) != len(self.materials):
            raise KlinixError(f"Duplicate material references in {self.id}")
        dependency_ids = {dependency.recipe_id for dependency in self.dependencies}
        if len(dependency_ids) != len(self.dependencies):
            raise KlinixError(f"Duplicate dependencies in {self.id}")


@dataclass(frozen=True)
class Book:
    pin: Pin
    artifacts: tuple[Artifact, ...]
    recipes: tuple[Recipe, ...]
    instruction_license: str = ""

    def __post_init__(self) -> None:
        urls = {artifact.url for artifact in self.artifacts}
        if not self.recipes or len(urls) != len(self.artifacts):
            raise KlinixError("Book requires recipes and unique material URLs")
        if [recipe.order for recipe in self.recipes] != list(range(len(self.recipes))):
            raise KlinixError("Book recipes must have consecutive order numbers from zero")
        for recipe in self.recipes:
            for url in recipe.materials:
                if url not in urls:
                    raise KlinixError(f"{recipe.id} references a material absent from the book catalog")
