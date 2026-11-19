"""Stage verified inputs and publish a validated directory build."""

import ctypes
import errno
import hashlib
import hmac
import os
import posixpath
import stat
import tarfile
from pathlib import Path

from klinix.builder.recipes import ApprovedInput
from klinix.errors import KlinixError
from klinix.sources import open_source, verify_file

INPUT_LIMIT = 192 * 1024 * 1024
UNPACK_LIMIT = 2 * 1024 * 1024 * 1024
ARCHIVE_MEMBER_LIMIT = 200_000
OUTPUT_ENTRY_LIMIT = 16_384
BOOTSTRAP_OUTPUT_ENTRY_LIMIT = 32_768
OUTPUT_LIMIT = 2 * 1024 * 1024 * 1024
CHUNK_SIZE = 1024 * 1024


def stage_input(source: Path, destination: Path, approved: ApprovedInput) -> None:
    digest = hashlib.sha256()
    size = 0
    with open_source(source) as stream, destination.open("xb") as output:
        for chunk in iter(lambda: stream.read(CHUNK_SIZE), b""):
            size += len(chunk)
            if size > INPUT_LIMIT:
                raise KlinixError(f"Source exceeds the reviewed input size limit: {source.name}")
            digest.update(chunk)
            output.write(chunk)
    if not hmac.compare_digest(digest.hexdigest(), approved.sha256):
        raise KlinixError(f"Source does not match the reviewed SHA-256 digest: {source.name}")
    verify_file(destination, approved.artifact)


def _relative_link_inside(link: str, target: str) -> bool:
    logical = posixpath.normpath(posixpath.join(posixpath.dirname(link), target))
    return bool(target) and not posixpath.isabs(target) and logical != ".." and not logical.startswith("../")


def unpack_archive(archive: Path, destination: Path, top: str) -> None:
    """Extract bounded members, creating safe relative symlinks after regular files."""
    destination.mkdir(exist_ok=True)
    source_root = destination / top
    if os.path.lexists(source_root):
        raise KlinixError(f"Archive source root already exists: {top}")
    seen = set()
    symlinks = []
    size = 0
    try:
        with tarfile.open(archive, mode="r|*") as tar:
            for member in tar:
                components = member.name.split("/")
                if (components[0] != top or len(components) > 16
                        or any(part in {"", ".", ".."} for part in components)):
                    raise KlinixError(f"Archive path escapes the reviewed source tree: {member.name!r}")
                relative = Path(*components)
                if relative in seen:
                    raise KlinixError(f"Duplicate archive entry: {member.name!r}")
                if len(seen) >= ARCHIVE_MEMBER_LIMIT:
                    raise KlinixError("Archive exceeds the reviewed member limit")
                seen.add(relative)
                path = destination / relative
                if member.isdir():
                    path.mkdir(parents=True, exist_ok=True)
                    continue
                if member.issym() and len(components) > 1:
                    if not _relative_link_inside(path.relative_to(source_root).as_posix(), member.linkname):
                        raise KlinixError(f"Archive path escapes the reviewed source tree: {member.name!r}")
                    path.parent.mkdir(parents=True, exist_ok=True)
                    symlinks.append((path, member.linkname))
                    continue
                if not member.isfile() or member.issparse() or member.size < 0 or len(components) == 1:
                    raise KlinixError(f"Unsupported archive entry: {member.name!r}")
                size += member.size
                if size > UNPACK_LIMIT:
                    raise KlinixError("Expanded archive exceeds the reviewed size limit")
                path.parent.mkdir(parents=True, exist_ok=True)
                data = tar.extractfile(member)
                if data is None:
                    raise KlinixError(f"Archive member contains no data: {member.name!r}")
                with data, path.open("xb") as stream:
                    for chunk in iter(lambda: data.read(CHUNK_SIZE), b""):
                        stream.write(chunk)
                path.chmod(0o755 if member.mode & 0o111 else 0o644)
                os.utime(path, (member.mtime, member.mtime))
        # Creating a parent directory for another entry must never create a path
        # that the archive also attempts to declare as a symlink.
        for path, _ in symlinks:
            if os.path.lexists(path):
                raise KlinixError(f"Archive symlink conflicts with another entry: {path.relative_to(source_root)}")
        for path, target in symlinks:
            path.symlink_to(target)
        resolved_root = source_root.resolve()
        for path, _ in symlinks:
            if not path.resolve(strict=True).is_relative_to(resolved_root):
                raise KlinixError(f"Archive symlink escapes the reviewed source tree: {path.relative_to(source_root)}")
    except (OSError, RuntimeError, tarfile.TarError, EOFError) as exc:
        raise KlinixError(f"Cannot unpack the reviewed source archive: {exc}") from exc


