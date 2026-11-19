"""Reviewed Chapter 6 adapters that install user-space tools into the sysroot."""

from dataclasses import dataclass

from klinix.builder.recipes import (
    CONFIG_SITE,
    SYSROOT,
    TARGET_COMPILER,
    TARGET_LOADER,
    TARGET_TRIPLET,
    Command,
    PackageBuild,
    _select_input,
    _target_program,
    reviewed_build,
)
from klinix.errors import KlinixError
from klinix.recipes import Book, Recipe


def _check(program: str, script: str) -> Command:
    return Command(
        f"Exercise target {program}",
        ("/usr/bin/bash", "-ec", script, "klinix", *_target_program(f"/usr/bin/{program}")),
    )


def _compression_check(program: str) -> Command:
    return _check(program, (
        r'printf "%s\n" "Klinix compression probe" > input.txt; '
        '"$@" -c input.txt > compressed; "$@" -dc compressed > restored.txt; '
        'cmp input.txt restored.txt'
    ))


@dataclass(frozen=True)
class Tool:
    probe: Command
    required: tuple[str, ...]
    archive: str = ".tar.xz"
    guess: str = "build-aux/config.guess"
    flags: tuple[str, ...] = ()
    before: tuple[Command, ...] = ()
    after: tuple[Command, ...] = ()
    root_symlinks: tuple[str, ...] = ()


TOOLS = {
    "coreutils": Tool(
        _check("sort", r"""printf '%s\n' b a b > input.txt; "$@" -u input.txt > sorted; test "$(cat sorted)" = $'a\nb'"""),
        ("usr/bin/sort", "usr/bin/hostname", "usr/sbin/chroot", "usr/share/man/man8/chroot.8", "sbin"),
        flags=("--enable-install-program=hostname",),
        after=(
            Command("Prepare Coreutils' system binary and manual directories", (
                "/usr/bin/mkdir", "-p", f"{SYSROOT}/usr/sbin", f"{SYSROOT}/usr/share/man/man8",
            )),
            Command("Move chroot to its system binary location", (
                "/usr/bin/mv", f"{SYSROOT}/usr/bin/chroot", f"{SYSROOT}/usr/sbin/chroot",
            )),
            Command("Move the chroot manual to section 8", (
                "/usr/bin/mv", f"{SYSROOT}/usr/share/man/man1/chroot.1", f"{SYSROOT}/usr/share/man/man8/chroot.8",
            )),
            Command("Update the chroot manual section", (
                "/usr/bin/sed", "-i", 's/"1"/"8"/', f"{SYSROOT}/usr/share/man/man8/chroot.8",
            )),
            Command("Create the target /sbin link", ("/usr/bin/ln", "-s", "usr/sbin", f"{SYSROOT}/sbin")),
        ),
        root_symlinks=("sbin",),
    ),
    "diffutils": Tool(
        _check("diff", (
            r'printf "%s\n" before > before.txt; printf "%s\n" after > after.txt; '
            '"$@" before.txt before.txt; '
            'status=0; "$@" before.txt after.txt > difference || status=$?; '
            'test "$status" = 1; grep -Fx "< before" difference; grep -Fx "> after" difference'
        )),
        ("usr/bin/diff", "usr/bin/cmp"), flags=("gl_cv_func_strcasecmp_works=yes",),
    ),
    "file": Tool(
        _check("file", (
            '"$@" -b -m /mnt/lfs/usr/share/misc/magic.mgc /mnt/lfs/usr/bin/bash > classification; '
            'grep -F "ELF 64-bit" classification'
        )),
        ("usr/bin/file", "usr/lib/libmagic.so", "usr/share/misc/magic.mgc"),
        archive=".tar.gz", guess="config.guess",
        after=(Command("Remove the libmagic libtool archive", (
            "/usr/bin/rm", f"{SYSROOT}/usr/lib/libmagic.la",
        )),),
    ),
    "findutils": Tool(
        _check("find", (
            'mkdir -p probe/nested; touch probe/nested/example; '
            'test "$("$@" probe -type f -name example -print)" = "probe/nested/example"'
        )),
        ("usr/bin/find", "usr/bin/xargs"), flags=("--localstatedir=/var/lib/locate",),
    ),
    "gawk": Tool(
        _check("gawk", (
            r"""printf '%s\n' '1 2' '3 4' > input.txt; """
            """test "$("$@" '{ sum += $1 + $2 } END { print sum }' input.txt)" = 10"""
        )),
        ("usr/bin/gawk",),
        before=(Command("Exclude the Gawk extras directory", (
            "/usr/bin/sed", "-i", "s/extras//", "Makefile.in",
        )),),
    ),
    "grep": Tool(
        _check("grep", r"""printf '%s\n' prefix exact 'exact suffix' > input.txt; test "$("$@" -Fx exact input.txt)" = exact"""),
        ("usr/bin/grep",),
    ),
    "gzip": Tool(_compression_check("gzip"), ("usr/bin/gzip",), guess=""),
    "make": Tool(
        _check("make", (
            r"""printf '%s\n' '$(info Klinix make probe)' 'all:;' > probe.mk; """
            '"$@" -f probe.mk > output; grep -Fx "Klinix make probe" output'
        )),
        ("usr/bin/make",), archive=".tar.gz",
    ),
    "patch": Tool(
        _check("patch", (
            r"""printf '%s\n' before > probe.txt; """
            r"""printf '%s\n' '--- probe.txt' '+++ probe.txt' '@@ -1 +1 @@' '-before' '+after' > probe.diff; """
            '"$@" --batch --forward -i probe.diff; test "$(cat probe.txt)" = after'
        )),
        ("usr/bin/patch",),
    ),
    "sed": Tool(
        _check("sed", r"""printf '%s\n' before > input.txt; test "$("$@" 's/before/after/' input.txt)" = after"""),
        ("usr/bin/sed",),
    ),
    "tar": Tool(
        _check("tar", (
            r'printf "%s\n" "Klinix tar probe" > input.txt; mkdir extracted; '
            '"$@" -cf probe.tar input.txt; "$@" -xf probe.tar -C extracted; cmp input.txt extracted/input.txt'
        )),
        ("usr/bin/tar",),
    ),
    "xz": Tool(
        _compression_check("xz"), ("usr/bin/xz", "usr/lib/liblzma.so"), flags=("--disable-static",),
        after=(Command("Remove the liblzma libtool archive", ("/usr/bin/rm", f"{SYSROOT}/usr/lib/liblzma.la")),),
    ),
}

