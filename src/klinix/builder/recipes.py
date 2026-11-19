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
TARGET_SOURCE = "/tmp/source"
TARGET_LOADER = "/lib64/ld-linux-x86-64.so.2"
TARGET_COMPILER = f"{TOOLS}/bin/{TARGET_TRIPLET}-gcc"
CONFIG_SITE = f"{SYSROOT}/usr/share/config.site"
BOOTSTRAP_HOST_TOOLS = (
    "bash", "bison", "diff", "find", "flex", "g++", "gawk", "grep", "m4", "makeinfo", "perl", "python3", "readlink",
)
VERSION = re.compile(r"[0-9]+(?:\.[0-9]+)*")


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
    target_root: bool = False
    target_writable: bool = False


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
    root_directories: tuple[str, ...] = ("usr",)
    root_symlinks: tuple[str, ...] = ()


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
        "gcc-pass2-v1": _gcc_pass2,
        "linux-headers-v1": _linux_headers,
        "glibc-cross-v1": _glibc,
        "libstdcxx-pass1-v1": _libstdcxx,
        "m4-cross-v1": _m4,
        "ncurses-cross-v1": _ncurses,
        "bash-cross-v1": _bash,
    }.get(adapter)
    if adapter in {"temporary-tool-v1", "file-cross-v1", "binutils-pass2-v1"}:
        from klinix.builder.temporary import reviewed_temporary

        factory = reviewed_temporary
    if adapter in {"gettext-temporary-v1", "bison-temporary-v1"}:
        from klinix.builder.native import reviewed_native

        factory = reviewed_native
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
                        source_root, commands, expected, bootstrap=True, root_directories=("tools",))


def _gcc_archives(book: Book, recipe: Recipe, approval: dict, source_root: str) -> tuple[ApprovedInput, ...]:
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
    return tuple(archives)


def _gcc(book: Book, recipe: Recipe, approval: dict, jobs: int) -> PackageBuild:
    package = recipe.package
    if package is None or package.name != "gcc-pass1":
        raise KlinixError("Pinned catalog lacks the reviewed GCC pass 1 package metadata")
    libc = next((item.package for item in book.recipes if item.id == "chapter05/glibc"), None)
    if libc is None or libc.name != "glibc" or not VERSION.fullmatch(libc.version):
        raise KlinixError("Pinned catalog lacks the target Glibc version for GCC pass 1")

    source_root = f"gcc-{package.version}"
    archives = _gcc_archives(book, recipe, approval, source_root)
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
    return PackageBuild(book.pin, recipe.id, package, approval["adapter"], archives,
                        source_root, commands, expected, bootstrap=True, prerequisites=(binutils,), root_directories=("tools",))


NATIVE_GCC_CHECK = r"""
test "$(/usr/bin/cc -dumpmachine)" = "$1"
case "$(/usr/bin/cc -print-sysroot)" in
    ""|/) ;;
    *) exit 1 ;;
esac
test ! -e /work
test ! -e /mnt/lfs
for name in as ld; do
    tool=$(/usr/bin/cc -print-prog-name="$name")
    case "$tool" in */*) ;; *) tool=$(command -v "$tool");; esac
    case "$(readlink -e "$tool")" in /usr/*) ;; *) exit 1;; esac
done
cat > probe.c <<'EOF'
#include <stdio.h>
int main(void) { return puts("Klinix native C OK") < 0; }
EOF
cat > probe.cc <<'EOF'
#include <iostream>
#include <stdexcept>
#include <thread>
int main() {
    int value = 0;
    std::thread worker([&] { value = 42; });
    worker.join();
    if (value != 42) return 1;
    try { throw std::runtime_error("Klinix native C++ thread and exception OK"); }
    catch (const std::runtime_error& error) { std::cout << error.what() << '\n'; }
}
EOF
/usr/bin/cc probe.c -o native-c
/usr/bin/g++ -pthread probe.cc -o native-cxx
/usr/bin/readelf -lW native-c > interpreter
grep -F "Requesting program interpreter: $2]" interpreter
./native-c > c.out
grep -Fx "Klinix native C OK" c.out
./native-cxx > cxx.out
grep -Fx "Klinix native C++ thread and exception OK" cxx.out
""".strip()


