"""Command-line interface for the headless Klinix backend."""

import argparse
import json
import logging
from dataclasses import asdict
from importlib.metadata import version
from pathlib import Path

from klinix.books.lfs import LfsProvider
from klinix.builder import ENVIRONMENT, preview, run_build
from klinix.builder.recipes import reviewed_build
from klinix.builder.sandbox import Bubblewrap
from klinix.catalog import Catalog
from klinix.config import Settings
from klinix.dependencies import Policy
from klinix.errors import KlinixError
from klinix.planner import plan
from klinix.recipes import REVISION
from klinix.sources import select_artifact, verify_file

LOGGER = logging.getLogger("klinix")


def _revision(value: str) -> str:
    if not REVISION.fullmatch(value):
        raise argparse.ArgumentTypeError("use a full lowercase Git object ID")
    return value


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="klinix", description="Advanced Automated Linux From Scratch")
    parser.add_argument("--version", action="version", version="%(prog)s " + version("klinix"))
    parser.add_argument("--config", type=Path, help="TOML settings file")
    parser.add_argument("--db", type=Path, help="override the SQLite catalog path")
    parser.add_argument("-v", "--verbose", action="store_true", help="emit progress to stderr")
    commands = parser.add_subparsers(dest="command", required=True)

    books = commands.add_parser("books", help="discover and import LFS books")
    actions = books.add_subparsers(dest="book_action", required=True)
    actions.add_parser("discover", help="discover official LFS systemd releases through Git")
    actions.add_parser("list", help="list revisions stored in the catalog")
    importer = actions.add_parser("import", help="import HEAD of a local LFS Git checkout")
    importer.add_argument("source", type=Path)
    importer.add_argument("--expect-revision", type=_revision)

    check = commands.add_parser("build-check", help="check the host toolchain and Bubblewrap isolation")
    check.add_argument("--bootstrap", action="store_true", help="check cross-toolchain bootstrap prerequisites")
    builder = commands.add_parser("build-package", help="build a reviewed package and its stages into a new directory")
    builder.add_argument("recipe", help="exact reviewed chapter/section ID")
    builder.add_argument("--revision", type=_revision, required=True, help="full imported Git object ID")
    builder.add_argument("--sources", type=Path, help="directory of local source archives and patches")
    builder.add_argument("--target", type=Path, help="new output directory under an existing parent")
    builder.add_argument("--jobs", type=int, default=1, help="parallel build jobs, between 1 and 64")
    builder.add_argument("--dry-run", action="store_true", help="show reviewed commands without running them")

    for command, help_text in (
        ("packages", "list available recipes and package versions"),
        ("sources", "list source and patch verification metadata"),
        ("plan", "show a dry-run build plan"),
        ("verify-source", "verify a local archive or patch against the pinned catalog"),
    ):
        sub = commands.add_parser(command, help=help_text)
        sub.add_argument("--revision", type=_revision, required=True, help="full imported Git object ID")
        if command == "verify-source":
            sub.add_argument("file", type=Path)
            sub.add_argument("--url", help="exact catalog URL, useful for renamed files")
        if command == "plan":
            sub.add_argument("selection", nargs="*", help="package names or chapter/section IDs")
            sub.add_argument("--recommended", action=argparse.BooleanOptionalAction, default=None)
            sub.add_argument("--optional", action=argparse.BooleanOptionalAction, default=None)
    return parser


def _run(args: argparse.Namespace) -> dict | list:
    if args.command == "books" and args.book_action == "discover":
        return [asdict(release) for release in LfsProvider().discover()]
    if args.command == "build-check":
        Bubblewrap(bootstrap=args.bootstrap).probe()
        return {"ready": True, "environment": ENVIRONMENT, "sandbox": "bubblewrap"}

    settings = Settings.load(args.config)
    catalog = Catalog(args.db.expanduser() if args.db else settings.database)
    if args.command == "books":
        if args.book_action == "list":
            return [asdict(pin) for pin in catalog.pins()]
        LOGGER.info("Reading committed book files from %s", args.source)
        book = LfsProvider().import_local(args.source, args.expect_revision)
        added = catalog.add(book)
        return {
            "book": asdict(book.pin),
            "recipes": len(book.recipes),
            "packages": len({recipe.package for recipe in book.recipes if recipe.package}),
            "materials": len(book.artifacts),
            "added": added,
        }
    book = catalog.load(args.revision)
    if args.command == "build-package":
        build = reviewed_build(book, args.recipe, args.jobs)
        if args.dry_run:
            return {"mode": "dry-run", **preview(build)}
        if args.sources is None or args.target is None:
            raise KlinixError("build-package requires --sources and --target for execution")
        return run_build(build, args.sources.expanduser(), args.target)
    if args.command == "packages":
        return [
            {"recipe": recipe.id, "chapter": recipe.id.split("/")[0],
             "phase": recipe.phase, **asdict(recipe.package)}
            for recipe in book.recipes if recipe.package
        ]
    if args.command == "sources":
        return [asdict(artifact) for artifact in book.artifacts]
    if args.command == "verify-source":
        artifact = select_artifact(book, args.file, args.url)
        size = verify_file(args.file, artifact)
        return {"book": asdict(book.pin), "material": asdict(artifact), "bytes": size, "matched": True}
    policy = Policy(
        settings.recommended if args.recommended is None else args.recommended,
        settings.optional if args.optional is None else args.optional,
    )
    return plan(book, tuple(args.selection), policy)


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING, format="%(levelname)s: %(message)s")
    try:
        print(json.dumps(_run(args), indent=2))
    except KlinixError as exc:
        LOGGER.error("%s", exc)
        return 1
    except KeyboardInterrupt as exc:
        LOGGER.error("%s", str(exc) or "Interrupted")
        return 130
    return 0