# Chapter 6's reviewed execution order after the temporary shell.
ORDER = ("bash", *TOOLS, "binutils-pass2")


def _configure(recipe: str, tool: Tool, source_root: str) -> Command:
    flags = tool.flags
    if recipe == "xz":
        flags += (f"--docdir=/usr/share/doc/{source_root}",)
    options = (f"CONFIG_SITE={CONFIG_SITE}", "--prefix=/usr", f"--host={TARGET_TRIPLET}", f"CC={TARGET_COMPILER}")
    if tool.guess:
        # The config.guess path comes only from the reviewed table above.
        script = f'exec ./configure --build="$(./{tool.guess})" "$@"'
        argv = ("/usr/bin/bash", "-ec", script, "klinix", *options, *flags)
    else:
        argv = ("./configure", *options, *flags)
    return Command(f"Configure target {recipe}", argv, timeout=600)


def _file_commands(source_root: str, jobs: int) -> tuple[tuple[Command, ...], tuple[str, ...]]:
    native = "build-host"
    commands = (
        Command("Create the build-host File directory", ("/usr/bin/mkdir", native)),
        Command("Configure a native magic database compiler", (
            "../configure", "--disable-bzlib", "--disable-libseccomp", "--disable-xzlib", "--disable-zlib",
        ), cwd=native, timeout=600),
        Command("Compile the build-host File program", ("/usr/bin/make", f"-j{jobs}"), cwd=native, timeout=1500),
    )
    return commands, (f"FILE_COMPILE=/work/source/{source_root}/{native}/src/file",)