def _gcc_pass2(book: Book, recipe: Recipe, approval: dict, jobs: int) -> PackageBuild:
    package = recipe.package
    if package is None or package.name != "gcc-pass2":
        raise KlinixError("Pinned catalog lacks the reviewed GCC pass 2 package metadata")
    source_root = f"gcc-{package.version}"
    inputs = _gcc_archives(book, recipe, approval, source_root)
    commands = (
        Command("Use lib for 64-bit target libraries", (
            "/usr/bin/sed", "-i", "/m64=/s/lib64/lib/", "gcc/config/i386/t-linux64",
        )),
        Command("Create the out-of-tree GCC pass 2 build directory", ("/usr/bin/mkdir", "build")),
        Command("Configure GCC to run natively in the target", (
            "/usr/bin/bash", "-ec",
            'triplet=$1; shift; exec ../configure --build="$(../config.guess)" '
            '"--host=$triplet" "--target=$triplet" "LDFLAGS_FOR_TARGET=-L$PWD/$triplet/libgcc" "$@"',
            "klinix", TARGET_TRIPLET, "--prefix=/usr", f"--with-build-sysroot={SYSROOT}",
            "--enable-default-pie", "--enable-default-ssp", "--disable-fixincludes", "--disable-nls",
            "--disable-multilib", "--disable-libatomic", "--disable-libgomp", "--disable-libquadmath",
            "--disable-libsanitizer", "--disable-libssp", "--disable-libvtv", "--disable-libstdcxx-pch",
            "--enable-languages=c,c++",
            f"CXX_FOR_TARGET={TARGET_COMPILER} -nostdinc++", "target_configargs=gcc_cv_target_thread_file=posix",
            "CFLAGS=-O2", "CXXFLAGS=-O2", "CFLAGS_FOR_TARGET=-O2", "CXXFLAGS_FOR_TARGET=-O2",
        ), cwd="build", timeout=600),
        Command("Cross-compile the native C and C++ toolchain", (
            "/usr/bin/make", f"-j{jobs}",
        ), cwd="build", timeout=5400),
        Command("Install the native GCC toolchain", (
            "/usr/bin/make", f"DESTDIR={SYSROOT}", "install",
        ), cwd="build", timeout=900),
        Command("Provide the target cc link", ("/usr/bin/ln", "-s", "gcc", f"{SYSROOT}/usr/bin/cc")),
        Command("Prepare the target verification mount points", (
            "/usr/bin/mkdir", "-p", f"{SYSROOT}/dev", f"{SYSROOT}/tmp",
        )),
        Command(
            "Compile, link, and run native C and C++ inside the target",
            ("/usr/bin/bash", "-ec", NATIVE_GCC_CHECK, "klinix", TARGET_TRIPLET, TARGET_LOADER),
            cwd="/tmp", target_root=True,
        ),
    )
    expected = (
        "usr/bin/cc", "usr/bin/gcc", "usr/bin/g++", "usr/lib/libgcc_s.so.1", "usr/lib/libstdc++.so.6",
    )
    binutils = reviewed_build(book, "chapter06/binutils-pass2", jobs)
    return PackageBuild(book.pin, recipe.id, package, approval["adapter"], inputs, source_root,
                        commands, expected, bootstrap=True, prerequisites=(binutils,),
                        root_directories=("usr", "dev", "tmp"))


def _linux_headers(book: Book, recipe: Recipe, approval: dict, jobs: int) -> PackageBuild:
    package = recipe.package
    if package is None or package.name != "linux-headers":
        raise KlinixError("Pinned catalog lacks the reviewed Linux API headers metadata")
    source_root = f"linux-{package.version}"
    archive = _select_input(book, recipe, source_root + ".tar.xz", "source", approval["source_sha256"])
    make = ("/usr/bin/make", "ARCH=x86", "HOSTCC=/usr/bin/gcc")
    commands = (
        Command("Clean the kernel source tree", (*make, "mrproper"), timeout=300),
        Command("Generate the x86 Linux API headers", (*make, f"-j{jobs}", "headers"), timeout=900),
        Command("Remove non-header files from the generated include tree", (
            "/usr/bin/find", "usr/include", "-type", "f", "!", "-name", "*.h", "-delete",
        )),
        Command("Create the target include directory", ("/usr/bin/mkdir", "-p", f"{SYSROOT}/usr/include")),
        Command("Install the Linux API headers into the sysroot", (
            "/usr/bin/cp", "-R", "usr/include/.", f"{SYSROOT}/usr/include/",
        )),
    )
    expected = ("usr/include/linux/version.h", "usr/include/linux/types.h", "usr/include/asm/unistd_64.h")
    return PackageBuild(book.pin, recipe.id, package, approval["adapter"], (archive,),
                        source_root, commands, expected, bootstrap=True)


