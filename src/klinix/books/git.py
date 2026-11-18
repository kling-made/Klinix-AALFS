"""Read relevant files directly from immutable Git objects."""

import os
import re
import subprocess
from pathlib import Path

from klinix.books import Release
from klinix.errors import KlinixError
from klinix.recipes import REVISION, Pin

LFS_GIT_URL = "https://git.linuxfromscratch.org/lfs.git"
STABLE_TAG = re.compile(r"r([0-9]+(?:\.[0-9]+)+)")
BOOK_PATHS = ["index.xml", "general.ent", "packages.ent", "patches.ent", "appendices/mit-lic.xml"]
BOOK_PATHS += [f"chapter{number:02}" for number in range(3, 11)]


def _git(*args: str, path: Path | None = None) -> bytes:
    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    env.update(
        GIT_CONFIG_NOSYSTEM="1",
        GIT_CONFIG_GLOBAL=os.devnull,
        GIT_NO_LAZY_FETCH="1",
        GIT_TERMINAL_PROMPT="0",
        GIT_ALLOW_PROTOCOL="https" if path is None else "",
    )
    command = ["git", "--no-replace-objects"]
    if path is not None:
        command += ["-C", os.fspath(path)]
    try:
        result = subprocess.run(
            [*command, *args], env=env, capture_output=True, timeout=30, check=True,
            stdin=subprocess.DEVNULL,
        )
    except subprocess.CalledProcessError as exc:
        detail = exc.stderr.decode(errors="replace").strip()
        raise KlinixError(f"Git {args[0]} failed: {detail}") from exc
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise KlinixError(f"Cannot run Git {args[0]}: {exc}") from exc
    return result.stdout


def discover_lfs() -> tuple[Release, ...]:
    result = _git("ls-remote", "--symref", "--", LFS_GIT_URL, "HEAD", "refs/tags/r*")
    refs: dict[str, str] = {}
    head_ref = "HEAD"
    for line in result.decode().splitlines():
        value, _, ref = line.partition("\t")
        if ref == "HEAD" and value.startswith("ref: refs/heads/"):
            head_ref = value.removeprefix("ref: ")
        elif REVISION.fullmatch(value):
            refs[ref] = value

    stable = []
    for ref in refs:
        tag = STABLE_TAG.fullmatch(ref.removeprefix("refs/tags/"))
        if ref.startswith("refs/tags/") and tag:
            version = tag[1]
            revision = refs.get(ref + "^{}", refs[ref])
            stable.append((tuple(map(int, version.split("."))), Release(
                version + "-systemd", ref, revision, "stable",
            )))
    if "HEAD" not in refs or not stable:
        raise KlinixError("The official LFS repository did not return its development and release refs")
    stable.sort(key=lambda item: item[0], reverse=True)
    head = refs["HEAD"]
    return (Release(development_version(head), head_ref, head, "development"),
            *(release for _, release in stable))


def development_version(revision: str) -> str:
    return f"development-{revision[:12]}-systemd"


class GitBook:
    def __init__(self, path: Path, expected: str | None = None) -> None:
        if _git("rev-parse", "--show-prefix", path=path).strip():
            raise KlinixError("Select the root of the LFS Git checkout")
        revision = _git("rev-parse", "--verify", "HEAD^{commit}", path=path).decode().strip()
        if expected is not None and expected != revision:
            raise KlinixError(f"Expected Git revision {expected}, found {revision}")
        if not REVISION.fullmatch(revision):
            raise KlinixError("Git returned an invalid book revision")

        tag_output = _git("tag", "--points-at", revision, "--list", "r[0-9]*", path=path)
        tags = [match[1] for name in tag_output.decode().splitlines()
                if (match := STABLE_TAG.fullmatch(name))]
        if len(tags) > 1:
            raise KlinixError(f"Ambiguous LFS release tags at {revision}: {', '.join(tags)}")
        version = tags[0] + "-systemd" if tags else development_version(revision)
        self.pin = Pin("lfs", "systemd", version, revision)
        self.stable_release = tags[0] if tags else None
        self.commit_date = _git(
            "show", "-s", "--no-show-signature", "--no-notes", "--format=%cs", revision, path=path,
        ).decode().strip()
        if not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", self.commit_date):
            raise KlinixError("Git returned an invalid book date")

        file_rows = _git("ls-tree", "-rlz", "--full-tree", revision, "--", *BOOK_PATHS, path=path)
        self._objects: dict[str, tuple[str, int]] = {}
        total_size = 0
        for row in file_rows.split(b"\x00"):
            if not row:
                continue
            entry, filename = row.split(b"\t", 1)
            mode, kind, object_id, size_text = entry.decode().split()
            if mode not in {"100644", "100755"} or kind != "blob":
                continue
            name = filename.decode("utf-8", errors="surrogateescape")
            if not name.endswith((".xml", ".ent")):
                continue
            size = int(size_text)
            total_size += size
            if size > 1_000_000 or total_size > 8_000_000 or len(self._objects) >= 600:
                raise KlinixError("LFS XML tree exceeds import limits")
            self._objects[name] = object_id, size
        self._path = path
        self._cache: dict[str, bytes] = {}

    def read(self, name: str) -> bytes:
        if name not in self._cache:
            if name not in self._objects:
                raise KlinixError(f"Missing regular book file in pinned Git tree: {name}")
            object_id, size = self._objects[name]
            content = _git("cat-file", "blob", object_id, path=self._path)
            if len(content) != size:
                raise KlinixError(f"Unexpected Git blob size for {name}")
            self._cache[name] = content
        return self._cache[name]
