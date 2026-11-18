"""Map a pinned catalog entry to reviewed, structured build commands."""

import json
import re
from dataclasses import dataclass
from importlib.resources import files

from klinix.errors import KlinixError
from klinix.recipes import Artifact, Book, Package, Pin
from klinix.sources import artifact_filename


@dataclass(frozen=True)
class ApprovedInput:
    artifact: Artifact
    sha256: str

    @property
    def filename(self) -> str:
        return artifact_filename(self.artifact)


@dataclass(frozen=True)
class Command:
    label: str
    argv: tuple[str, ...]


@dataclass(frozen=True)
class PackageBuild:
    pin: Pin
    recipe: str
    package: Package
    adapter: str
    inputs: tuple[ApprovedInput, ...]
    source_root: str
    commands: tuple[Command, ...]
    required_output: tuple[str, ...]


def reviewed_build(book: Book, recipe_id: str, jobs: int) -> PackageBuild:
    if not 1 <= jobs <= 64:
        raise KlinixError("Build jobs must be between 1 and 64")
    approvals = json.loads(files(__package__).joinpath("approvals.json").read_text(encoding="utf-8"))
    approved = approvals.get(book.pin.revision, {}).get(recipe_id, {})
    if (book.pin.family, book.pin.edition) != ("lfs", "systemd") or approved.get("adapter") != "bzip2-v1":
        raise KlinixError(f"No reviewed build adapter for {recipe_id!r} at {book.pin.revision}")

    recipe = next((entry for entry in book.recipes if entry.id == recipe_id), None)
    package = recipe.package if recipe else None
    if package is None or package.name != "bzip2" or not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", package.version):
        raise KlinixError("Pinned catalog lacks the reviewed Bzip2 package metadata")
    assert recipe is not None

    source_root = f"bzip2-{package.version}"
    expected_names = {"source": source_root + ".tar.gz", "patch": source_root + "-install_docs-1.patch"}
    selected = []
    for kind, name in expected_names.items():
        matches = [item for item in book.artifacts if item.kind == kind and artifact_filename(item) == name
                   and item.url in recipe.materials]
        if len(matches) != 1:
            raise KlinixError(f"Pinned recipe requires exactly one catalog entry for {name}")
        selected.append(ApprovedInput(matches[0], approved[f"{kind}_sha256"]))

    prefix = "/work/root/usr"
    library = f"libbz2.so.{package.version}"
    license_dir = f"{prefix}/share/doc/{source_root}"
    commands = (
        Command("Apply the documentation patch", (
            "/usr/bin/patch", "--batch", "--forward", "-Np1", "-i", f"/inputs/{expected_names['patch']}",
        )),
        Command("Prepare staged relative symlinks and man page paths", (
            "/usr/bin/sed", "-i",
            "-e", r"s@\(ln -s -f \)$(PREFIX)/bin/@\1@",
            "-e", "s@(PREFIX)/man@(PREFIX)/share/man@g",
            "Makefile",
        )),
        Command("Compile the shared library and executable", (
            "/usr/bin/make", f"-j{jobs}", "-f", "Makefile-libbz2_so",
        )),
        Command("Clean objects before the regular build", ("/usr/bin/make", "clean")),
        Command("Compile and run upstream self-tests", ("/usr/bin/make", f"-j{jobs}", "all")),
        Command("Install into the staging directory", ("/usr/bin/make", f"PREFIX={prefix}", "install")),
        Command("Install the shared library", ("/usr/bin/cp", "-a", library, "libbz2.so.1.0", f"{prefix}/lib")),
        Command("Create library links", ("/usr/bin/ln", "-s", library, f"{prefix}/lib/libbz2.so")),
        Command("Create the compatibility link", ("/usr/bin/ln", "-s", library, f"{prefix}/lib/libbz2.so.1")),
        Command("Install the shared executable", ("/usr/bin/cp", "bzip2-shared", f"{prefix}/bin/bzip2")),
        Command("Replace duplicate executables with links", (
            "/usr/bin/ln", "-sf", "bzip2", f"{prefix}/bin/bunzip2",
        )),
        Command("Replace the archive reader with a link", ("/usr/bin/ln", "-sf", "bzip2", f"{prefix}/bin/bzcat")),
        Command("Remove the static archive", ("/usr/bin/rm", f"{prefix}/lib/libbz2.a")),
        Command("Install upstream licensing terms", ("/usr/bin/install", "-m", "644", "LICENSE", license_dir)),
        Command("Check sample integrity with the staged executable", (
            "/usr/bin/env", f"LD_LIBRARY_PATH={prefix}/lib", f"{prefix}/bin/bzip2", "-t", "sample1.bz2",
        )),
    )
    required = ("usr/bin/bzip2", "usr/bin/bunzip2", "usr/bin/bzcat", f"usr/lib/{library}", "usr/include/bzlib.h")
    return PackageBuild(book.pin, recipe_id, package, approved["adapter"], tuple(selected),
                        source_root, commands, required)