def _glibc(book: Book, recipe: Recipe, approval: dict, jobs: int) -> PackageBuild:
    package = recipe.package
    if package is None or package.name != "glibc":
        raise KlinixError("Pinned catalog lacks the reviewed cross-Glibc metadata")
    source_root = f"glibc-{package.version}"
    fhs_patch = "glibc-fhs-1.patch"
    fixes_patch = f"{source_root}-upstream_fixes-1.patch"
    inputs = (
        _select_input(book, recipe, source_root + ".tar.xz", "source", approval["source_sha256"]),
        _select_input(book, recipe, fhs_patch, "patch", approval["fhs_sha256"]),
        _select_input(book, recipe, fixes_patch, "patch", approval["fixes_sha256"]),
    )
    patch = ("/usr/bin/patch", "--batch", "--forward", "-Np1", "-i")
    target_bin = f"{TOOLS}/bin/{TARGET_TRIPLET}"
    loader = TARGET_LOADER
    loader_target = "../lib/ld-linux-x86-64.so.2"
    commands = (
        Command("Prepare the target library directories", (
            "/usr/bin/mkdir", "-p", f"{SYSROOT}/usr/lib", f"{SYSROOT}/lib64",
        )),
        Command("Create the merged /lib link", ("/usr/bin/ln", "-s", "usr/lib", f"{SYSROOT}/lib")),
        Command("Create the x86-64 loader link", ("/usr/bin/ln", "-s", loader_target, SYSROOT + loader)),
        Command("Create the LSB loader link", (
            "/usr/bin/ln", "-s", loader_target, f"{SYSROOT}/lib64/ld-lsb-x86-64.so.3",
        )),
        Command("Apply Glibc's FHS layout patch", (*patch, f"/inputs/{fhs_patch}")),
        Command("Apply Glibc's upstream fixes", (*patch, f"/inputs/{fixes_patch}")),
        Command("Create an out-of-tree build directory", ("/usr/bin/mkdir", "build")),
        Command("Configure Glibc for the LFS sysroot", (
            "/usr/bin/bash", "-ec",
            r'printf "%s\n" "rootsbindir=/usr/sbin" > configparms; '
            'exec ../configure --build="$(../scripts/config.guess)" "$@"',
            "klinix", "--prefix=/usr", f"--host={TARGET_TRIPLET}",
            f"--with-headers={SYSROOT}/usr/include", "--disable-nscd", "libc_cv_slibdir=/usr/lib",
            f"--enable-kernel={approval['min_kernel']}", f"CC={target_bin}-gcc", f"AR={target_bin}-ar",
            f"RANLIB={target_bin}-ranlib", "BUILD_CC=/usr/bin/gcc",
        ), cwd="build", timeout=600),
        Command("Cross-compile Glibc", ("/usr/bin/make", f"-j{jobs}"), cwd="build", timeout=3600),
        Command("Install Glibc into the sysroot", (
            "/usr/bin/make", f"DESTDIR={SYSROOT}", "install",
        ), cwd="build", timeout=900),
        Command("Set ldd to use the target's loader paths", (
            "/usr/bin/sed", "-i", "/RTLDLIST=/s@/usr@@g", f"{SYSROOT}/usr/bin/ldd",
        )),
        Command("Verify headers, startup files, linker, and the staged Glibc runtime", (
            "/usr/bin/bash", "-ec",
            r'printf "%s\n" "#include <stdio.h>" "#include <linux/version.h>" '
            "'int main(void) { return puts(\"Klinix sysroot OK\") < 0; }' > libc-probe.c; "
            '"$1-gcc" -H -Wl,--trace libc-probe.c -o libc-probe > link.trace 2>&1 || { cat link.trace; exit 1; }; '
            'cat link.trace; '
            'grep -F "$2/usr/include/stdio.h" link.trace; '
            'grep -F "$2/usr/include/linux/version.h" link.trace; '
            'for object in Scrt1.o crti.o crtn.o; do '
            'test "$(readlink -e "$("$1-gcc" -print-file-name="$object")")" = "$2/usr/lib/$object"; done; '
            '"$1-readelf" -lW libc-probe > interpreter.trace; '
            'grep -F "Requesting program interpreter: $3]" interpreter.trace; '
            '"$2$3" --inhibit-cache --library-path "$2/usr/lib" --list ./libc-probe > libraries.trace; '
            'grep -F "$2/usr/lib/libc.so.6" libraries.trace; '
            '"$2$3" --inhibit-cache --library-path "$2/usr/lib" ./libc-probe > probe.out; '
            'grep -Fx "Klinix sysroot OK" probe.out',
            "klinix", target_bin, SYSROOT, loader,
        ), cwd="build"),
    )
    required = (
        "usr/include/stdio.h", "usr/lib/libc.so.6", "usr/lib/Scrt1.o", "usr/lib/crti.o",
        "usr/lib/crtn.o", "usr/bin/ldd", loader.lstrip("/"), "lib64/ld-lsb-x86-64.so.3",
    )
    prerequisites = tuple(reviewed_build(book, name, jobs) for name in (
        "chapter05/gcc-pass1", "chapter05/linux-headers",
    ))
    return PackageBuild(
        book.pin, recipe.id, package, approval["adapter"], inputs, source_root, commands, required,
        bootstrap=True, prerequisites=prerequisites, root_directories=("usr", "lib64", "etc", "var"),
        root_symlinks=("lib",),
    )