def new_target(path: Path) -> Path:
    requested = path.expanduser().absolute()
    try:
        parent = requested.parent.resolve(strict=True)
        metadata = parent.stat()
    except (OSError, RuntimeError) as exc:
        raise KlinixError(f"Cannot open target parent {requested.parent}: {exc}") from exc
    if not requested.name or not stat.S_ISDIR(metadata.st_mode):
        raise KlinixError("Target must name a new directory under an existing directory")
    shared = metadata.st_mode & (stat.S_IWGRP | stat.S_IWOTH)
    if shared and not metadata.st_mode & stat.S_ISVTX:
        raise KlinixError(f"Target parent must prevent other users from replacing build files: {parent}")
    target = parent / requested.name
    if os.path.lexists(target):
        raise KlinixError(f"Target already exists: {target}")
    return target


def inventory(
    root: Path, required: tuple[str, ...], *, bootstrap: bool = False,
    directories: tuple[str, ...] | None = None, symlinks: tuple[str, ...] = (),
) -> list[dict]:
    """Record regular files and safe relative links in a completed staging root."""
    if not stat.S_ISDIR(root.lstat().st_mode):
        raise KlinixError("The staged root is not a directory")
    entries = list(root.iterdir())
    if directories is None:
        prefix = "tools" if bootstrap else "usr"
        if tuple(path.name for path in entries) != (prefix,):
            raise KlinixError(f"Staged output must contain a regular {prefix} directory only")
        directories = (prefix,)
    for entry in entries:
        mode = entry.lstat().st_mode
        if (entry.name in directories and stat.S_ISDIR(mode)) or (entry.name in symlinks and stat.S_ISLNK(mode)):
            continue
        raise KlinixError(f"Unexpected root entry in staged output: {entry.name!r}")
    pending = entries
    seen = 0
    total_size = 0
    entry_limit = BOOTSTRAP_OUTPUT_ENTRY_LIMIT if bootstrap else OUTPUT_ENTRY_LIMIT
    records = {}
    links: dict[tuple[int, int], tuple[int, list[str]]] = {}
    while pending:
        path = pending.pop()
        seen += 1
        relative = path.relative_to(root).as_posix()
        metadata = path.lstat()
        mode = metadata.st_mode
        if seen > entry_limit:
            raise KlinixError(f"Staged output exceeds the {entry_limit:,}-entry limit at {relative!r}")
        if mode & (stat.S_ISUID | stat.S_ISGID | stat.S_ISVTX):
            raise KlinixError(f"Unsupported staged permissions at {relative!r}")
        if stat.S_ISDIR(mode):
            pending.extend(path.iterdir())
            continue
        if stat.S_ISLNK(mode):
            target = os.readlink(path)
            if Path(target).is_absolute():
                raise KlinixError(f"Staged symlink must use a relative target: {relative!r}")
            if not _relative_link_inside(relative, target):
                raise KlinixError(f"Staged symlink escapes the target: {relative!r}")
            try:
                resolved = path.resolve(strict=True)
            except (RuntimeError, OSError) as exc:
                raise KlinixError(f"Cannot resolve staged symlink {relative!r}: {exc}") from exc
            if not resolved.is_relative_to(root):
                raise KlinixError(f"Staged symlink escapes the target: {relative!r}")
            records[relative] = {"path": relative, "kind": "symlink", "target": target}
        elif stat.S_ISREG(mode) and (bootstrap or metadata.st_nlink == 1):
            total_size += metadata.st_size
            if total_size > OUTPUT_LIMIT:
                raise KlinixError("Staged package exceeds the output size limit")
            with path.open("rb") as stream:
                sha256 = hashlib.file_digest(stream, "sha256").hexdigest()
            records[relative] = {"path": relative, "kind": "file", "sha256": sha256,
                                 "mode": stat.S_IMODE(mode), "bytes": metadata.st_size}
            if metadata.st_nlink > 1:
                identifier = metadata.st_dev, metadata.st_ino
                links.setdefault(identifier, (metadata.st_nlink, []))[1].append(relative)
        else:
            raise KlinixError(f"Unsupported staged file type at {relative!r}")
    for count, names in links.values():
        if len(names) != count:
            raise KlinixError(f"Staged hardlink escapes the target: {min(names)!r}")
        first, *aliases = sorted(names)
        for name in aliases:
            records[name] = {"path": name, "kind": "hardlink", "target": first}
    missing = set(required) - records.keys()
    if missing:
        raise KlinixError(f"Build did not produce expected output: {', '.join(sorted(missing))}")
    return [records[name] for name in sorted(records)]


def publish(root: Path, target: Path) -> None:
    """Use Linux rename-no-replace so simultaneous builds cannot overwrite a target."""
    libc = ctypes.CDLL(None, use_errno=True)
    try:
        rename = libc.renameat2
    except AttributeError as exc:
        raise KlinixError("The host libc lacks atomic rename-no-replace support") from exc
    rename.argtypes = (ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint)
    rename.restype = ctypes.c_int
    at_fdcwd = -100
    rename_noreplace = 1
    if rename(at_fdcwd, os.fsencode(root), at_fdcwd, os.fsencode(target), rename_noreplace) != 0:
        error = ctypes.get_errno()
        if error == errno.EEXIST:
            raise KlinixError(f"Target appeared during the build: {target}")
        raise KlinixError(f"Cannot publish the directory atomically: {os.strerror(error)}")
