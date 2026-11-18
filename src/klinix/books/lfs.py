"""Import a pinned, systemd-profiled LFS DocBook source."""

import re
from collections.abc import Iterator
from pathlib import Path, PurePosixPath
from xml.etree.ElementTree import Element

from klinix.books import Release
from klinix.books.git import GitBook, discover_lfs
from klinix.books.xml import DocBook, XINCLUDE, edition_matches, plain_text, profiled_children
from klinix.errors import KlinixError
from klinix.recipes import Artifact, Book, Dependency, Package, Recipe, Step
from klinix.sources import artifact_filename

PHASES = {
    "chapter04": "host-preparation",
    "chapter05": "cross-toolchain",
    "chapter06": "temporary-tools",
    "chapter07": "chroot-and-temporary-tools",
    "chapter08": "final-system",
    "chapter09": "system-configuration",
    "chapter10": "boot-configuration",
}
PATH_PATTERN = re.compile(r"(?:[a-zA-Z0-9_][a-zA-Z0-9_.+-]*/)*[a-zA-Z0-9_][a-zA-Z0-9_.+-]*\.xml")
ARCHIVE_TOKEN = re.compile(r"[a-zA-Z0-9_.+-]+")
CHECKSUM = re.compile(r"(MD5|SHA-?256|SHA-?512)(?:\s+(?:sum|checksum))?\s*:", re.IGNORECASE)


def _walk(element: Element) -> Iterator[Element]:
    if edition_matches(element):
        yield element
        for child in profiled_children(element):
            yield from _walk(child)


def _field(element: Element, tag: str) -> str:
    return next((plain_text(child).strip() for child in profiled_children(element) if child.tag == tag), "")


def _includes(element: Element, parent: str) -> Iterator[str]:
    for node in _walk(element):
        if node.tag != XINCLUDE:
            continue
        href = node.get("href", "")
        if not PATH_PATTERN.fullmatch(href) or set(node.attrib) - {"href"}:
            raise KlinixError(f"Unsupported XML include {href!r} in {parent}")
        yield (PurePosixPath(parent).parent / href).as_posix()


def _materials(root: Element, kind: str) -> Iterator[Artifact]:
    for node in _walk(root):
        if node.tag != "variablelist" or node.get("role") != "materials":
            continue
        for entry in profiled_children(node):
            if entry.tag != "varlistentry":
                continue
            title = " ".join(_field(entry, "term").split())
            url = ""
            digest = ""
            algorithm = ""
            for para in entry.findall("./listitem/para"):
                if not edition_matches(para):
                    continue
                text = plain_text(para).strip()
                if text.startswith("Download:"):
                    url = next((link.get("url", "") for link in profiled_children(para)
                                if link.tag == "ulink"), "")
                elif match := CHECKSUM.match(text):
                    algorithm = match[1].lower().replace("-", "")
                    digest = text[match.end():].strip().lower()
            if not url or not digest:
                raise KlinixError(f"Material entry lacks download or checksum: {title}")
            yield Artifact(title, kind, url, algorithm, digest)


def _steps(section: Element) -> tuple[Step, ...]:
    steps = []
    for screen in _walk(section):
        if screen.tag != "screen":
            continue
        role = screen.get("role", "")
        for inp in profiled_children(screen):
            if inp.tag != "userinput":
                continue
            text = plain_text(inp).strip("\n")
            if text.strip():
                manual = bool(role) or any(node.tag == "replaceable" for node in _walk(inp))
                steps.append(Step(text, role, inp.get("remap", ""), manual))
    return tuple(steps)


class LfsProvider:
    """Systemd LFS source provider. Imported recipe text is display data."""

    def discover(self) -> tuple[Release, ...]:
        return discover_lfs()

    def import_local(self, path: Path, expected_revision: str | None = None) -> Book:
        source = GitBook(path, expected_revision)
        doc = DocBook(source)
        index = doc.parse("index.xml")
        if index.tag != "book":
            raise KlinixError("The source does not have an LFS DocBook index")
        chapter_paths = tuple(_includes(index, "index.xml"))
        if not {"chapter03/chapter03.xml", "chapter05/chapter05.xml", "chapter08/chapter08.xml"}.issubset(
            chapter_paths,
        ):
            raise KlinixError("The source lacks required LFS chapters")
        artifacts = tuple(
            item
            for filename, kind in (("packages.xml", "source"), ("patches.xml", "patch"))
            for item in _materials(doc.parse(f"chapter03/{filename}"), kind)
        )
        if not artifacts:
            raise KlinixError("The LFS source catalog is empty")
        urls = {item.url for item in artifacts}
        artifact_names = [(item.url, artifact_filename(item)) for item in artifacts]

        recipes = []
        for chapter_path in chapter_paths:
            chapter = PurePosixPath(chapter_path).parent.name
            if chapter not in PHASES or chapter_path != f"{chapter}/{chapter}.xml":
                continue
            sections = _includes(doc.parse(chapter_path), chapter_path)
            for section_path in sections:
                if PurePosixPath(section_path).parent.as_posix() != chapter:
                    raise KlinixError(f"Unexpected section outside {chapter}: {section_path}")
                section = doc.parse(section_path)
                steps = _steps(section)
                if not steps:
                    continue
                recipe_id = section_path.removesuffix(".xml")
                details = next((child for child in profiled_children(section)
                                if child.tag == "sect1info" and child.get("condition") == "script"), None)
                package = None
                materials = []
                if details is not None:
                    package = Package(_field(details, "productname").lower(), _field(details, "productnumber"))
                    url = _field(details, "address")
                    if url not in urls:
                        raise KlinixError(f"{section_path} has a package source absent from Chapter 3")
                    materials.append(url)
                tokens = {token for step in steps for token in ARCHIVE_TOKEN.findall(step.text)}
                materials.extend(url for url, name in artifact_names if name in tokens and url not in materials)
                required = (Dependency(recipes[-1].id),) if recipes else ()
                recipes.append(Recipe(
                    recipe_id, _field(section, "title"), PHASES[chapter], len(recipes), section_path,
                    package, tuple(materials), steps, required,
                ))

        if not any(recipe.package and recipe.package.name == "systemd" for recipe in recipes):
            raise KlinixError("No systemd package recipe was found in this LFS source")
        license_root = doc.parse("appendices/mit-lic.xml")
        license_text = "\n\n".join(
            " ".join(plain_text(node).split()) for node in _walk(license_root) if node.tag == "para"
        )
        if not license_text:
            raise KlinixError("LFS instruction license is missing from the pinned source")
        return Book(source.pin, artifacts, tuple(recipes), license_text)