def _target_program(path: str) -> tuple[str, ...]:
    return (SYSROOT + TARGET_LOADER, "--inhibit-cache", "--library-path", f"{SYSROOT}/usr/lib", SYSROOT + path)


def _libstdcxx(book: Book, recipe: Recipe, approval: dict, jobs: int) -> PackageBuild:
    package = recipe.package
    if package is None or package.name != "gcc-libstdc++":
        raise KlinixError("Pinned catalog lacks the reviewed target libstdc++ metadata")
    source_root = f"gcc-{package.version}"
    archive = _select_input(book, recipe, source_root + ".tar.xz", "source", approval["source_sha256"])
    include = f"/tools/{TARGET_TRIPLET}/include/c++/{package.version}"
    commands = (
        Command("Create an out-of-tree libstdc++ build directory", ("/usr/bin/mkdir", "build")),
        Command("Configure libstdc++ for the target", (
            "/usr/bin/bash", "-ec", 'exec ../libstdc++-v3/configure --build="$(../config.guess)" "$@"',
            "klinix", f"--host={TARGET_TRIPLET}", f"CC={TARGET_COMPILER}", f"CXX={TARGET_COMPILER}",
            "CXXFLAGS=-O2", "--prefix=/usr", "--disable-multilib", "--disable-nls", "--disable-libstdcxx-pch",
            f"--with-gxx-include-dir={include}",
        ), cwd="build", timeout=600),
        Command("Cross-compile libstdc++", ("/usr/bin/make", f"-j{jobs}"), cwd="build", timeout=3600),
        Command("Install target C++ headers and libraries", (
            "/usr/bin/make", f"DESTDIR={SYSROOT}", "install",
        ), cwd="build", timeout=900),
        Command("Remove libtool archives from the target", (
            "/usr/bin/rm", "-f", *(f"{SYSROOT}/usr/lib/lib{name}.la" for name in ("stdc++", "stdc++exp", "stdc++fs", "supc++")),
        )),
        Command("Compile and run a program with target libstdc++", (
            "/usr/bin/bash", "-ec",
            r'printf "%s\n" "#include <iostream>" "#include <string>" '
            "'int main() { std::cout << std::string(\"Klinix C++ runtime OK\") << std::endl; }' > cxx-probe.cc; "
            '"$1" -H cxx-probe.cc -o cxx-probe > cxx.trace 2>&1 || { cat cxx.trace; exit 1; }; '
            'grep -F "$2/iostream" cxx.trace; '
            '"$3" --inhibit-cache --library-path "$4" --list ./cxx-probe > libraries.trace; '
            'grep -F "$4/libstdc++.so.6" libraries.trace; '
            '"$3" --inhibit-cache --library-path "$4" ./cxx-probe > cxx.out; '
            'grep -Fx "Klinix C++ runtime OK" cxx.out',
            "klinix", f"{TOOLS}/bin/{TARGET_TRIPLET}-g++", SYSROOT + include,
            SYSROOT + TARGET_LOADER, f"{SYSROOT}/usr/lib",
        ), cwd="build"),
    )
    glibc = reviewed_build(book, "chapter05/glibc", jobs)
    required = (include.lstrip("/") + "/iostream", "usr/lib/libstdc++.so.6", "usr/lib/libstdc++.so")
    return PackageBuild(book.pin, recipe.id, package, approval["adapter"], (archive,), source_root,
                        commands, required, bootstrap=True, prerequisites=(glibc,))


