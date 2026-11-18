"""Map a pinned catalog entry to reviewed, structured build commands."""

import json
import re
from dataclasses import dataclass
from importlib.resources import files

from klinix.errors import KlinixError
from klinix.recipes import Artifact, Book, Package, Pin, Recipe
from klinix.sources import artifact_filename

TARGET_TRIPLET = "x86_64-lfs-linux-gnu"
SYSROOT = "/mnt/lfs"
TOOLS = f"{SYSROOT}/tools"
BOOTSTRAP_HOST_TOOLS = ("bash", "bison", "flex", "gawk", "g++", "makeinfo", "diff", "perl")
VERSION = re.compile(r"[0-9]+(?:\.[0-9]+)+")


@dataclass(frozen=True)
class ApprovedInput:
    artifact: Artifact
    sha256: str
    unpack_as: str | None = None

    @property
    def filename(self) -> str:
        return artifact_filename(self.artifact)


@dataclass(frozen=True)
class Command:
    label: str
    argv: tuple[str, ...]
    cwd: str = "."
    timeout: int = 180


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
    bootstrap: bool = False
    prerequisites: tuple["PackageBuild", ...] = ()


def reviewed_build(book: Book, recipe_id: str, jobs: int) -> PackageBuild:
    if not 1 <= jobs <= 64:
        raise KlinixError("Build jobs must be between 1 and 64")
    approvals = json.loads(files(__package__).joinpath("approvals.json").read_text(encoding="utf-8"))
    approved = approvals.get(book.pin.revision, {}).get(recipe_id, {})
    adapter = approved.get("adapter")
    factory = {
        "bzip2-v1": _bzip2,
        "binutils-pass1-v1": _binutils,
        "gcc-pass1-v1": _gcc,
    }.get(adapter)
    if (book.pin.family, book.pin.edition) != ("lfs", "systemd") or factory is None:
        raise KlinixError(f"No reviewed build adapter for {recipe_id!r} at {book.pin.revision}")
    recipe = next((entry for entry in book.recipes if entry.id == recipe_id), None)
    if recipe is None or recipe.package is None or not VERSION.fullmatch(recipe.package.version):
        raise KlinixError(f"Pinned catalog lacks reviewed package metadata for {recipe_id!r}")
    return factory(book, recipe, approved, jobs)


def _select_input(book: Book, recipe: Recipe, name: str, kind: str, digest: str) -> ApprovedInput:
    matches = [item for item in book.artifacts if item.kind == kind and artifact_filename(item) == name
               and item.url in recipe.materials]
    if len(matches) != 1:
        raise KlinixError(f"Pinned recipe requires exactly one catalog entry for {name}")
    return ApprovedInput(matches[0], digest)


def _bzip2(book: Book, recipe: Recipe, approval: dict, jobs: int) -> PackageBuild:
    package = recipe.package
    if package is None or package.name != "bzip2":
        raise KlinixError("Pinned catalog lacks the reviewed Bzip2 package metadata")
    source_root = f"bzip2-{package.version}"
    expected_names = {"source": source_root + ".tar.gz", "patch": source_root + "-install_docs-1.patch"}
    selected = tuple(
        _select_input(book, recipe, name, kind, approval[f"{kind}_sha256"])
        for kind, name in expected_names.items()
    )

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
    return PackageBuild(book.pin, recipe.id, package, approval["adapter"], selected,
                        source_root, commands, required)


def _binutils(book: Book, recipe: Recipe, approval: dict, jobs: int) -> PackageBuild:
    package = recipe.package
    if package is None or package.name != "binutils-pass1":
        raise KlinixError("Pinned catalog lacks the reviewed Binutils pass 1 package metadata")
    source_root = f"binutils-{package.version}"
    archive = _select_input(book, recipe, source_root + ".tar.xz", "source", approval["source_sha256"])
    target_bin = f"{TOOLS}/bin/{TARGET_TRIPLET}"
    commands = (
        Command("Create an out-of-tree build directory", ("/usr/bin/mkdir", "build")),
        Command("Configure cross Binutils", (
            "../configure", f"--prefix={TOOLS}", f"--with-sysroot={SYSROOT}",
            f"--target={TARGET_TRIPLET}", "--disable-nls", "--enable-gprofng=no",
            "--disable-werror", "--enable-new-dtags", "--enable-default-hash-style=gnu",
        ), cwd="build", timeout=600),
        Command("Compile cross Binutils", ("/usr/bin/make", f"-j{jobs}"), cwd="build", timeout=1800),
        Command("Install cross Binutils", ("/usr/bin/make", "install"), cwd="build", timeout=600),
        Command("Check the cross assembler and linker", (
            "/usr/bin/bash", "-ec",
            r'printf "%s\n" ".text" ".globl _start" "_start: nop" > probe.s; '
            '"$1-as" -o probe.o probe.s; '
            '"$1-ld" -e _start -o probe probe.o; '
            'test "$("$1-ld" --print-sysroot)" = "$2"; '
            '"$1-readelf" -h probe',
            "klinix", target_bin, SYSROOT,
        ), cwd="build"),
    )
    expected = tuple(f"tools/bin/{TARGET_TRIPLET}-{name}" for name in ("as", "ld", "ar", "readelf"))
    return PackageBuild(book.pin, recipe.id, package, approval["adapter"], (archive,),
                        source_root, commands, expected, bootstrap=True)