def _binutils_commands(jobs: int) -> tuple[Command, ...]:
    return (
        Command("Remove the host library search adjustment from libtool", (
            "/usr/bin/sed", "-i", "6031s/$add_dir//", "ltmain.sh",
        )),
        Command("Create an out-of-tree Binutils build directory", ("/usr/bin/mkdir", "build")),
        Command("Configure the target-native Binutils", (
            "/usr/bin/bash", "-ec", 'exec ../configure --build="$(../config.guess)" "$@"',
            "klinix", "--prefix=/usr", f"--host={TARGET_TRIPLET}", "--disable-nls", "--enable-shared",
            "--enable-gprofng=no", "--disable-werror", "--enable-64-bit-bfd", "--enable-new-dtags",
            "--enable-default-hash-style=gnu",
        ), cwd="build", timeout=600),
        Command("Cross-compile target-native Binutils", ("/usr/bin/make", f"-j{jobs}"), cwd="build", timeout=2100),
        Command("Install target-native Binutils", ("/usr/bin/make", f"DESTDIR={SYSROOT}", "install"),
                cwd="build", timeout=900),
        Command("Remove temporary Binutils static and libtool archives", (
            "/usr/bin/rm", "-f", *(
                f"{SYSROOT}/usr/lib/lib{name}.{extension}"
                for name in ("bfd", "ctf", "ctf-nobfd", "opcodes", "sframe")
                for extension in ("a", "la")
            ),
        )),
        Command("Assemble, link, and inspect an ELF with target-native Binutils", (
            "/usr/bin/bash", "-ec",
            r'printf "%s\n" ".text" ".globl _start" "_start: nop" > probe.s; '
            '"$1" --inhibit-cache --library-path "$2" "$3/as" -o probe.o probe.s; '
            '"$1" --inhibit-cache --library-path "$2" "$3/ld" -e _start -o probe probe.o; '
            '"$1" --inhibit-cache --library-path "$2" "$3/readelf" -h probe > header; '
            'grep -F "Advanced Micro Devices X86-64" header',
            "klinix", SYSROOT + TARGET_LOADER, f"{SYSROOT}/usr/lib", f"{SYSROOT}/usr/bin",
        ), cwd="build"),
    )


def reviewed_temporary(book: Book, recipe: Recipe, approval: dict, jobs: int) -> PackageBuild:
    name = recipe.id.removeprefix("chapter06/")
    package = recipe.package
    if name not in ORDER[1:] or recipe.id != f"chapter06/{name}" or package is None or package.name != name:
        raise KlinixError("Pinned catalog lacks the reviewed temporary package metadata")
    previous = reviewed_build(book, f"chapter06/{ORDER[ORDER.index(name) - 1]}", jobs)
    if name == "binutils-pass2":
        source_root = f"binutils-{package.version}"
        archive = _select_input(book, recipe, source_root + ".tar.xz", "source", approval["source_sha256"])
        return PackageBuild(
            book.pin, recipe.id, package, approval["adapter"], (archive,), source_root,
            _binutils_commands(jobs), ("usr/bin/as", "usr/bin/ld", "usr/bin/readelf"),
            bootstrap=True, prerequisites=(previous,),
        )

    tool = TOOLS[name]
    source_root = f"{name}-{package.version}"
    archive = _select_input(book, recipe, source_root + tool.archive, "source", approval["source_sha256"])
    before, make_options = _file_commands(source_root, jobs) if name == "file" else (tool.before, ())
    commands = (
        *before,
        _configure(name, tool, source_root),
        Command(f"Cross-compile {name}", ("/usr/bin/make", f"-j{jobs}", *make_options), timeout=1800),
        Command(f"Install target {name}", ("/usr/bin/make", f"DESTDIR={SYSROOT}", "install"), timeout=900),
        *tool.after,
        tool.probe,
    )
    return PackageBuild(book.pin, recipe.id, package, approval["adapter"], (archive,), source_root, commands,
                        tool.required, bootstrap=True, prerequisites=(previous,), root_symlinks=tool.root_symlinks)