def _m4(book: Book, recipe: Recipe, approval: dict, jobs: int) -> PackageBuild:
    package = recipe.package
    if package is None or package.name != "m4":
        raise KlinixError("Pinned catalog lacks the reviewed M4 metadata")
    source_root = f"m4-{package.version}"
    archive = _select_input(book, recipe, source_root + ".tar.xz", "source", approval["source_sha256"])
    commands = (
        Command("Create the target configure site directory", ("/usr/bin/mkdir", "-p", f"{SYSROOT}/usr/share")),
        Command("Record target POSIX spawn support for GNU configure", (
            "/usr/bin/bash", "-ec",
            r'printf "%s\n" ac_cv_func_posix_spawn_file_actions_addchdir=yes '
            'ac_cv_func_posix_spawn_file_actions_addfchdir=yes > "$1"',
            "klinix", CONFIG_SITE,
        )),
        Command("Configure GNU M4 for the target", (
            "/usr/bin/bash", "-ec", 'exec ./configure --build="$(build-aux/config.guess)" "$@"',
            "klinix", f"CONFIG_SITE={CONFIG_SITE}", f"--host={TARGET_TRIPLET}",
            f"CC={TARGET_COMPILER}", "--prefix=/usr",
        ), timeout=600),
        Command("Cross-compile GNU M4", ("/usr/bin/make", f"-j{jobs}"), timeout=1800),
        Command("Install GNU M4 into the sysroot", ("/usr/bin/make", f"DESTDIR={SYSROOT}", "install"), timeout=600),
        Command("Expand a macro with the staged GNU M4", (
            "/usr/bin/bash", "-ec",
            r"""printf '%s\n' 'changequote([,])dnl' 'define([klinix], [Klinix M4 OK])dnl' 'klinix' > probe.m4; """
            '"$@" probe.m4 > probe.out; grep -Fx "Klinix M4 OK" probe.out',
            "klinix", *_target_program("/usr/bin/m4"),
        )),
    )
    libstdcxx = reviewed_build(book, "chapter05/libstdc++", jobs)
    return PackageBuild(book.pin, recipe.id, package, approval["adapter"], (archive,), source_root,
                        commands, ("usr/bin/m4", "usr/share/config.site"), bootstrap=True,
                        prerequisites=(libstdcxx,))


