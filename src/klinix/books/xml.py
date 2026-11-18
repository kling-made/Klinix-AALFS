"""Parse the LFS DocBook profile without fetching external files or DTDs."""

import posixpath
import re
from functools import partial
from html.entities import name2codepoint
from xml.etree.ElementTree import Element, TreeBuilder
from xml.parsers import expat

from klinix.books.git import GitBook
from klinix.errors import KlinixError

DOCBOOK_DTD = "http://www.oasis-open.org/docbook/xml/4.5/docbookx.dtd"
XINCLUDE = "{http://www.w3.org/2001/XInclude}include"
CHAR_ENTITIES = "\n".join(
    f'<!ENTITY {name} "&#{value};">'
    for name, value in name2codepoint.items() if name not in {"lt", "gt", "amp", "quot"}
).encode()
CONDITIONAL = b'<!ENTITY % sysv "IGNORE">\n<!ENTITY % systemd "INCLUDE">'
COMMON_FILES = {"general.ent", "packages.ent", "patches.ent", "conditional.ent", "version.ent"}
ENTITY_CHILDREN = {
    "general.ent": {"packages.ent", "patches.ent", "conditional.ent", "version.ent"},
}


def _is_systemd(revisions: str | None) -> bool:
    return revisions is None or any(name.strip() == "systemd" for name in revisions.split(";"))


def edition_matches(element: Element) -> bool:
    return _is_systemd(element.get("revision"))


def profiled_children(element: Element):
    """Yield elements of the selected edition, keeping ancestor conditions."""
    for child in element:
        if edition_matches(child):
            yield child


def plain_text(element: Element) -> str:
    parts = [element.text or ""]
    for child in element:
        if edition_matches(child):
            parts.append(plain_text(child))
        parts.append(child.tail or "")
    return "".join(parts)


class DocBook:
    def __init__(self, source: GitBook) -> None:
        self.source = source
        self._entities: dict[str, bytes] = {"conditional.ent": CONDITIONAL}
        self._remaining_nodes = 180_000
        self._remaining_text = 12_000_000
        self._active: set[str] = set()

    def _entity(self, name: str) -> bytes:
        if name not in self._entities:
            if name == "version.ent":
                self._entities[name] = (
                    f'<!ENTITY version "{self.source.pin.version}">\n'
                    f'<!ENTITY releasedate "{self.source.commit_date}">\n'
                    f'<!ENTITY copyrightdate "1999-{self.source.commit_date[:4]}">\n'
                ).encode()
            else:
                data = self.source.read(name)
                if name == "general.ent" and (release := self.source.stable_release):
                    for key, value in (
                        ("development", "IGNORE"), ("release", "INCLUDE"),
                        ("rc", "IGNORE"), ("relnum", release),
                    ):
                        pattern = rb'(<!ENTITY\s+%\s+' + key.encode() + rb'\s+")[^"]+(")'
                        data, replacements = re.subn(
                            pattern, lambda match: match[1] + value.encode() + match[2], data, count=1,
                        )
                        if replacements != 1:
                            raise KlinixError(f"Unsupported LFS release entity {key!r}")
                self._entities[name] = data
        return self._entities[name]

    def parse(self, path: str) -> Element:
        parser = expat.ParserCreate(namespace_separator="}")
        builder = TreeBuilder()
        depth = 0
        excluded_depth = 0

        def qualify(name: str) -> str:
            return "{" + name if "}" in name else name

        def start(name: str, attrs: dict[str, str]) -> None:
            nonlocal depth, excluded_depth
            depth += 1
            if not excluded_depth and not _is_systemd(attrs.get("revision")):
                excluded_depth = depth
            self._remaining_nodes -= 1
            self._remaining_text -= sum(len(k) + len(v) for k, v in attrs.items())
            if depth > 80 or self._remaining_nodes < 0 or self._remaining_text < 0:
                raise KlinixError(f"Expanded XML exceeds import limits in {path}")
            builder.start(qualify(name), {qualify(k): v for k, v in attrs.items()})

        def end(name: str) -> None:
            nonlocal depth, excluded_depth
            if excluded_depth == depth:
                excluded_depth = 0
            builder.end(qualify(name))
            depth -= 1

        def text(value: str) -> None:
            self._remaining_text -= len(value)
            if self._remaining_text < 0:
                raise KlinixError(f"Expanded XML exceeds import limits in {path}")
            builder.data(value)

        parser.StartElementHandler = start
        parser.EndElementHandler = end
        parser.CharacterDataHandler = text
        parser.SetParamEntityParsing(expat.XML_PARAM_ENTITY_PARSING_ALWAYS)
        def external(context, base, system_id, public_id):
            if context is not None and excluded_depth:
                return 1
            return self._external(parser, path, context, base, system_id, public_id)

        parser.ExternalEntityRefHandler = external
        try:
            parser.Parse(self.source.read(path), True)
            return builder.close()
        except (expat.ExpatError, ValueError) as exc:
            raise KlinixError(f"Cannot parse {path}: {exc}") from exc

    def _external(self, parser, parent, context, base, system_id, public_id) -> int:
        if context is not None:
            raise KlinixError(f"External general entities are unsupported in {parent}")
        if system_id == DOCBOOK_DTD and public_id == "-//OASIS//DTD DocBook XML V4.5//EN":
            parser.ExternalEntityParserCreate(context).Parse(CHAR_ENTITIES, True)
            return 1
        expected = ENTITY_CHILDREN.get(parent, set() if parent in COMMON_FILES else {"general.ent"})
        target = posixpath.normpath(posixpath.join(posixpath.dirname(parent), system_id))
        relative = posixpath.relpath(target, posixpath.dirname(parent) or ".")
        if public_id or target not in expected or system_id != relative or target in self._active:
            raise KlinixError(f"Unsupported external XML entity {system_id!r} in {parent}")

        external = parser.ExternalEntityParserCreate(context)
        external.ExternalEntityRefHandler = partial(self._external, external, target)
        self._active.add(target)
        try:
            external.Parse(self._entity(target), True)
        finally:
            self._active.remove(target)
        return 1
