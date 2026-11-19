"""Reviewed Chapter 7 builds using the target's temporary toolchain."""

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


def reviewed_native(book: Book, recipe: Recipe, approval: dict, jobs: int) -> PackageBuild:
    package = recipe.package
    adapter = approval["adapter"]
    expected_name = "gettext" if adapter == "gettext-temporary-v1" else "bison"
    if package is None or package.name != expected_name:
        raise KlinixError(f"Pinned catalog lacks the reviewed {expected_name} package metadata")
    source_root = f"{expected_name}-{package.version}"
    source = _select_input(book, recipe, f"{source_root}.tar.xz", "source", approval["source_sha256"])

    if expected_name == "gettext":
        prerequisite = "chapter06/gcc-pass2"
        configure = ("--disable-shared",)
        install = ("/usr/bin/cp", "-v", *(f"gettext-tools/src/{name}" for name in GETTEXT_PROGRAMS), "/usr/bin")
        required = tuple(f"usr/bin/{name}" for name in GETTEXT_PROGRAMS)
        check = GETTEXT_CHECK
    else:
        prerequisite = "chapter07/gettext"
        configure = ("--prefix=/usr", f"--docdir=/usr/share/doc/{source_root}")
        install = ("/usr/bin/make", "install")
        required = ("usr/bin/bison", "usr/bin/yacc", "usr/share/bison/skeletons/yacc.c")
        check = BISON_CHECK

    commands = (
        Command(f"Configure temporary {expected_name}", ("./configure", *configure, "CFLAGS=-O2"),
                cwd=TARGET_SOURCE, timeout=600, target_root=True, target_writable=True),
        Command(f"Build temporary {expected_name}", ("/usr/bin/make", f"-j{jobs}"),
                cwd=TARGET_SOURCE, timeout=1800, target_root=True, target_writable=True),
        Command(f"Install temporary {expected_name}", install,
                cwd=TARGET_SOURCE, timeout=300, target_root=True, target_writable=True),
        Command(f"Verify temporary {expected_name} in the target", ("/usr/bin/bash", "-ec", check),
                cwd="/tmp", target_root=True),
    )
    return PackageBuild(book.pin, recipe.id, package, adapter, (source,), source_root, commands, required,
                        bootstrap=True, prerequisites=(reviewed_build(book, prerequisite, jobs),))