def _ncurses(book: Book, recipe: Recipe, approval: dict, jobs: int) -> PackageBuild:
    package = recipe.package
    if package is None or package.name != "ncurses":
        raise KlinixError("Pinned catalog lacks the reviewed Ncurses metadata")
    source_root = f"ncurses-{package.version}"
    archive = _select_input(book, recipe, source_root + ".tar.gz", "source", approval["source_sha256"])
    commands = (
        Command("Create a native tic build directory", ("/usr/bin/mkdir", "build-host")),
        Command("Configure the build-host tic", (
            "../configure", f"--prefix={TOOLS}", "AWK=gawk",
        ), cwd="build-host", timeout=600),
        Command("Generate native tic headers", ("/usr/bin/make", "-C", "include"), cwd="build-host"),
        Command("Compile the native terminfo compiler", (
            "/usr/bin/make", f"-j{jobs}", "-C", "progs", "tic",
        ), cwd="build-host", timeout=900),
        Command("Stage native tic for use during cross-install", (
            "/usr/bin/install", "-m", "755", "progs/tic", f"{TOOLS}/bin/tic",
        ), cwd="build-host"),
        Command("Configure target Ncurses and C++ bindings", (
            "/usr/bin/bash", "-ec", 'exec ./configure --build="$(./config.guess)" "$@"',
            "klinix", f"CONFIG_SITE={CONFIG_SITE}", "--prefix=/usr", f"--host={TARGET_TRIPLET}",
            "--mandir=/usr/share/man", "--with-manpage-format=normal", "--with-shared", "--without-normal",
            "--with-cxx-shared", "--without-debug", "--without-ada", "--disable-stripping", "AWK=gawk",
        ), timeout=600),
        Command("Cross-compile Ncurses", ("/usr/bin/make", f"-j{jobs}"), timeout=1800),
        Command("Install target Ncurses and terminfo", (
            "/usr/bin/make", f"DESTDIR={SYSROOT}", "install",
        ), timeout=600),
        Command("Expose libncurses through its wide-character library", (
            "/usr/bin/ln", "-s", "libncursesw.so", f"{SYSROOT}/usr/lib/libncurses.so",
        )),
        Command("Select the wide-character Ncurses headers", (
            "/usr/bin/sed", "-i", "s/^#if.*XOPEN.*$/#if 1/", f"{SYSROOT}/usr/include/curses.h",
        )),
        Command("Read staged xterm terminfo with target Ncurses", (
            *_target_program("/usr/bin/infocmp"), "-A", f"{SYSROOT}/usr/share/terminfo", "xterm",
        )),
    )
    m4 = reviewed_build(book, "chapter06/m4", jobs)
    required = ("tools/bin/tic", "usr/bin/infocmp", "usr/include/curses.h", "usr/lib/libncurses.so")
    return PackageBuild(book.pin, recipe.id, package, approval["adapter"], (archive,), source_root,
                        commands, required, bootstrap=True, prerequisites=(m4,))


def _bash(book: Book, recipe: Recipe, approval: dict, jobs: int) -> PackageBuild:
    package = recipe.package
    if package is None or package.name != "bash":
        raise KlinixError("Pinned catalog lacks the reviewed Bash metadata")
    source_root = f"bash-{package.version}"
    archive = _select_input(book, recipe, source_root + ".tar.gz", "source", approval["source_sha256"])
    commands = (
        Command("Configure Bash for the target", (
            "/usr/bin/bash", "-ec", 'exec ./configure --build="$(sh support/config.guess)" "$@"',
            "klinix", f"CONFIG_SITE={CONFIG_SITE}", "--prefix=/usr", f"--host={TARGET_TRIPLET}",
            "--without-bash-malloc", f"--docdir=/usr/share/doc/{source_root}",
        ), timeout=600),
        Command("Cross-compile Bash", ("/usr/bin/make", f"-j{jobs}"), timeout=1800),
        Command("Install Bash into the sysroot", ("/usr/bin/make", f"DESTDIR={SYSROOT}", "install"), timeout=600),
        Command("Create the merged /bin link", ("/usr/bin/ln", "-s", "usr/bin", f"{SYSROOT}/bin")),
        Command("Provide the target /bin/sh", ("/usr/bin/ln", "-s", "bash", f"{SYSROOT}/usr/bin/sh")),
        Command("Run the target shell in POSIX mode", (
            *_target_program("/bin/sh"), "-c",
            r'test -n "$BASH_VERSION" && test -o posix && printf "%s\n" "Klinix target shell OK"',
        )),
    )
    ncurses = reviewed_build(book, "chapter06/ncurses", jobs)
    return PackageBuild(book.pin, recipe.id, package, approval["adapter"], (archive,), source_root,
                        commands, ("bin", "usr/bin/sh", "usr/bin/bash"), bootstrap=True,
                        prerequisites=(ncurses,), root_symlinks=("bin",))
