"""Reviewed Chapter 7 builds using the target's temporary toolchain."""

from dataclasses import dataclass

from klinix.builder.recipes import TARGET_SOURCE, Command, PackageBuild, _select_input, reviewed_build
from klinix.errors import KlinixError
from klinix.recipes import Book, Recipe

GETTEXT_PROGRAMS = ("msgfmt", "msgmerge", "xgettext")

GETTEXT_CHECK = r"""
printf '%s\n' 'void example(void) { gettext("Klinix"); }' > sample.c
xgettext --language=C --output=sample.pot sample.c
cat > sample.po <<'EOF'
msgid ""
msgstr ""
"Content-Type: text/plain; charset=UTF-8\n"

msgid "Klinix"
msgstr "Translated message"
EOF
msgmerge --quiet --output-file=merged.po sample.po sample.pot
msgfmt --output-file=sample.mo merged.po
grep --text --fixed-strings --quiet 'Translated message' sample.mo
printf '%s\n' 'Klinix native Gettext OK'
"""

BISON_CHECK = r"""
cat > sample.y <<'EOF'
%{
int yylex(void) { return 0; }
void yyerror(const char *message) { (void) message; }
%}
%%
input: %empty;
%%
int main(void) { return yyparse(); }
EOF
bison --output=sample.c sample.y
cc sample.c -o sample
./sample
printf '%s\n' 'Klinix native Bison OK'
"""

PERL_CHECK = r"""
test "$(perl -MDigest::SHA=sha256_hex -e 'print sha256_hex("abc")')" = \
    ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad
printf '%s\n' 'Klinix native Perl OK'
"""

ZLIB_CHECK = r"""
test ! -e /usr/lib/libz.a
cat > probe.c <<'EOF'
#include <string.h>
#include <zlib.h>

int main(void) {
    const unsigned char original[] = "Klinix";
    unsigned char compressed[128], restored[sizeof original];
    unsigned long compressed_size = sizeof compressed, restored_size = sizeof restored;
    return compress(compressed, &compressed_size, original, sizeof original) != Z_OK
        || uncompress(restored, &restored_size, compressed, compressed_size) != Z_OK
        || restored_size != sizeof original
        || memcmp(original, restored, sizeof original) != 0;
}
EOF
cc probe.c -lz -o probe
./probe
printf '%s\n' 'Klinix native Zlib OK'
"""

MPDECIMAL_CHECK = r"""
cat > probe.c <<'EOF'
#include <string.h>
#include <mpdecimal.h>

int main(void) {
    mpd_context_t context;
    uint32_t status = 0;
    mpd_defaultcontext(&context);
    mpd_t *left = mpd_qnew(), *right = mpd_qnew(), *sum = mpd_qnew();
    if (!left || !right || !sum) return 1;
    mpd_qset_string(left, "0.1", &context, &status);
    mpd_qset_string(right, "0.2", &context, &status);
    mpd_qadd(sum, left, right, &context, &status);
    char *result = mpd_to_sci(sum, 1);
    int failed = status != 0 || !result || strcmp(result, "0.3") != 0;
    mpd_free(result);
    mpd_del(sum);
    mpd_del(right);
    mpd_del(left);
    return failed;
}
EOF
cc probe.c -lmpdec -o probe
./probe
printf '%s\n' 'Klinix native Mpdecimal OK'
"""

PYTHON_CHECK = r"""
python3 -I -B - <<'EOF'
import _decimal
import zlib

assert _decimal.Decimal("0.1") + _decimal.Decimal("0.2") == _decimal.Decimal("0.3")
assert zlib.decompress(zlib.compress(b"Klinix")) == b"Klinix"
EOF
decimal_module=$(python3 -I -B -c 'import _decimal; print(_decimal.__file__)')
zlib_module=$(python3 -I -B -c 'import zlib; print(zlib.__file__)')
readelf -d "$decimal_module" > decimal.elf
readelf -d "$zlib_module" > zlib.elf
grep -E 'NEEDED.*\[libmpdec\.so' decimal.elf
grep -E 'NEEDED.*\[libz\.so' zlib.elf
printf '%s\n' 'Klinix native Python OK'
"""


@dataclass(frozen=True)
class NativeTool:
    name: str
    prerequisite: str
    configure: tuple[str, ...]
    required: tuple[str, ...]
    check: str
    suffix: str = ".tar.xz"
    configure_program: tuple[str, ...] = ("./configure",)
    install: tuple[tuple[str, ...], ...] = (("/usr/bin/make", "install"),)


