"""Verify already-downloaded build materials against a pinned book."""

import hashlib
import hmac
import os
import stat
from pathlib import Path, PurePosixPath
from urllib.parse import unquote, urlsplit

from klinix.errors import KlinixError
from klinix.recipes import Artifact, Book


def artifact_filename(artifact: Artifact) -> str:
    return unquote(PurePosixPath(urlsplit(artifact.url).path).name)


def select_artifact(book: Book, path: Path, url: str | None = None) -> Artifact:
    if url is None:
        matches = [item for item in book.artifacts if artifact_filename(item) == path.name]
    else:
        matches = [item for item in book.artifacts if item.url == url]
    if not matches:
        raise KlinixError("No matching source in the pinned book; see 'klinix sources'")
    if len(matches) != 1:
        raise KlinixError("Multiple book sources share this filename; use --url to select one")
    return matches[0]


def verify_file(path: Path, artifact: Artifact) -> int:
    hasher = hashlib.new(artifact.algorithm, usedforsecurity=artifact.algorithm != "md5")

    def stamp(info: os.stat_result) -> tuple[int, int, int]:
        return info.st_size, info.st_mtime_ns, info.st_ctime_ns

    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(descriptor, "rb") as stream:
            initial = os.fstat(stream.fileno())
            if not stat.S_ISREG(initial.st_mode):
                raise KlinixError(f"Source must be a regular file: {path}")
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                hasher.update(chunk)
            if stamp(initial) != stamp(os.fstat(stream.fileno())):
                raise KlinixError(f"Source changed during verification: {path}")
    except OSError as exc:
        raise KlinixError(f"Cannot read source {path}: {exc}") from exc
    actual = hasher.hexdigest()
    if not hmac.compare_digest(actual, artifact.checksum):
        raise KlinixError(
            f"{artifact.algorithm.upper()} checksum mismatch for {path.name}. "
            f"Expected {artifact.checksum}, got {actual}."
        )
    return initial.st_size