def _gcc(book: Book, recipe: Recipe, approval: dict, jobs: int) -> PackageBuild:
    package = recipe.package
    if package is None or package.name != "gcc-pass1":
        raise KlinixError("Pinned catalog lacks the reviewed GCC pass 1 package metadata")
    libc = next((item.package for item in book.recipes if item.id == "chapter05/glibc"), None)
    if libc is None or libc.name != "glibc" or not VERSION.fullmatch(libc.version):
        raise KlinixError("Pinned catalog lacks the target Glibc version for GCC pass 1")

    source_root = f"gcc-{package.version}"
    archives = [_select_input(book, recipe, source_root + ".tar.xz", "source", approval["source_sha256"])]
    for name in ("mpfr", "gmp", "mpc"):
        candidates = [
            artifact for artifact in book.artifacts
            if artifact.kind == "source" and artifact.url in recipe.materials
            and re.fullmatch(rf"{name}-[0-9]+(?:\.[0-9]+)+\.tar\.xz", artifact_filename(artifact))
        ]
        if len(candidates) != 1:
            raise KlinixError(f"Pinned GCC recipe requires exactly one source archive for {name}")
        archives.append(ApprovedInput(candidates[0], approval[f"{name}_sha256"], name))

    target_bin = f"{TOOLS}/bin/{TARGET_TRIPLET}"
    commands = (
        Command("Use lib for 64-bit target libraries", (
            "/usr/bin/sed", "-i", "/m64=/s/lib64/lib/", "gcc/config/i386/t-linux64",
        )),
        Command("Create an out-of-tree build directory", ("/usr/bin/mkdir", "build")),
        Command("Configure the headerless cross compiler", (
            "/usr/bin/env", "CFLAGS=-O2", "CXXFLAGS=-O2",
            "../configure", f"--target={TARGET_TRIPLET}", f"--prefix={TOOLS}",
            f"--with-glibc-version={libc.version}", f"--with-sysroot={SYSROOT}",
            f"--with-as={target_bin}-as", f"--with-ld={target_bin}-ld",
            "--with-newlib", "--without-headers", "--enable-default-pie", "--enable-default-ssp",
            "--disable-fixincludes", "--disable-nls", "--disable-shared", "--disable-multilib",
            "--disable-threads", "--disable-libatomic", "--disable-libgomp", "--disable-libquadmath",
            "--disable-libssp", "--disable-libvtv", "--disable-libstdcxx", "--enable-languages=c,c++",
        ), cwd="build", timeout=600),
        Command("Compile cross GCC and libgcc", ("/usr/bin/make", f"-j{jobs}"), cwd="build", timeout=5400),
        Command("Install cross GCC", ("/usr/bin/make", "install"), cwd="build", timeout=600),
        Command("Complete the GCC limits header", (
            "/usr/bin/bash", "-ec",
            'include=$("$1-gcc" -print-file-name=include); '
            'cat gcc/limitx.h gcc/glimits.h gcc/limity.h > "$include/limits.h"',
            "klinix", target_bin,
        )),
        Command("Compile freestanding C and C++ objects with the staged tools", (
            "/usr/bin/bash", "-ec",
            'test "$("$1-gcc" -dumpmachine)" = "$2"; '
            'test "$("$1-gcc" -print-sysroot)" = "$3"; '
            r'printf "%s\n" "int bootstrap_probe(void) { return 0; }" > probe.c; '
            '"$1-gcc" -ffreestanding -c probe.c -o c.o; '
            '"$1-g++" -ffreestanding -x c++ -c probe.c -o cxx.o; '
            '"$1-readelf" -h c.o cxx.o',
            "klinix", target_bin, TARGET_TRIPLET, SYSROOT,
        ), cwd="build"),
    )
    expected = (
        f"tools/bin/{TARGET_TRIPLET}-gcc", f"tools/bin/{TARGET_TRIPLET}-g++",
        f"tools/lib/gcc/{TARGET_TRIPLET}/{package.version}/libgcc.a",
        f"tools/lib/gcc/{TARGET_TRIPLET}/{package.version}/include/limits.h",
    )
    binutils = reviewed_build(book, "chapter05/binutils-pass1", jobs)
    return PackageBuild(book.pin, recipe.id, package, approval["adapter"], tuple(archives),
                        source_root, commands, expected, bootstrap=True, prerequisites=(binutils,))