NATIVE_TOOLS = {
    "gettext-temporary-v1": NativeTool(
        "gettext", "chapter06/gcc-pass2", ("--disable-shared", "CFLAGS=-O2"),
        tuple(f"usr/bin/{name}" for name in GETTEXT_PROGRAMS), GETTEXT_CHECK,
        install=(("/usr/bin/cp", "-v", *(f"gettext-tools/src/{name}" for name in GETTEXT_PROGRAMS), "/usr/bin"),),
    ),
    "bison-temporary-v1": NativeTool(
        "bison", "chapter07/gettext", ("--prefix=/usr", "--docdir=/usr/share/doc/{source}", "CFLAGS=-O2"),
        ("usr/bin/bison", "usr/bin/yacc", "usr/share/bison/skeletons/yacc.c"), BISON_CHECK,
    ),
    "perl-temporary-v1": NativeTool(
        "perl", "chapter07/bison",
        ("-des", "-D", "prefix=/usr", "-D", "vendorprefix=/usr", "-D", "useshrplib", "-D", "optimize=-O2",
         "-D", "privlib=/usr/lib/perl5/{series}/core_perl", "-D", "archlib=/usr/lib/perl5/{series}/core_perl",
         "-D", "sitelib=/usr/lib/perl5/{series}/site_perl", "-D", "sitearch=/usr/lib/perl5/{series}/site_perl",
         "-D", "vendorlib=/usr/lib/perl5/{series}/vendor_perl", "-D", "vendorarch=/usr/lib/perl5/{series}/vendor_perl"),
        ("usr/bin/perl", "usr/lib/perl5/{series}/core_perl/CORE/libperl.so"), PERL_CHECK,
        configure_program=("/usr/bin/sh", "Configure"),
    ),
    "zlib-temporary-v1": NativeTool(
        "zlib", "chapter07/perl", ("--prefix=/usr",), ("usr/include/zlib.h", "usr/lib/libz.so"), ZLIB_CHECK,
        suffix=".tar.gz", install=(("/usr/bin/make", "install"), ("/usr/bin/rm", "-f", "/usr/lib/libz.a")),
    ),
    "mpdecimal-temporary-v1": NativeTool(
        "mpdecimal", "chapter07/zlib",
        ("--prefix=/usr", "--disable-static", "--docdir=/usr/share/doc/{source}", "CFLAGS=-O2"),
        ("usr/include/mpdecimal.h", "usr/lib/libmpdec.so"), MPDECIMAL_CHECK, suffix=".tar.gz",
    ),
    "python-temporary-v1": NativeTool(
        "Python", "chapter07/mpdecimal",
        ("--prefix=/usr", "--enable-shared", "--without-ensurepip", "--without-static-libpython", "CFLAGS=-O2"),
        ("usr/bin/python3", "usr/lib/libpython{series}.so", "usr/lib/python{series}/decimal.py"), PYTHON_CHECK,
    ),
}


def reviewed_native(book: Book, recipe: Recipe, approval: dict, jobs: int) -> PackageBuild:
    package = recipe.package
    adapter = approval["adapter"]
    tool = NATIVE_TOOLS[adapter]
    if package is None or package.name != tool.name.lower():
        raise KlinixError(f"Pinned catalog lacks the reviewed {tool.name} package metadata")
    source_root = f"{tool.name}-{package.version}"
    source = _select_input(book, recipe, source_root + tool.suffix, "source", approval["source_sha256"])
    series = ".".join(package.version.split(".")[:2])
    configure = tuple(arg.format(source=source_root, series=series) for arg in tool.configure)
    required = tuple(path.format(series=series) for path in tool.required)
    commands = (
        Command(f"Configure temporary {tool.name}", (*tool.configure_program, *configure),
                cwd=TARGET_SOURCE, timeout=600, target_root=True, target_writable=True),
        Command(f"Build temporary {tool.name}", ("/usr/bin/make", f"-j{jobs}"),
                cwd=TARGET_SOURCE, timeout=3600, target_root=True, target_writable=True),
        *(Command(f"Install temporary {tool.name}", argv,
                  cwd=TARGET_SOURCE, timeout=600, target_root=True, target_writable=True) for argv in tool.install),
        Command(f"Verify temporary {tool.name} in the target", ("/usr/bin/bash", "-ec", tool.check),
                cwd="/tmp", target_root=True),
    )
    return PackageBuild(book.pin, recipe.id, package, adapter, (source,), source_root, commands, required,
                        bootstrap=True, prerequisites=(reviewed_build(book, tool.prerequisite, jobs),))
